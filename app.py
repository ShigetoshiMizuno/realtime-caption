"""
Realtime Caption & Translation — GUI アプリ
dearpygui を使ったフロントエンド。CaptionSystem をサブスレッドで動かし、
queue 経由で GUI を安全に更新する。
"""

import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path as _Path

# Embeddable Python の python311._pth は sys.path を完全上書きするため、
# プロジェクトルート（このファイルのディレクトリ）を明示的に追加する。
# また config.yaml を相対パスで開けるよう CWD もスクリプトのディレクトリに固定する。
_SCRIPT_DIR = _Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))
os.chdir(_SCRIPT_DIR)


def _to_short_path(path: _Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    buf = ctypes.create_unicode_buffer(1024)
    r = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    return buf.value if r > 0 else str(path)


def _ensure_ascii_path(path: _Path) -> tuple[_Path, str | None]:
    path.mkdir(parents=True, exist_ok=True)
    path_str = str(path)
    if all(ord(c) < 128 for c in path_str):
        return path, None
    short = _to_short_path(path)
    if short != path_str and all(ord(c) < 128 for c in short):
        return _Path(short), None
    for letter in "RSTUVWXYZ":
        if not _Path(f"{letter}:\\").exists():
            r = subprocess.run(["subst", f"{letter}:", path_str], capture_output=True)
            if r.returncode == 0:
                return _Path(f"{letter}:\\"), letter
    return path, None


def _release_subst(letter: str | None):
    if letter:
        subprocess.run(["subst", f"{letter}:", "/d"], capture_output=True)


# モデルキャッシュ先: プロジェクトディレクトリが ASCII なら ./models、
# 非 ASCII なら %LOCALAPPDATA%\rc-models にフォールバック（torch.jit の fopen 制約を回避）
_project_models = _Path(__file__).parent.resolve() / "models"
if all(ord(c) < 128 for c in str(_project_models)):
    _MODEL_BASE = _project_models
else:
    _localappdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    _MODEL_BASE = _Path(_localappdata) / "rc-models"
    print(f"[app] プロジェクトパスに非ASCII文字を含むため、モデルキャッシュを {_MODEL_BASE} に配置します", flush=True)

_ascii_models, _subst_letter = _ensure_ascii_path(_MODEL_BASE)
os.environ.setdefault("HF_HOME", str(_ascii_models / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(_ascii_models / "torch"))
# main.py の重複セットアップ/重複ログを抑制するマーカー
os.environ["RC_MODELS_CONFIGURED"] = "1"

import argparse
import asyncio
import io
import json
import queue
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import dearpygui.dearpygui as dpg

from main import (
    CaptionSystem, MultiCaptionSystem, RouteConfig, RouteState,
    list_audio_devices, find_device_by_name, load_config,
    _open_quota_usage_page,
)
from config_utils import decode_api_key, encode_api_key
from constants import get_language_display_names, get_language_codes
from ptt_hotkey_manager import PttHotkeyManager

# Windows コンソールの文字化け対策
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# グローバル状態
# ---------------------------------------------------------------------------

_config: dict = {}
_devices: list[dict] = []
_gui_queue: queue.Queue = queue.Queue()
_log_entries: list[dict] = []  # {"ts": str, "original": str, "translated": str}
_system: CaptionSystem | None = None
_system_thread: threading.Thread | None = None
_is_running = False
_rpc_server: HTTPServer | None = None

# 翻訳こんにゃくモード用
_konnyaku_system: MultiCaptionSystem | None = None
_konnyaku_running: bool = False

# PTT (Push-to-Talk) モード用 (issue #82 / ptt-mode-design.md)
_ptt_manager: PttHotkeyManager | None = None
_ptt_enabled: bool = False
_ptt_hotkey: str = "f8"

# プリロードキャッシュ
_preloaded_system: CaptionSystem | None = None
_preload_key: tuple | None = None   # (model_name, device_index)
_preload_lock = threading.Lock()

# ロード進捗
_loading_active: bool = False
_loading_start_time: float = 0.0
_loading_phase: str = ""          # 表示用テキスト
_loading_model_name: str = ""     # small / medium
# 期待ファイルサイズ（MB単位、ダウンロード進捗推定用）
_MODEL_EXPECTED_MB = {"small": 500, "medium": 1500}

# GUI タグ
TAG_DEVICE_COMBO = "device_combo"
TAG_MODEL_COMBO = "model_combo"
TAG_START_BTN = "start_btn"
TAG_TRANS_COMBO = "trans_combo"
TAG_VAD_SENSITIVITY = "vad_sensitivity"
TAG_VAD_SILENCE = "vad_silence"
TAG_GAIN_MODE = "gain_mode"
TAG_GAIN_SLIDER = "gain_slider"
TAG_GAIN_LABEL = "gain_label"
TAG_PROGRESS_BAR = "progress_bar"
TAG_PROGRESS_TEXT = "progress_text"
TAG_LEVEL_METER = "level_meter"
TAG_LEVEL_THEME_GREEN = "level_theme_green"
TAG_LEVEL_THEME_YELLOW = "level_theme_yellow"
TAG_LEVEL_THEME_RED = "level_theme_red"
TAG_VERBOSE_BTN = "verbose_btn"
TAG_LOG_GROUP = "log_group"
TAG_LOG_SCROLL = "log_scroll"
TAG_STATUS_DEVICE = "status_device"
TAG_STATUS_WS = "status_ws"
TAG_STATUS_RPC = "status_rpc"
TAG_STATUS_STATE = "status_state"
TAG_STATUS_STT = "status_stt"
TAG_STATUS_TRL = "status_trl"
TAG_OUTPUT_DEVICE_COMBO = "output_device_combo"
TAG_ZOOM_PRESET_BTN = "zoom_preset_btn"
TAG_STATUS_COST = "status_cost"
TAG_HOST_API_COMBO = "host_api_combo"

# ---------------------------------------------------------------------------
# 翻訳こんにゃくモード GUI タグ (Issue #38 Phase 4)
# ---------------------------------------------------------------------------

# こんにゃくモードプリセットボタン
TAG_KONNYAKU_PRESET_BTN = "konnyaku_preset_btn"

# 経路A レベルメーター（入力・出力）
TAG_LEVEL_METER_A_IN = "level_meter_a_in"
TAG_LEVEL_METER_A_OUT = "level_meter_a_out"

# 経路B レベルメーター（入力・出力）
TAG_LEVEL_METER_B_IN = "level_meter_b_in"
TAG_LEVEL_METER_B_OUT = "level_meter_b_out"

# 経路A 設定タグ
TAG_ROUTE_A_DEVICE_COMBO = "route_a_device_combo"
TAG_ROUTE_A_GAIN_MODE = "route_a_gain_mode"
TAG_ROUTE_A_GAIN_SLIDER = "route_a_gain_slider"
TAG_ROUTE_A_LANG_COMBO = "route_a_lang_combo"
TAG_ROUTE_A_OUTPUT_ENABLE = "route_a_output_enable"
TAG_ROUTE_A_OUTPUT_DEVICE_COMBO = "route_a_output_device_combo"
TAG_ROUTE_A_OUTPUT_VOLUME = "route_a_output_volume"

# 経路B 設定タグ
TAG_ROUTE_B_DEVICE_COMBO = "route_b_device_combo"
TAG_ROUTE_B_GAIN_MODE = "route_b_gain_mode"
TAG_ROUTE_B_GAIN_SLIDER = "route_b_gain_slider"
TAG_ROUTE_B_LANG_COMBO = "route_b_lang_combo"
TAG_ROUTE_B_OUTPUT_ENABLE = "route_b_output_enable"
TAG_ROUTE_B_OUTPUT_DEVICE_COMBO = "route_b_output_device_combo"
TAG_ROUTE_B_OUTPUT_VOLUME = "route_b_output_volume"

# 経路 ON/OFF トグル（Issue #43）
TAG_ROUTE_A_ENABLE = "route_a_enable"
TAG_ROUTE_B_ENABLE = "route_b_enable"

# こんにゃくモード コンテナ
TAG_KONNYAKU_SECTION = "konnyaku_section"
TAG_KONNYAKU_START_BTN = "konnyaku_start_btn"

VAD_DEFAULT_SENSITIVITY = 0.4
# 0.6 秒: 自然な息継ぎ程度の沈黙では文を切らず、文末の本格的な無音で確定する。
# 短すぎる（0.2 等）と長文の途中で分断され、Whisper が冒頭/末尾を取りこぼしやすい。
VAD_DEFAULT_SILENCE = 0.6
GAIN_DEFAULT_MODE = "off"
GAIN_DEFAULT_VALUE = 1.0

_SETTINGS_PATH = _SCRIPT_DIR / "settings.json"
CONFIG_PATH = _SCRIPT_DIR / "config.yaml"

# 動的可視性制御用グループタグ
TAG_WHISPER_SETTINGS_GROUP = "whisper_settings_group"
TAG_REALTIME_SETTINGS_GROUP = "realtime_settings_group"
TAG_DEEPL_KEY_GROUP = "deepl_key_group"

# API キー UI タグ
TAG_OPENAI_KEY_INPUT = "openai_key_input"
TAG_DEEPL_KEY_INPUT = "deepl_key_input"
TAG_KEY_SHOW_OPENAI = "key_show_openai"
TAG_KEY_SHOW_DEEPL = "key_show_deepl"
TAG_KEY_SAVE_BTN = "key_save_btn"
TAG_KEY_STATUS = "key_status"
TAG_QUOTA_WEB_BTN = "quota_web_btn"  # issue #80: クォータ確認ボタン

# PTT 設定 UI タグ (issue #82 / ptt-mode-design.md F-5)
TAG_PTT_SECTION = "ptt_section"
TAG_PTT_ENABLED = "ptt_enabled_checkbox"
TAG_PTT_HOTKEY = "ptt_hotkey_input"

# PTT 視覚フィードバック用タグ (ptt-mode-design.md F-6)
TAG_ROUTE_B_LABEL = "route_b_label"       # 系統2 見出しテキスト（ラベル動的切替用）
TAG_PTT_STATUS_LABEL = "ptt_status_label"  # 押下中ステータス表示ラベル

# Verbose ロギング状態（settings.json で永続化）
_verbose_state: bool = False

# コスト警告: スレッドセーフなフラグ（メインスレッドの描画ループで検査）
_pending_cost_warnings: list[float] = []
_cost_warning_lock = threading.Lock()

# dpg コンテキスト初期化済みフラグ
# _build_gui() で True にする。テスト環境では False のまま。
_dpg_ready: bool = False


def _resolve_settings_visibility(trans_key: str) -> dict[str, bool]:
    """
    翻訳エンジンキーに応じた設定グループの表示/非表示マップを返す純関数。

    Parameters
    ----------
    trans_key:
        翻訳エンジンの内部キー ("openai" / "deepl" / "openai-realtime")。

    Returns
    -------
    dict[str, bool]
        "whisper": Whisper/VAD 設定グループの表示フラグ
        "realtime": Realtime 専用設定グループの表示フラグ
        "deepl_key": DeepL API キーグループの表示フラグ
    """
    is_realtime = trans_key == "openai-realtime"
    return {
        "whisper": not is_realtime,
        "realtime": is_realtime,
        "deepl_key": not is_realtime,
    }


def _update_settings_visibility(trans_key: str) -> None:
    """翻訳エンジン選択に応じて詳細設定グループの可視性を更新する。"""
    v = _resolve_settings_visibility(trans_key)
    if dpg.does_item_exist(TAG_WHISPER_SETTINGS_GROUP):
        dpg.configure_item(TAG_WHISPER_SETTINGS_GROUP, show=v["whisper"])
    if dpg.does_item_exist(TAG_REALTIME_SETTINGS_GROUP):
        dpg.configure_item(TAG_REALTIME_SETTINGS_GROUP, show=v["realtime"])
    if dpg.does_item_exist(TAG_DEEPL_KEY_GROUP):
        dpg.configure_item(TAG_DEEPL_KEY_GROUP, show=v["deepl_key"])


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings():
    # dpg コンテキストが初期化されていない場合（テスト環境など）は何もしない
    if not _dpg_ready:
        return
    try:
        # 翻訳エンジンは表示ラベルではなく内部キーで保存する
        # TAG_TRANS_COMBO は _build_gui から削除済み（単独モード廃止）のため does_item_exist で安全化
        if dpg.does_item_exist(TAG_TRANS_COMBO):
            trans_label = dpg.get_value(TAG_TRANS_COMBO)
            trans_key = _trans_label_to_key(trans_label)
        else:
            trans_key = "openai-realtime"
        output_device = ""
        if dpg.does_item_exist(TAG_OUTPUT_DEVICE_COMBO):
            output_device = dpg.get_value(TAG_OUTPUT_DEVICE_COMBO)

        # ヘルパー: ウィジェットの値を安全に取得
        def _get(tag, default=None):
            if dpg.does_item_exist(tag):
                try:
                    return dpg.get_value(tag)
                except Exception:
                    return default
            return default

        data = {
            "device": dpg.get_value(TAG_DEVICE_COMBO) if dpg.does_item_exist(TAG_DEVICE_COMBO) else "",
            "model": dpg.get_value(TAG_MODEL_COMBO) if dpg.does_item_exist(TAG_MODEL_COMBO) else "",
            "trans": trans_key,
            "vad_sensitivity": dpg.get_value(TAG_VAD_SENSITIVITY) if dpg.does_item_exist(TAG_VAD_SENSITIVITY) else VAD_DEFAULT_SENSITIVITY,
            "vad_silence": dpg.get_value(TAG_VAD_SILENCE) if dpg.does_item_exist(TAG_VAD_SILENCE) else VAD_DEFAULT_SILENCE,
            "gain_mode": dpg.get_value(TAG_GAIN_MODE) if dpg.does_item_exist(TAG_GAIN_MODE) else GAIN_DEFAULT_MODE,
            "gain_value": dpg.get_value(TAG_GAIN_SLIDER) if dpg.does_item_exist(TAG_GAIN_SLIDER) else GAIN_DEFAULT_VALUE,
            "verbose": _verbose_state,
            "output_device": output_device,
            "host_api": _get(TAG_HOST_API_COMBO, "wasapi"),
            "route_a": {
                "enabled":        _get(TAG_ROUTE_A_ENABLE, True),
                "device":         _get(TAG_ROUTE_A_DEVICE_COMBO, ""),
                "lang":           _get(TAG_ROUTE_A_LANG_COMBO, ""),
                "output_enabled": _get(TAG_ROUTE_A_OUTPUT_ENABLE, False),
                "output_device":  _get(TAG_ROUTE_A_OUTPUT_DEVICE_COMBO, "(なし)"),
                "output_volume":  _get(TAG_ROUTE_A_OUTPUT_VOLUME, 1.0),
            },
        }
        # PTT 設定を route_b にマージ（W-3: _build_ptt_settings_dict 経由で統一）
        # route_b の GUI 値を先に構築してから PTT 設定をマージする
        _route_b_base = {
            "enabled":        _get(TAG_ROUTE_B_ENABLE, True),
            "device":         _get(TAG_ROUTE_B_DEVICE_COMBO, ""),
            "lang":           _get(TAG_ROUTE_B_LANG_COMBO, ""),
            "output_enabled": _get(TAG_ROUTE_B_OUTPUT_ENABLE, True),
            "output_device":  _get(TAG_ROUTE_B_OUTPUT_DEVICE_COMBO, "(なし)"),
            "output_volume":  _get(TAG_ROUTE_B_OUTPUT_VOLUME, 1.0),
        }
        data["route_b"] = _build_ptt_settings_dict(
            existing_data={"route_b": _route_b_base},
            ptt_enabled=_ptt_enabled,
            ptt_hotkey=_ptt_hotkey,
        )["route_b"]
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# API キー保存
# ---------------------------------------------------------------------------

import re as _re
import logging as _app_logging
_app_logger = _app_logging.getLogger(__name__)


def _save_api_keys_to_config(
    openai_key_plain: str,
    deepl_key_plain: str,
    target_path: _Path | None = None,
) -> bool:
    """
    config.yaml の openai.api_key / deepl.api_key を b64: 形式で上書きする。

    空文字のキーはそのセクションを変更しない。
    行単位スキャンでセクションヘッダーを検出して対象行だけ置換する方式を採用。
    正規表現による一括置換より安全で、既存コメント・空行・インデントを保持できる。

    書き込みは atomic (tempfile → os.replace) で行うため、書き込み中の
    プロセスクラッシュによる config 破損を防ぐ。

    Parameters
    ----------
    openai_key_plain:
        OpenAI API キー（平文）。空文字の場合は変更しない。
    deepl_key_plain:
        DeepL API キー（平文）。空文字の場合は変更しない。
    target_path:
        書き込み先ファイルパス。None の場合は CONFIG_PATH を使う。
        テストで任意のパスを指定する用途に使用可。
    """
    path = target_path if target_path is not None else CONFIG_PATH
    try:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        current_section = None
        out = []
        for line in lines:
            # トップレベルセクション検出（インデント無しの "name:" 行）
            m = _re.match(r'^([a-zA-Z_]+):\s*(?:#.*)?$', line)
            if m:
                current_section = m.group(1)
                out.append(line)
                continue

            # api_key 行検出（任意のインデント）
            m = _re.match(r'^(\s+api_key:\s*)(.*)$', line)
            if m:
                indent_key = m.group(1)
                rest = m.group(2)
                # コメント部分を保持
                comment_idx = rest.find('#')
                comment = rest[comment_idx:] if comment_idx >= 0 else ""

                if current_section == "openai" and openai_key_plain:
                    new_val = f'"{encode_api_key(openai_key_plain)}"'
                    line = (f"{indent_key}{new_val}  {comment}\n"
                            if comment else f"{indent_key}{new_val}\n")
                elif current_section == "deepl" and deepl_key_plain:
                    new_val = f'"{encode_api_key(deepl_key_plain)}"'
                    line = (f"{indent_key}{new_val}  {comment}\n"
                            if comment else f"{indent_key}{new_val}\n")

            out.append(line)

        # atomic write: truncate-then-write の途中 crash による破損を防ぐ
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=".config.yaml.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("".join(out))
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

        return True
    except Exception as e:
        _app_logger.error("config.yaml write failed: %s", e)
        return False


def _on_key_show_toggle(sender, app_data, user_data):
    """Show/Hide トグルボタンのコールバック。パスワードモードを切り替える。"""
    tag = user_data  # TAG_OPENAI_KEY_INPUT または TAG_DEEPL_KEY_INPUT
    if not dpg.does_item_exist(tag):
        return
    # ボタンラベルで現在の表示状態を判定し、configure_item で password モードを切り替える。
    current_label = dpg.get_item_label(sender)
    show_now = (current_label == "表示")
    dpg.configure_item(tag, password=not show_now)
    dpg.configure_item(sender, label="非表示" if show_now else "表示")


def _on_save_api_keys():
    """保存ボタン押下のコールバック。"""
    openai_plain = dpg.get_value(TAG_OPENAI_KEY_INPUT) if dpg.does_item_exist(TAG_OPENAI_KEY_INPUT) else ""
    deepl_plain = dpg.get_value(TAG_DEEPL_KEY_INPUT) if dpg.does_item_exist(TAG_DEEPL_KEY_INPUT) else ""

    if not openai_plain and not deepl_plain:
        if dpg.does_item_exist(TAG_KEY_STATUS):
            dpg.set_value(TAG_KEY_STATUS, "キーが未入力です")
        return

    ok = _save_api_keys_to_config(openai_plain, deepl_plain)

    if dpg.does_item_exist(TAG_KEY_STATUS):
        if ok:
            if _is_running:
                dpg.set_value(TAG_KEY_STATUS, "変更は次回起動時に反映されます")
            else:
                dpg.set_value(TAG_KEY_STATUS, "保存しました")
                # 停止中のみ: 翻訳エンジンコンボを再構築して Start ボタンを有効化
                global _config
                try:
                    import yaml as _yaml
                    _config = _yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
                except Exception:
                    pass
                new_models = _available_trans_models(_config)
                if dpg.does_item_exist(TAG_TRANS_COMBO):
                    dpg.configure_item(TAG_TRANS_COMBO,
                                       items=new_models if new_models else ["(APIキー未設定)"])
                    if new_models:
                        dpg.set_value(TAG_TRANS_COMBO, new_models[0])
                if dpg.does_item_exist(TAG_START_BTN):
                    dpg.configure_item(TAG_START_BTN, enabled=bool(new_models))
        else:
            dpg.set_value(TAG_KEY_STATUS, "保存に失敗しました")


def _on_open_quota_page(sender=None, app_data=None, user_data=None):
    """クォータを Web で確認ボタンのコールバック。(issue #80)"""
    _open_quota_usage_page()


# ---------------------------------------------------------------------------
# Host API フィルタ変更
# ---------------------------------------------------------------------------

def _on_host_api_change(sender, value, user_data):
    """Host API フィルタ変更時にデバイスコンボを再列挙する。

    仕様書 §4.9 / B-05: 系統1・系統2 の入力/出力デバイスコンボも再列挙する。
    """
    global _devices
    host_api_value = value.split()[0].lower()  # "wasapi (default)" -> "wasapi"
    _devices = list_audio_devices(host_api=host_api_value)
    device_labels = [_device_label(d) for d in _devices]
    output_devices = list_audio_devices(device_type="output", host_api=host_api_value)
    output_labels = ["(なし)"] + [d["name"] for d in output_devices]

    # 旧単独モード用（残存）
    if dpg.does_item_exist(TAG_DEVICE_COMBO):
        dpg.configure_item(TAG_DEVICE_COMBO, items=device_labels)
        if device_labels:
            dpg.set_value(TAG_DEVICE_COMBO, device_labels[0])
    if dpg.does_item_exist(TAG_OUTPUT_DEVICE_COMBO):
        dpg.configure_item(TAG_OUTPUT_DEVICE_COMBO, items=output_labels)
        dpg.set_value(TAG_OUTPUT_DEVICE_COMBO, "(なし)")

    # 系統1: 入力 (loopback デフォルト) + 出力
    if dpg.does_item_exist(TAG_ROUTE_A_DEVICE_COMBO):
        dpg.configure_item(TAG_ROUTE_A_DEVICE_COMBO, items=device_labels)
        if device_labels:
            loopback_a = next((lbl for lbl in device_labels if "[Loopback]" in lbl), device_labels[0])
            dpg.set_value(TAG_ROUTE_A_DEVICE_COMBO, loopback_a)
    if dpg.does_item_exist(TAG_ROUTE_A_OUTPUT_DEVICE_COMBO):
        dpg.configure_item(TAG_ROUTE_A_OUTPUT_DEVICE_COMBO, items=output_labels)
        dpg.set_value(TAG_ROUTE_A_OUTPUT_DEVICE_COMBO, "(なし)")

    # 系統2: 入力 (非 loopback デフォルト) + 出力 (CABLE Input デフォルト)
    if dpg.does_item_exist(TAG_ROUTE_B_DEVICE_COMBO):
        dpg.configure_item(TAG_ROUTE_B_DEVICE_COMBO, items=device_labels)
        if device_labels:
            mic_b = next((lbl for lbl in device_labels if "[Loopback]" not in lbl), device_labels[0])
            dpg.set_value(TAG_ROUTE_B_DEVICE_COMBO, mic_b)
    if dpg.does_item_exist(TAG_ROUTE_B_OUTPUT_DEVICE_COMBO):
        dpg.configure_item(TAG_ROUTE_B_OUTPUT_DEVICE_COMBO, items=output_labels)
        cable = next((lbl for lbl in output_labels if "cable input" in lbl.lower()), "(なし)")
        dpg.set_value(TAG_ROUTE_B_OUTPUT_DEVICE_COMBO, cable)

    _save_settings()


# ---------------------------------------------------------------------------
# VAD リアルタイム更新
# ---------------------------------------------------------------------------

def _on_vad_sensitivity_change(sender, value, user_data):
    if _system and _system._recorder:
        try:
            _system._recorder.silero_sensitivity = value
        except Exception:
            pass


def _on_vad_silence_change(sender, value, user_data):
    if _system and _system._recorder:
        try:
            _system._recorder.post_speech_silence_duration = value
        except Exception:
            pass


def _on_gain_mode_change(sender, value, user_data):
    if _system:
        _system.gain_mode = value
    # Manual スライダー・ラベルは manual モード時のみ表示
    show_manual = (value == "manual")
    if dpg.does_item_exist(TAG_GAIN_SLIDER):
        dpg.configure_item(TAG_GAIN_SLIDER, show=show_manual)
    if dpg.does_item_exist(TAG_GAIN_LABEL):
        dpg.configure_item(TAG_GAIN_LABEL, show=show_manual)
    _save_settings()


def _on_gain_value_change(sender, value, user_data):
    if _system:
        _system.manual_gain = float(value)


def _on_route_a_volume_change(sender, app_data, user_data):
    """経路A 出力音量スライダー変更時。動作中の AudioOutputStream に即反映。"""
    print(f"[USER] 系統1 出力音量変更: {float(app_data):.2f}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_a_system is None:
        return
    try:
        _konnyaku_system.route_a_system.output_volume = float(app_data)
    except Exception:
        pass


def _on_route_b_volume_change(sender, app_data, user_data):
    """経路B 出力音量スライダー変更時。動作中の AudioOutputStream に即反映。"""
    print(f"[USER] 系統2 出力音量変更: {float(app_data):.2f}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_b_system is None:
        return
    try:
        _konnyaku_system.route_b_system.output_volume = float(app_data)
    except Exception:
        pass


def _on_route_a_output_device_change(sender, app_data, user_data):
    """経路A 出力デバイス変更時。動作中の系統に即反映。"""
    print(f"[USER] 系統1 出力デバイス選択: {app_data!r}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_a_system is None:
        return
    label = str(app_data)
    if not label or label == "(なし)":
        index = None
    else:
        out_devices = list_audio_devices(device_type="output")
        matched = find_device_by_name(label, out_devices)
        index = matched["index"] if matched else None
    try:
        _konnyaku_system.route_a_system.set_output_device(index)
    except Exception as e:
        print(f"[ERROR] route_a 出力デバイス変更失敗: {e}", flush=True)


def _on_route_b_output_device_change(sender, app_data, user_data):
    """経路B 出力デバイス変更時。動作中の系統に即反映。"""
    print(f"[USER] 系統2 出力デバイス選択: {app_data!r}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_b_system is None:
        return
    label = str(app_data)
    if not label or label == "(なし)":
        index = None
    else:
        out_devices = list_audio_devices(device_type="output")
        matched = find_device_by_name(label, out_devices)
        index = matched["index"] if matched else None
    try:
        _konnyaku_system.route_b_system.set_output_device(index)
    except Exception as e:
        print(f"[ERROR] route_b 出力デバイス変更失敗: {e}", flush=True)


def _on_route_a_output_enable_change(sender, app_data, user_data):
    """経路A 音声出力 ON/OFF 変更時。稼働中なら即反映。"""
    print(f"[USER] 系統1 音声出力 {'ON' if app_data else 'OFF'}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_a_system is None:
        return
    enabled = bool(app_data)
    if enabled:
        label = (dpg.get_value(TAG_ROUTE_A_OUTPUT_DEVICE_COMBO)
                 if dpg.does_item_exist(TAG_ROUTE_A_OUTPUT_DEVICE_COMBO) else "")
        if label and label != "(なし)":
            out_devices = list_audio_devices(device_type="output")
            matched = find_device_by_name(label, out_devices)
            if matched:
                try:
                    _konnyaku_system.route_a_system.set_output_device(matched["index"])
                except Exception as e:
                    print(f"[ERROR] route_a 出力 ON 失敗: {e}", flush=True)
    else:
        try:
            _konnyaku_system.route_a_system.set_output_device(None)
        except Exception as e:
            print(f"[ERROR] route_a 出力 OFF 失敗: {e}", flush=True)


def _on_route_b_output_enable_change(sender, app_data, user_data):
    """経路B 音声出力 ON/OFF 変更時。稼働中なら即反映。"""
    print(f"[USER] 系統2 音声出力 {'ON' if app_data else 'OFF'}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_b_system is None:
        return
    enabled = bool(app_data)
    if enabled:
        label = (dpg.get_value(TAG_ROUTE_B_OUTPUT_DEVICE_COMBO)
                 if dpg.does_item_exist(TAG_ROUTE_B_OUTPUT_DEVICE_COMBO) else "")
        if label and label != "(なし)":
            out_devices = list_audio_devices(device_type="output")
            matched = find_device_by_name(label, out_devices)
            if matched:
                try:
                    _konnyaku_system.route_b_system.set_output_device(matched["index"])
                except Exception as e:
                    print(f"[ERROR] route_b 出力 ON 失敗: {e}", flush=True)
    else:
        try:
            _konnyaku_system.route_b_system.set_output_device(None)
        except Exception as e:
            print(f"[ERROR] route_b 出力 OFF 失敗: {e}", flush=True)


def _find_zoom_preset_output(devices: list[dict]) -> int | None:
    """
    デバイスリストから CABLE Input (VB-CABLE) のインデックスを返す純関数。

    Parameters
    ----------
    devices:
        各要素に "name" (str) と "index" (int) を持つ辞書のリスト。
        例: [{"name": "Speakers", "index": 1}, {"name": "CABLE Input ...", "index": 5}]

    Returns
    -------
    int | None
        "cable input" を名前に含む最初のデバイスの index。見つからない場合は None。
    """
    for device in devices:
        if "cable input" in device.get("name", "").lower():
            return device["index"]
    return None


def _on_zoom_preset_click():
    """
    Zoom 同時通訳プリセットボタン押下。
    - 翻訳エンジンを OpenAI Realtime に設定
    - 出力デバイスを CABLE Input（VB-CABLE）に自動選択
    設定を適用するのみ。起動はしない。
    """
    # 翻訳エンジンを openai-realtime に変更
    realtime_label = _trans_key_to_label("openai-realtime")
    if dpg.does_item_exist(TAG_TRANS_COMBO):
        items = dpg.get_item_configuration(TAG_TRANS_COMBO).get("items", [])
        if realtime_label in items:
            dpg.set_value(TAG_TRANS_COMBO, realtime_label)
            # コールバックは set_value では自動発火しないので、明示的に visibility を更新
            _update_settings_visibility("openai-realtime")

    # 出力デバイスを CABLE Input に自動選択
    if dpg.does_item_exist(TAG_OUTPUT_DEVICE_COMBO):
        items = dpg.get_item_configuration(TAG_OUTPUT_DEVICE_COMBO).get("items", [])
        # GUI のアイテムリストは "name (index)" 形式の文字列のため、
        # 名前部分の大小文字無視マッチで CABLE Input を検索する。
        cable_item = next((it for it in items if "cable input" in it.lower()), None)
        if cable_item:
            dpg.set_value(TAG_OUTPUT_DEVICE_COMBO, cable_item)
        else:
            print("[INFO] CABLE Input デバイスが見つかりませんでした。VB-CABLE をインストールしてください。")

    _save_settings()


def _on_both_routes_on(sender=None, app_data=None, user_data=None):
    """系統1・系統2 を両方とも有効化（一括 ON）。"""
    print("[USER] 両方 ON ボタン押下", flush=True)
    if dpg.does_item_exist(TAG_ROUTE_A_ENABLE):
        dpg.set_value(TAG_ROUTE_A_ENABLE, True)
    if dpg.does_item_exist(TAG_ROUTE_B_ENABLE):
        dpg.set_value(TAG_ROUTE_B_ENABLE, True)
    _save_settings()


def _on_both_routes_off(sender=None, app_data=None, user_data=None):
    """系統1・系統2 を両方とも無効化（一括 OFF）。"""
    print("[USER] 両方 OFF ボタン押下", flush=True)
    if dpg.does_item_exist(TAG_ROUTE_A_ENABLE):
        dpg.set_value(TAG_ROUTE_A_ENABLE, False)
    if dpg.does_item_exist(TAG_ROUTE_B_ENABLE):
        dpg.set_value(TAG_ROUTE_B_ENABLE, False)
    _save_settings()


def _konnyaku_thread_error_handler(route_id: str, exc: Exception, tb: str) -> None:
    """MultiCaptionSystem のバックグラウンドスレッドが例外で終了したときに呼ばれるコールバック。

    GUI スレッドからではなく daemon スレッドから呼ばれるため、
    dpg への書き込みは _gui_queue 経由が安全だが、ステータスバーへの単発 set_value は
    DearPyGui の仕様上 GUI スレッド以外からでも概ね動作する（最悪ドロップされる）。
    """
    msg = f"こんにゃく route-{route_id} スレッド異常終了: {type(exc).__name__}: {exc}"
    print(f"[ERROR] {msg}", flush=True)
    if dpg.does_item_exist(TAG_STATUS_STATE):
        dpg.set_value(TAG_STATUS_STATE, msg)


def _on_realtime_error_handler(route_id: str, category: str, display_text: str) -> None:
    """RealtimeTranslator エラーを GUI ステータスバーに表示する。

    Parameters
    ----------
    route_id:
        エラーが発生した経路 ("a" | "b")。
    category:
        エラーカテゴリ ("quota" | "auth" | "rate_limit" | "connection" | "other")。
    display_text:
        GUI に表示するメッセージ（⚠️ 絵文字付き）。
    """
    route_label = "系統1" if route_id == "a" else "系統2"
    full_text = f"[{route_label}] {display_text}"
    print(f"[GUI ERROR] {full_text}", flush=True)
    if dpg.does_item_exist(TAG_STATUS_STATE):
        try:
            dpg.set_value(TAG_STATUS_STATE, full_text)
        except Exception:
            pass


def _on_result_route_a_dispatch(original: str, translated: str) -> None:
    """系統1 翻訳結果を GUI ログに追加（常駐 callback）。"""
    ts = datetime.now().strftime("%H:%M:%S")
    _log_entries.append({
        "ts": ts,
        "original": (f"[系統1 入力] {original}" if original else ""),
        "translated": (f"[系統1 出力] {translated}" if translated else ""),
        "route": "a",
    })
    if len(_log_entries) > 200:
        _log_entries.pop(0)
    _enqueue(
        "append_log",
        ts=ts,
        original=(f"[系統1 入力] {original}" if original else ""),
        translated=(f"[系統1 出力] {translated}" if translated else ""),
    )


def _on_result_route_b_dispatch(original: str, translated: str) -> None:
    """系統2 翻訳結果を GUI ログに追加（常駐 callback）。"""
    ts = datetime.now().strftime("%H:%M:%S")
    _log_entries.append({
        "ts": ts,
        "original": (f"[系統2 入力] {original}" if original else ""),
        "translated": (f"[系統2 出力] {translated}" if translated else ""),
        "route": "b",
    })
    if len(_log_entries) > 200:
        _log_entries.pop(0)
    _enqueue(
        "append_log",
        ts=ts,
        original=(f"[系統2 入力] {original}" if original else ""),
        translated=(f"[系統2 出力] {translated}" if translated else ""),
    )


def _create_konnyaku_system() -> None:
    """アプリ起動時に MultiCaptionSystem を生成（常駐モデル）。

    保存設定 (settings.json) があればそれを使う。なければデフォルト RouteConfig。
    callback はクロージャ経由で遅延バインド。
    """
    global _konnyaku_system
    if _konnyaku_system is not None:
        return  # 既に生成済み

    # デバイスがひとつもなければスキップ
    if not _devices:
        print("[WARN] 利用可能なデバイスがありません", flush=True)
        return

    # 保存設定をロード
    saved = _load_settings()
    route_a_saved = saved.get("route_a", {})
    route_b_saved = saved.get("route_b", {})

    # デフォルトデバイス選択
    device_labels = [_device_label(d) for d in _devices]
    loopback_label = next((lbl for lbl in device_labels if "[Loopback]" in lbl), None)
    non_loopback_label = next((lbl for lbl in device_labels if "[Loopback]" not in lbl), None)

    # 保存値があればそれを優先、なければデフォルト
    saved_a_label = route_a_saved.get("device", "")
    if saved_a_label and saved_a_label in device_labels:
        route_a_device = next((d for d in _devices if _device_label(d) == saved_a_label), None)
        print(f"[INFO] _create_konnyaku_system: route_a using saved device '{saved_a_label}'", flush=True)
    else:
        route_a_device = next(
            (d for d in _devices if _device_label(d) == (loopback_label or device_labels[0])),
            _devices[0],
        )
        print(f"[INFO] _create_konnyaku_system: route_a using default '{_device_label(route_a_device)}'", flush=True)

    saved_b_label = route_b_saved.get("device", "")
    if saved_b_label and saved_b_label in device_labels:
        route_b_device = next((d for d in _devices if _device_label(d) == saved_b_label), None)
        print(f"[INFO] _create_konnyaku_system: route_b using saved device '{saved_b_label}'", flush=True)
    else:
        route_b_device = next(
            (d for d in _devices if _device_label(d) == (non_loopback_label or device_labels[0])),
            _devices[0],
        )
        print(f"[INFO] _create_konnyaku_system: route_b using default '{_device_label(route_b_device)}'", flush=True)

    cfg = {**_config}
    cfg.setdefault("translation", {})["translation_model"] = "openai-realtime"

    # 翻訳先言語（保存値があれば反映）
    lang_codes = get_language_codes()
    lang_names = get_language_display_names()
    saved_a_lang_name = route_a_saved.get("lang", "")
    a_lang_code = "ja"
    if saved_a_lang_name in lang_names:
        a_lang_code = lang_codes[lang_names.index(saved_a_lang_name)]
    saved_b_lang_name = route_b_saved.get("lang", "")
    b_lang_code = "en"
    if saved_b_lang_name in lang_names:
        b_lang_code = lang_codes[lang_names.index(saved_b_lang_name)]

    # 出力デバイスインデックスを解決
    # （audio_output_enabled=True かつ output_device の保存値があれば index を解決）
    def _resolve_output_device_index(saved_label: str) -> int | None:
        if not saved_label or saved_label == "(なし)":
            return None
        out_devs = list_audio_devices(device_type="output")
        matched = find_device_by_name(saved_label, out_devs)
        return matched["index"] if matched else None

    a_output_enabled = bool(route_a_saved.get("output_enabled", False))
    a_output_idx = (
        _resolve_output_device_index(route_a_saved.get("output_device", ""))
        if a_output_enabled else None
    )
    b_output_enabled = bool(route_b_saved.get("output_enabled", True))
    b_output_idx = (
        _resolve_output_device_index(route_b_saved.get("output_device", ""))
        if b_output_enabled else None
    )
    print(
        f"[INFO] _create_konnyaku_system: route_a output_enabled={a_output_enabled} "
        f"output_device_index={a_output_idx}",
        flush=True,
    )
    print(
        f"[INFO] _create_konnyaku_system: route_b output_enabled={b_output_enabled} "
        f"output_device_index={b_output_idx}",
        flush=True,
    )

    route_a_cfg = RouteConfig(
        route_id="a",
        input_device_info=route_a_device,
        target_language_code=a_lang_code,
        audio_output_enabled=a_output_enabled,
        output_device_index=a_output_idx,
        output_volume=float(route_a_saved.get("output_volume", 1.0)),
    )
    route_b_cfg = RouteConfig(
        route_id="b",
        input_device_info=route_b_device,
        target_language_code=b_lang_code,
        audio_output_enabled=b_output_enabled,
        output_device_index=b_output_idx,
        output_volume=float(route_b_saved.get("output_volume", 1.0)),
    )

    _konnyaku_system = MultiCaptionSystem(
        config=cfg,
        route_a=route_a_cfg,
        route_b=route_b_cfg,
        on_result_a=lambda o, t: _on_result_route_a_dispatch(o, t),
        on_result_b=lambda o, t: _on_result_route_b_dispatch(o, t),
        on_realtime_error=_on_realtime_error_handler,
        on_thread_error=_konnyaku_thread_error_handler,
    )
    # ゲインモードは auto 固定
    if _konnyaku_system.route_a_system is not None:
        _konnyaku_system.route_a_system.gain_mode = "auto"
    if _konnyaku_system.route_b_system is not None:
        _konnyaku_system.route_b_system.gain_mode = "auto"
    print("[INFO] MultiCaptionSystem 常駐生成完了", flush=True)


def _on_route_a_enable_change(sender, app_data, user_data) -> None:
    """系統1 有効チェック変更時。稼働中なら即時反映（B-14）。"""
    print(f"[USER] 系統1 有効チェック {'ON' if app_data else 'OFF'}", flush=True)
    _save_settings()
    if _konnyaku_system is None:
        return
    if app_data:
        _konnyaku_system.start_route("a")
    else:
        _konnyaku_system.stop_route("a")


def _on_route_b_enable_change(sender, app_data, user_data) -> None:
    """系統2 有効チェック変更時。PTT 対応版に委譲（B-14 / TBD-4）。"""
    _on_route_b_enable_change_ptt_aware(enabled=bool(app_data))


# ---------------------------------------------------------------------------
# PTT (Push-to-Talk) コールバック・管理関数 (issue #82 / ptt-mode-design.md)
# ---------------------------------------------------------------------------

def _on_ptt_press(event) -> None:
    """PTT ホットキー押下コールバック（keyboard スレッドから呼ばれる）。

    start_route("b") は WebSocket 接続を含むため別スレッドで実行する（F-2.1）。
    _konnyaku_running=False の場合は no-op（F-7.4）。
    """
    if not _konnyaku_running:
        return
    if _konnyaku_system is None:
        return
    print("[PTT] press: route_b 起動", flush=True)
    threading.Thread(
        target=_konnyaku_system.start_route,
        args=("b",),
        daemon=True,
        name="PttStartRouteB",
    ).start()
    _gui_queue.put({"cmd": "update_ptt_visual"})


def _on_ptt_release(event) -> None:
    """PTT ホットキー離脱コールバック（keyboard スレッドから呼ばれる）。

    stop_route("b") は別スレッドで実行する（F-2.2）。
    _konnyaku_running=False の場合は no-op。
    """
    if not _konnyaku_running:
        return
    if _konnyaku_system is None:
        return
    print("[PTT] release: route_b 停止", flush=True)
    threading.Thread(
        target=_konnyaku_system.stop_route,
        args=("b",),
        daemon=True,
        name="PttStopRouteB",
    ).start()
    _gui_queue.put({"cmd": "update_ptt_visual"})


def _on_ptt_chatter_warning() -> None:
    """連打上限（10秒内に 5 回以上）検出時のコールバック（F-4.3）。"""
    print("[PTT] 警告: 連打（chatter）を検出しました。しばらく操作をお待ちください。", flush=True)


def _load_ptt_settings(saved: dict) -> dict:
    """settings.json の saved データから PTT 設定を読み込む純関数。

    Parameters
    ----------
    saved : dict
        _load_settings() で読み込んだ設定辞書。

    Returns
    -------
    dict
        "ptt_enabled" (bool) と "ptt_hotkey" (str) を含む辞書。
        キーが存在しない場合はデフォルト値（False, "f8"）を返す。
    """
    route_b = saved.get("route_b", {})
    return {
        "ptt_enabled": bool(route_b.get("ptt_enabled", False)),
        "ptt_hotkey": str(route_b.get("ptt_hotkey", "f8")),
    }


def _build_ptt_settings_dict(
    existing_data: dict,
    ptt_enabled: bool,
    ptt_hotkey: str,
) -> dict:
    """PTT 設定を既存データの route_b にマージした辞書を返す純関数。

    既存の route_b キーを破壊せず、ptt_enabled / ptt_hotkey のみ上書きする。

    Parameters
    ----------
    existing_data : dict
        既存の設定辞書（settings.json 相当）。
    ptt_enabled : bool
        保存する PTT 有効フラグ。
    ptt_hotkey : str
        保存するホットキー文字列。

    Returns
    -------
    dict
        ptt_enabled / ptt_hotkey がマージされた設定辞書。
    """
    import copy
    data = copy.deepcopy(existing_data)
    route_b = data.setdefault("route_b", {})
    route_b["ptt_enabled"] = ptt_enabled
    route_b["ptt_hotkey"] = ptt_hotkey
    return data


def _init_ptt_manager(
    ptt_enabled: bool,
    ptt_hotkey: str,
    keyboard_module=None,
    timer_factory=None,
) -> None:
    """PttHotkeyManager をインスタンス化し、ptt_enabled=True の場合のみ start() する。

    Parameters
    ----------
    ptt_enabled : bool
        True の場合はホットキーの監視を開始する。
    ptt_hotkey : str
        監視するホットキー文字列（例: "f8"）。
    keyboard_module : optional
        テスト注入用 keyboard モジュール代替。
    timer_factory : optional
        テスト注入用タイマーファクトリー。
    """
    global _ptt_manager, _ptt_enabled, _ptt_hotkey

    # 既存の manager があれば停止してから置き換え
    if _ptt_manager is not None:
        try:
            _ptt_manager.stop()
        except Exception:
            pass

    _ptt_enabled = ptt_enabled
    _ptt_hotkey = ptt_hotkey

    kwargs = {
        "hotkey": ptt_hotkey,
        "on_press": _on_ptt_press,
        "on_release": _on_ptt_release,
        "on_chatter_warning": _on_ptt_chatter_warning,
    }
    if keyboard_module is not None:
        kwargs["keyboard_module"] = keyboard_module
    if timer_factory is not None:
        kwargs["timer_factory"] = timer_factory

    _ptt_manager = PttHotkeyManager(**kwargs)

    if ptt_enabled:
        _ptt_manager.start()
        print(f"[PTT] 有効化: ホットキー={ptt_hotkey}", flush=True)
    else:
        print("[PTT] 無効（ptt_enabled=False）", flush=True)


def _cleanup_ptt_manager() -> None:
    """PttHotkeyManager を停止してリソースを解放する（アプリ終了時に呼ぶ）。

    F-7.5: アプリ終了時のホットキー登録解除。
    _ptt_manager が None の場合は no-op。
    """
    global _ptt_manager
    if _ptt_manager is None:
        return
    try:
        _ptt_manager.stop()
    except Exception as e:
        print(f"[PTT] クリーンアップ中にエラー: {e}", flush=True)
    finally:
        _ptt_manager = None


def _is_route_b_active_for_meter(ptt_enabled: bool) -> bool:
    """レベルメーター表示用に系統Bが稼働中かどうかを返す純関数（F-7.1）。

    PTT モード有効時は RouteState.RUNNING で判定し、
    PTT モード無効時はチェックボックスの値（呼び出し側が提供）に委ねる。

    設計意図（W-2 PR4 — _is_ptt_pressing との差異）:
      この関数は RUNNING のみ True を返す（STARTING は False）。
      レベルメーターは音声データが実際に流れている場合のみ更新すべきであり、
      STARTING 中（WebSocket 接続待ち、音声未入力）は 0 表示が妥当なため。

      一方、_is_ptt_pressing() は STARTING + RUNNING の両方で True を返す。
      これは PTT 押下中のデバイスコンボ disable 判定に使うため、
      起動途中も含めて「押下操作中」として扱う必要があるから（TBD-3）。

    Parameters
    ----------
    ptt_enabled : bool
        PTT モードが有効かどうか。

    Returns
    -------
    bool
        True = 系統B が RUNNING 状態（レベルメーターを更新すべき）。
        STARTING / IDLE / STOPPING / ERROR は False。
    """
    if not ptt_enabled:
        # PTT 無効時: 呼び出し側がチェックボックス値で判定するため True を返す
        # （既存の route_b_enabled ロジックを維持）
        return True
    if _konnyaku_system is None:
        return False
    route_b = _konnyaku_system.route_b_system
    if route_b is None:
        return False
    return route_b.state == RouteState.RUNNING


def _is_ptt_pressing() -> bool:
    """PTT ホットキー押下中（系統Bが STARTING または RUNNING 状態）かどうかを返す。

    PTT モード OFF の場合、または _konnyaku_system が None の場合は False を返す。
    TBD-3: 押下中はデバイスコンボを disable する判定に使用する（F-7.2）。

    設計意図（W-2 PR4 — _is_route_b_active_for_meter との差異）:
      STARTING + RUNNING の両方で True を返す。
      PTT ホットキーを押している操作中（起動途中含む）はデバイスコンボを disable し続ける
      必要があるため、接続待ち中（STARTING）でも押下中として扱う（TBD-3）。

      一方、_is_route_b_active_for_meter() は RUNNING のみ True を返す
      （音声が実際に流れている場合のみレベルメーターを更新するため）。

    Returns
    -------
    bool
        True = PTT ホットキー押下中（系統B稼働中 or 起動中: STARTING or RUNNING）。
    """
    if not _ptt_enabled:
        return False
    if _konnyaku_system is None:
        return False
    route_b = _konnyaku_system.route_b_system
    if route_b is None:
        return False
    return route_b.state in (RouteState.STARTING, RouteState.RUNNING)


def _on_ptt_enabled_change(enabled: bool) -> None:
    """PTT モード有効チェックボックス変更時のコールバック（F-5）。

    ON 時: _ptt_manager.start() を呼んでホットキーを有効化。
    OFF 時: _cleanup_ptt_manager() を呼んでホットキーを解除。
    変更は即座に _save_settings() で永続化する。

    Parameters
    ----------
    enabled : bool
        チェックボックスの新しい値。
    """
    global _ptt_enabled
    _ptt_enabled = enabled
    print(f"[PTT] モード変更: {'ON' if enabled else 'OFF'}", flush=True)

    if enabled:
        if _ptt_manager is not None and not _ptt_manager.running:
            _ptt_manager.start()
            print(f"[PTT] 有効化: ホットキー={_ptt_hotkey}", flush=True)
        elif _ptt_manager is None:
            print("[PTT] _ptt_manager が None のため start() をスキップ", flush=True)
        # 既に running の場合は冪等性保証のため no-op
    else:
        _cleanup_ptt_manager()

    _save_settings()
    _update_ptt_visual_feedback()


def _on_ptt_hotkey_change(new_hotkey: str) -> None:
    """PTT ホットキー入力欄変更時のコールバック（F-5）。

    _ptt_manager が起動中の場合は change_hotkey() でホットキーを切り替える。
    停止中の場合は hotkey 属性の更新のみ（次回 start() 時に反映）。
    変更は即座に _save_settings() で永続化する。

    Parameters
    ----------
    new_hotkey : str
        新しいホットキー名（keyboard ライブラリのキー名形式）。
    """
    global _ptt_hotkey
    _ptt_hotkey = new_hotkey
    print(f"[PTT] ホットキー変更: {new_hotkey}", flush=True)

    if _ptt_manager is not None and _ptt_manager.running:
        _ptt_manager.change_hotkey(new_hotkey)

    _save_settings()
    _update_ptt_visual_feedback()


def _on_route_b_enable_change_ptt_aware(enabled: bool) -> None:
    """系統2 有効チェックボックス変更時の PTT 対応コールバック（TBD-4）。

    PTT モード ON の場合:
      - OFF にすると PTT モードも自動的に OFF になる（TBD-4 仕様）
      - ON にしてもここでは PTT を再起動しない（UI 側の PTT チェックボックスで操作）
    PTT モード OFF の場合:
      - 従来通り start_route/stop_route を呼ぶ

    Parameters
    ----------
    enabled : bool
        チェックボックスの新しい値。
    """
    global _ptt_enabled
    print(f"[USER] 系統2 有効チェック {'ON' if enabled else 'OFF'} "
          f"(PTT={'ON' if _ptt_enabled else 'OFF'})", flush=True)

    if _ptt_enabled:
        if not enabled:
            # PTT ON 状態で系統B を OFF → PTT モードも OFF にする（TBD-4）
            _ptt_enabled = False  # S-1 PR4: 状態更新を _save_settings() より先に行う
            _cleanup_ptt_manager()
            print("[PTT] 系統2 OFF により PTT モードを自動無効化", flush=True)
            # PTT チェックボックスの表示を同期（dpg_ready 時のみ）
            if _dpg_ready and dpg.does_item_exist(TAG_PTT_ENABLED):
                dpg.set_value(TAG_PTT_ENABLED, False)
        # PTT ON 中に系統B を ON にしても何もしない（PTT が制御を持つ）
    else:
        # PTT OFF 時は従来通り
        if _konnyaku_system is not None:
            if enabled:
                _konnyaku_system.start_route("b")
            else:
                _konnyaku_system.stop_route("b")

    # S-1 PR4: 状態更新（_ptt_enabled 等）完了後に永続化する
    _save_settings()
    _update_ptt_visual_feedback()


def _update_ptt_visual_feedback() -> None:
    """PTT の視覚フィードバックを更新する（F-6）。

    dpg が未初期化の場合は何もしない（テスト環境での安全性確保）。
    以下の要素を更新する:
    - TAG_ROUTE_B_LABEL: 系統2 見出しのラベルテキスト
    - TAG_PTT_STATUS_LABEL: PTT 押下中ステータス表示
    - TAG_STATUS_STATE: ステータスバーの PTT 状態

    呼び出しタイミング:
    - PTT チェックボックス変更時
    - ホットキー変更時
    - PTT 押下/離脱時（_on_ptt_press / _on_ptt_release が _gui_queue 経由でスケジュール）
    - 毎フレーム更新（_update_konnyaku_level_meters と同タイミング）

    Note:
        _on_ptt_press / _on_ptt_release は keyboard スレッドから呼ばれるため、
        この関数を直接呼ばず _gui_queue に "update_ptt_visual" コマンドを put する。
        _drain_queue() がメインスレッドからこの関数を呼び出す（C-1 対応）。
    """
    if not _dpg_ready:
        return

    pressing = _is_ptt_pressing()

    # 系統2 見出しラベルの更新（F-6.1 / F-6.2 / F-5.3）
    if dpg.does_item_exist(TAG_ROUTE_B_LABEL):
        if not _ptt_enabled:
            new_label = "【系統2】自分→相手（同時通訳）  I speak, they hear"
        elif pressing:
            new_label = f"【系統2 [送信中]】自分→相手（同時通訳）  I speak, they hear"
        else:
            new_label = (
                f"【系統2 (PTT: {_ptt_hotkey} 押下中)】"
                "自分→相手（同時通訳）  I speak, they hear"
            )
        dpg.set_value(TAG_ROUTE_B_LABEL, new_label)

    # PTT ステータスラベルの更新（F-6.1 / F-6.2）
    if dpg.does_item_exist(TAG_PTT_STATUS_LABEL):
        if not _ptt_enabled:
            dpg.configure_item(TAG_PTT_STATUS_LABEL, show=False)
        elif pressing:
            dpg.set_value(TAG_PTT_STATUS_LABEL, "● 送信中")
            dpg.configure_item(TAG_PTT_STATUS_LABEL, show=True)
        else:
            dpg.set_value(TAG_PTT_STATUS_LABEL, f"○ 待機中（{_ptt_hotkey.upper()} で送信）")
            dpg.configure_item(TAG_PTT_STATUS_LABEL, show=True)

    # 入力デバイスコンボの enabled/disabled 切替（TBD-3）
    if dpg.does_item_exist(TAG_ROUTE_B_DEVICE_COMBO):
        dpg.configure_item(TAG_ROUTE_B_DEVICE_COMBO, enabled=not pressing)


def _on_route_a_device_change(sender, app_data, user_data) -> None:
    """系統1 入力デバイス変更時。稼働中なら新デバイスで再起動（B-15）。"""
    print(f"[USER] 系統1 入力デバイス選択: {app_data!r}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_a_system is None:
        return
    new_device = next((d for d in _devices if _device_label(d) == app_data), None)
    if new_device is None:
        return

    route_a = _konnyaku_system.route_a_system
    try:
        route_a.set_input_device(new_device)
    except Exception as e:
        print(f"[WARN] 系統1 入力デバイス変更に失敗: {type(e).__name__}: {e}", flush=True)


def _on_route_b_device_change(sender, app_data, user_data) -> None:
    """系統2 入力デバイス変更時。稼働中なら新デバイスで再起動（B-15）。"""
    print(f"[USER] 系統2 入力デバイス選択: {app_data!r}", flush=True)
    _save_settings()
    if _konnyaku_system is None or _konnyaku_system.route_b_system is None:
        return
    new_device = next((d for d in _devices if _device_label(d) == app_data), None)
    if new_device is None:
        return

    route_b = _konnyaku_system.route_b_system
    try:
        route_b.set_input_device(new_device)
    except Exception as e:
        print(f"[WARN] 系統2 入力デバイス変更に失敗: {type(e).__name__}: {e}", flush=True)


def _on_konnyaku_start_stop_click():
    """翻訳こんにゃくモードの開始/停止ボタン（常駐モデル）。"""
    global _konnyaku_system, _konnyaku_running

    # 連打ガード: ボタンが disabled（処理中）なら無視する（W-5）
    # 停止処理中に「開始」連打すると二重起動や内部状態不整合が起きるため早期 return する。
    if dpg.does_item_exist(TAG_KONNYAKU_START_BTN):
        if not dpg.get_item_configuration(TAG_KONNYAKU_START_BTN).get("enabled", True):
            print("[USER] 開始ボタン連打を無視 (処理中)", flush=True)
            return

    print(
        f"[USER] {'停止' if _konnyaku_running else '開始'}ボタン押下"
        f" (running={_konnyaku_running})",
        flush=True,
    )

    if _konnyaku_running:
        # 停止ボタン押下: すぐにボタンを「停止中...」+ disabled に切り替え、
        # stop_all はバックグラウンドスレッドで実行して GUI がフリーズしないようにする。
        # 常駐モデル: _konnyaku_system は None にせず保持する。
        _gui_set_label(TAG_KONNYAKU_START_BTN, "停止中...", name="konnyaku_start_btn",
                       enabled=False)
        _gui_set_value(TAG_STATUS_STATE, "翻訳こんにゃくモード停止中...",
                       label="status_state")

        def _stop_in_background():
            global _konnyaku_running
            try:
                if _konnyaku_system is not None:
                    _konnyaku_system.stop_all()
            except Exception as e:
                print(f"[ERROR] こんにゃく停止失敗: {e}", flush=True)
            finally:
                _konnyaku_running = False
                try:
                    _gui_set_label(TAG_KONNYAKU_START_BTN, "開始",
                                   name="konnyaku_start_btn", enabled=True)
                except Exception:
                    pass
                if dpg.does_item_exist(TAG_STATUS_STATE):
                    try:
                        _gui_set_value(TAG_STATUS_STATE, "停止しました",
                                       label="status_state")
                    except Exception:
                        pass

        threading.Thread(
            target=_stop_in_background,
            daemon=True,
            name="KonnyakuStop",
        ).start()
        return

    # ポート競合チェック: 既存の単独モードが稼働中なら起動を拒否
    if _system is not None:
        dpg.set_value(TAG_STATUS_STATE, "既存モード停止後に翻訳こんにゃくモードを開始してください")
        return

    try:
        # 系統 ON/OFF チェック（Issue #43）
        route_a_enabled = bool(
            dpg.get_value(TAG_ROUTE_A_ENABLE)
            if dpg.does_item_exist(TAG_ROUTE_A_ENABLE) else True
        )
        route_b_enabled = bool(
            dpg.get_value(TAG_ROUTE_B_ENABLE)
            if dpg.does_item_exist(TAG_ROUTE_B_ENABLE) else True
        )
        if not route_a_enabled and not route_b_enabled:
            _gui_set_value(TAG_STATUS_STATE, "少なくとも1つの系統を有効にしてください",
                           label="status_state")
            return

        # 常駐モデル: MultiCaptionSystem は既に存在するはず（main() で生成済み）
        # 存在しない場合はフォールバックで生成
        if _konnyaku_system is None:
            _create_konnyaku_system()

        if _konnyaku_system is None:
            # デバイスがない等で生成失敗
            if dpg.does_item_exist(TAG_STATUS_STATE):
                dpg.set_value(TAG_STATUS_STATE, "こんにゃく起動失敗: デバイスが見つかりません")
            return

        # verbose モードが有効なら各 CaptionSystem に反映
        if _verbose_state:
            if _konnyaku_system.route_a_system is not None:
                _konnyaku_system.route_a_system.verbose = True
            if _konnyaku_system.route_b_system is not None:
                _konnyaku_system.route_b_system.verbose = True

        # 開始直前に GUI の最新値で _device_info / target_language を同期
        # (常駐モデルで GUI 変更がコールバック未経由のとき、ここで確実に反映)
        if _konnyaku_system.route_a_system is not None:
            a_label = dpg.get_value(TAG_ROUTE_A_DEVICE_COMBO) if dpg.does_item_exist(TAG_ROUTE_A_DEVICE_COMBO) else ""
            a_dev = next((d for d in _devices if _device_label(d) == a_label), None)
            if a_dev is not None:
                _konnyaku_system.route_a_system._device_info = a_dev
                print(f"[INFO] route_a device synced from GUI: '{a_label}' (index={a_dev.get('index')})", flush=True)
        if _konnyaku_system.route_b_system is not None:
            b_label = dpg.get_value(TAG_ROUTE_B_DEVICE_COMBO) if dpg.does_item_exist(TAG_ROUTE_B_DEVICE_COMBO) else ""
            b_dev = next((d for d in _devices if _device_label(d) == b_label), None)
            if b_dev is not None:
                _konnyaku_system.route_b_system._device_info = b_dev
                print(f"[INFO] route_b device synced from GUI: '{b_label}' (index={b_dev.get('index')})", flush=True)

        # 有効な系統だけ start_route（常駐モデル: start_all でなく個別制御）
        if route_a_enabled and _konnyaku_system.route_a_system is not None:
            _konnyaku_system.start_route("a")
        if route_b_enabled and _konnyaku_system.route_b_system is not None:
            _konnyaku_system.start_route("b")

        _konnyaku_running = True

        if dpg.does_item_exist(TAG_KONNYAKU_START_BTN):
            _gui_set_label(TAG_KONNYAKU_START_BTN, "停止", name="konnyaku_start_btn")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[ERROR] こんにゃくモード起動失敗: {e}", flush=True)
        print(tb, flush=True)
        if dpg.does_item_exist(TAG_STATUS_STATE):
            dpg.set_value(TAG_STATUS_STATE, f"こんにゃく起動失敗: {type(e).__name__}: {e}")
        try:
            from datetime import datetime as _dt
            log_path = _Path(_config.get("output", {}).get("log_dir", ".")) / \
                       f"crash_konnyaku_{_dt.now().strftime('%Y%m%d-%H%M%S')}.log"
            log_path.write_text(f"Exception: {e}\n\n{tb}\n", encoding="utf-8")
            print(f"[ERROR] 詳細ログ: {log_path}", flush=True)
        except Exception:
            pass
        _konnyaku_running = False


def _on_verbose_toggle():
    """Verbose ボタン押下: ON/OFF をトグルし、稼働中のシステムにも反映。"""
    global _verbose_state
    _verbose_state = not _verbose_state
    if _system:
        _system.verbose = _verbose_state
    if dpg.does_item_exist(TAG_VERBOSE_BTN):
        dpg.configure_item(TAG_VERBOSE_BTN,
                           label="Verbose ●" if _verbose_state else "Verbose ○")
    _save_settings()


# ---------------------------------------------------------------------------
# ロード進捗バー
# ---------------------------------------------------------------------------

def _start_loading(model_name: str):
    global _loading_active, _loading_start_time, _loading_phase, _loading_model_name
    import time as _t
    _loading_model_name = model_name
    _loading_start_time = _t.time()
    _loading_phase = "初期化中..."
    _loading_active = True
    if dpg.does_item_exist(TAG_PROGRESS_BAR):
        dpg.configure_item(TAG_PROGRESS_BAR, show=True)
        dpg.set_value(TAG_PROGRESS_BAR, 0.0)
    if dpg.does_item_exist(TAG_PROGRESS_TEXT):
        dpg.configure_item(TAG_PROGRESS_TEXT, show=True)
        dpg.set_value(TAG_PROGRESS_TEXT, f"モデル読み込み中 ({model_name}): {_loading_phase}")


def _end_loading():
    global _loading_active
    _loading_active = False
    if dpg.does_item_exist(TAG_PROGRESS_BAR):
        dpg.configure_item(TAG_PROGRESS_BAR, show=False)
    if dpg.does_item_exist(TAG_PROGRESS_TEXT):
        dpg.configure_item(TAG_PROGRESS_TEXT, show=False)


def _model_dir_size_mb(model_name: str) -> float:
    """models/huggingface のモデルディレクトリ合計サイズを MB で返す。"""
    base = _SCRIPT_DIR / "models" / "huggingface" / "hub" / f"models--Systran--faster-whisper-{model_name}"
    if not base.exists():
        return 0.0
    total = 0
    for p in base.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except Exception:
            pass
    return total / (1024 * 1024)


def _update_loading_progress():
    """レンダリングループから呼ばれる。ロード中なら進捗を更新する。"""
    global _loading_phase
    if not _loading_active:
        return
    import time as _t
    elapsed = _t.time() - _loading_start_time
    size_mb = _model_dir_size_mb(_loading_model_name)
    expected_mb = _MODEL_EXPECTED_MB.get(_loading_model_name, 500)

    # ダウンロード進捗を検出（サイズが目標の95%未満ならダウンロード中とみなす）
    if size_mb < expected_mb * 0.95:
        pct = min(0.95, size_mb / expected_mb)
        _loading_phase = f"ダウンロード中 {size_mb:.0f}/{expected_mb} MB"
    else:
        # ダウンロード済み → モデルをメモリへロード中（時間ベース）
        est_total = 40.0 if _loading_model_name == "medium" else 15.0
        pct = min(0.95, elapsed / est_total)
        _loading_phase = f"メモリ展開中... {elapsed:.1f}秒経過"

    if dpg.does_item_exist(TAG_PROGRESS_BAR):
        dpg.set_value(TAG_PROGRESS_BAR, pct)
    if dpg.does_item_exist(TAG_PROGRESS_TEXT):
        dpg.set_value(TAG_PROGRESS_TEXT, f"モデル読み込み中 ({_loading_model_name}): {_loading_phase}")


# ---------------------------------------------------------------------------
# コスト警告モーダル
# ---------------------------------------------------------------------------

def _show_cost_warning_modal(threshold: float) -> None:
    """コスト警告モーダルを表示する（dpg メインスレッド内から呼ぶこと）。"""
    tag = f"cost_warn_modal_{int(threshold * 1000)}"
    if dpg.does_item_exist(tag):
        return  # 既に表示中

    def _close(sender, app_data, user_data):
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)

    with dpg.window(
        label="コスト警告",
        tag=tag,
        modal=True,
        width=380,
        height=120,
        no_resize=True,
        no_move=True,
        pos=(290, 280),
    ):
        dpg.add_text(f"想定コストが ${threshold:.2f} を超えました。")
        dpg.add_text("使い過ぎにご注意ください。")
        dpg.add_separator()
        dpg.add_button(label="OK", width=340, callback=_close)


def _check_pending_cost_warnings() -> None:
    """メインループから毎フレーム呼ぶ。保留中の警告モーダルを描画する。"""
    global _pending_cost_warnings
    with _cost_warning_lock:
        warnings = list(_pending_cost_warnings)
        _pending_cost_warnings.clear()
    for threshold in warnings:
        _show_cost_warning_modal(threshold)


def _enqueue_cost_warning(threshold: float) -> None:
    """スレッドセーフに警告フラグをキューに積む（コールバックから呼ぶ）。"""
    with _cost_warning_lock:
        _pending_cost_warnings.append(threshold)


# ---------------------------------------------------------------------------
# GUI キューコマンド処理
# ---------------------------------------------------------------------------

def _enqueue(cmd: str, **kwargs):
    _gui_queue.put({"cmd": cmd, **kwargs})


# =============================================================================
# UI 変化ログユーティリティ (Issue #66)
# OLD->NEW を [GUI] プレフィックスで記録して、UI 動作の検証を容易にする
# =============================================================================

def _gui_set_value(tag: str, new_value, label: str | None = None) -> None:
    """dpg.set_value のラッパー。OLD->NEW を console に記録。

    Parameters
    ----------
    tag      : ウィジェットタグ
    new_value: 新しい値
    label    : ログ表記名（省略時は tag をそのまま使う）
    """
    name = label if label is not None else tag
    try:
        old = dpg.get_value(tag) if dpg.does_item_exist(tag) else None
    except Exception:
        old = None
    if old != new_value:
        print(f"[GUI] {name}: {old!r} -> {new_value!r}", flush=True)
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, new_value)


def _gui_set_label(tag: str, new_label: str, name: str | None = None,
                   **other_kwargs) -> None:
    """dpg.configure_item でラベル変更時 OLD->NEW を console に記録。"""
    display_name = name if name is not None else tag
    try:
        old = (dpg.get_item_configuration(tag).get("label")
               if dpg.does_item_exist(tag) else None)
    except Exception:
        old = None
    if old != new_label:
        print(f"[GUI] {display_name} label: {old!r} -> {new_label!r}", flush=True)
    if dpg.does_item_exist(tag):
        dpg.configure_item(tag, label=new_label, **other_kwargs)


def _classify_preload_cache(cached_system, cached_key, requested_key) -> tuple[str, object | None]:
    """プリロードキャッシュを分類する純関数（テスト可能性のために切り出し）。

    Returns
    -------
    ("ok", system)    : 再利用可能なキャッシュあり（呼び出し側で キャッシュ消費 + 起動）
    ("stale", system) : prepare() 失敗で _recorder=None の状態（呼び出し側で shutdown 必要）
    ("miss", None)    : キャッシュなし、キー不一致、または cached_system が None
    """
    if cached_system is None or cached_key != requested_key:
        return "miss", None
    if getattr(cached_system, "_recorder", None) is not None:
        return "ok", cached_system
    return "stale", cached_system


def _trigger_preload():
    """選択中のモデル・デバイスでバックグラウンドプリロードを開始する。
    既に同じキーでプリロード済み/進行中なら何もしない。"""
    global _preloaded_system, _preload_key

    if not dpg.does_item_exist(TAG_DEVICE_COMBO):
        return
    device_label = dpg.get_value(TAG_DEVICE_COMBO)
    device_info = next((d for d in _devices if _device_label(d) == device_label), None)
    if device_info is None:
        return
    model_name = dpg.get_value(TAG_MODEL_COMBO)
    key = (model_name, device_info["index"])

    with _preload_lock:
        if _preload_key == key:
            return  # 既に同キーでプリロード済みまたは進行中
        old = _preloaded_system
        _preloaded_system = None
        _preload_key = key  # 進行中フラグとして先に書く

    # 古いプリロードを廃棄
    if old is not None:
        try:
            old.shutdown()
        except Exception:
            pass

    # プリロード用 CaptionSystem（コールバックなし）
    cfg = {**_config}
    cfg.setdefault("vad", {})["silero_sensitivity"] = dpg.get_value(TAG_VAD_SENSITIVITY)
    cfg.setdefault("vad", {})["post_speech_silence_duration"] = dpg.get_value(TAG_VAD_SILENCE)
    system = CaptionSystem(cfg, device_info, model_name)

    def _do_prepare():
        global _preloaded_system, _preload_key
        # Issue #3 #2: 早期キャンセルチェック（thread 起動から prepare 開始までの間に
        # ユーザーがコンボを変更してキーが変わった場合は重い prepare() を呼ばずに撤退）
        with _preload_lock:
            if _preload_key != key:
                return
        print(f"[INFO] Preloading Whisper {model_name} ...")
        prepare_failed = False
        try:
            system.prepare()
        except Exception as e:
            prepare_failed = True
            print(f"[ERROR] Preload 失敗 {model_name}: {e}", flush=True)
        # Issue #3 #3: prepare 失敗時に _preloaded_system に格納せず、確実に shutdown する
        store_to_cache = False
        with _preload_lock:
            if (not prepare_failed
                    and _preload_key == key
                    and not system._stop_event.is_set()
                    and system._recorder is not None):
                _preloaded_system = system
                store_to_cache = True
                print(f"[INFO] Preload done: {model_name}")
            else:
                # 失敗・キャンセル時はキーをリセット（次のプリロード/起動で stale 判定されないよう）
                if _preload_key == key:
                    _preload_key = None
        if not store_to_cache:
            # ロック外で shutdown（重い操作のため、ロック保持時間を最小化）
            try:
                system.shutdown()
            except Exception:
                pass

    threading.Thread(target=_do_prepare, daemon=True).start()


def _drain_queue():
    """レンダリングループから毎フレーム呼ぶ。キューを処理して GUI を更新する。"""
    while not _gui_queue.empty():
        try:
            item = _gui_queue.get_nowait()
        except queue.Empty:
            break

        cmd = item.get("cmd")

        if cmd == "append_log":
            _append_log_item(item["ts"], item["original"], item["translated"])

        elif cmd == "set_status":
            dpg.set_value(TAG_STATUS_STATE, item["text"])

        elif cmd == "set_stt":
            dpg.set_value(TAG_STATUS_STT, "認識 ●" if item["busy"] else "認識 ○")

        elif cmd == "set_trl":
            dpg.set_value(TAG_STATUS_TRL, "翻訳 ●" if item["busy"] else "翻訳 ○")

        elif cmd == "set_running":
            global _is_running
            _is_running = item["value"]
            if _is_running:
                _end_loading()
                dpg.configure_item(TAG_START_BTN, label="停止")
                dpg.set_value(TAG_STATUS_STATE, "● 録音中")
            else:
                _end_loading()
                dpg.configure_item(TAG_START_BTN, label="開始")
                dpg.set_value(TAG_STATUS_STATE, "■ 待機中")

        elif cmd == "stop_system":
            _do_stop()

        elif cmd == "start_system":
            _do_start(device_index=item.get("device_index"), model=item.get("model"))

        elif cmd == "update_ptt_visual":
            _update_ptt_visual_feedback()


def _clear_log():
    """字幕ログ（GUI・メモリ両方）をクリア。"""
    _log_entries.clear()
    if dpg.does_item_exist(TAG_LOG_GROUP):
        dpg.delete_item(TAG_LOG_GROUP, children_only=True)


def _append_log_item(ts: str, original: str, translated: str):
    # 追加前に「最下部付近にいるか」を確認（ユーザーがスクロールバックしていれば False）
    scroll_y = dpg.get_y_scroll(TAG_LOG_SCROLL)
    scroll_max = dpg.get_y_scroll_max(TAG_LOG_SCROLL)
    was_at_bottom = scroll_max <= 0 or scroll_y >= scroll_max - 20

    # 原文・翻訳の片方だけが来た場合（OpenAI Realtime の独立ストリーム）と
    # 両方ペアで来た場合（Whisper モード等）で表示形式を切り替える
    with dpg.group(parent=TAG_LOG_GROUP):
        if original and translated:
            dpg.add_text(f"[{ts}] EN: {original}", wrap=860)
            dpg.add_text(f"             JP: {translated}", wrap=860)
        elif original:
            dpg.add_text(f"[{ts}] EN: {original}", wrap=860)
        elif translated:
            dpg.add_text(f"[{ts}] JP: {translated}", wrap=860)
        dpg.add_separator()

    if was_at_bottom:
        # dpg は -1.0 を渡すと「常に末尾」スクロールになる（レイアウト完了後に追従）
        dpg.set_y_scroll(TAG_LOG_SCROLL, -1.0)


# ---------------------------------------------------------------------------
# CaptionSystem の起動・停止
# ---------------------------------------------------------------------------

def _proceed_start(device_info: dict, model_name: str, selected_trans: str):
    """コスト確認後（またはコスト確認不要時）に実際に CaptionSystem を起動する。"""
    global _system, _system_thread, _is_running, _preloaded_system, _preload_key

    def on_result(original: str, translated: str):
        ts = datetime.now().strftime("%H:%M:%S")
        _log_entries.append({"ts": ts, "original": original, "translated": translated})
        if len(_log_entries) > 200:
            _log_entries.pop(0)
        _enqueue("append_log", ts=ts, original=original, translated=translated)
        # Realtime モードは片側だけ来る独立ストリームなので、空文字行はコンソールにも出さない
        if original:
            print(f"[{ts}] EN: {original}")
        if translated:
            print(f"       JP: {translated}")

    _config.setdefault("vad", {})["silero_sensitivity"] = dpg.get_value(TAG_VAD_SENSITIVITY)
    _config.setdefault("vad", {})["post_speech_silence_duration"] = dpg.get_value(TAG_VAD_SILENCE)

    gain_mode = dpg.get_value(TAG_GAIN_MODE)
    gain_value = float(dpg.get_value(TAG_GAIN_SLIDER))

    def on_ready():
        _enqueue("set_running", value=True)

    def on_whisper_busy(busy: bool):
        _enqueue("set_stt", busy=busy)

    def on_trans_busy(busy: bool):
        _enqueue("set_trl", busy=busy)

    # 出力デバイスのインデックスを取得（コンボボックスで選択されている場合）
    output_device_index: int | None = None
    if dpg.does_item_exist(TAG_OUTPUT_DEVICE_COMBO):
        output_label = dpg.get_value(TAG_OUTPUT_DEVICE_COMBO)
        if output_label and output_label != "(なし)":
            _host_api_sel = _config.get("audio", {}).get("host_api", "wasapi")
            output_devices = list_audio_devices(device_type="output", host_api=_host_api_sel)
            matched = find_device_by_name(output_label, output_devices)
            if matched:
                output_device_index = matched["index"]

    # Realtime モードの場合はプリロードキャッシュを使わない（Whisper 不要なので不要）
    if selected_trans == "openai-realtime":
        _system = CaptionSystem(_config, device_info, model_name,
                                on_result=on_result, on_ready=on_ready,
                                on_whisper_busy=on_whisper_busy,
                                on_trans_busy=on_trans_busy,
                                output_device_index=output_device_index)
        # コスト警告コールバックを設定（スレッドセーフにフラグを立てる）
        _system._on_cost_warning_cb = _enqueue_cost_warning
        # コスト上限到達時の GUI 通知（ステータス表示）
        _system._on_cost_status = lambda msg: _enqueue("set_status", text=f"■ {msg}")
        loading = False
    else:
        # プリロード済みのシステムがあれば再利用
        # Issue #3 #3: プリロード失敗（_recorder=None）の参照は明示的に shutdown して廃棄する
        key = (model_name, device_info["index"])
        cached = None
        stale_preload = None
        with _preload_lock:
            status, candidate = _classify_preload_cache(_preloaded_system, _preload_key, key)
            if status == "ok":
                cached = candidate
                _preloaded_system = None
                _preload_key = None
            elif status == "stale":
                stale_preload = candidate
                _preloaded_system = None
                _preload_key = None

        # ロック外で stale を廃棄（shutdown は重いのでロック保持時間を最小化）
        if stale_preload is not None:
            try:
                stale_preload.shutdown()
            except Exception:
                pass

        if cached is not None:
            _system = cached
            _system._on_result = on_result
            _system._on_ready = on_ready
            _system._on_whisper_busy = on_whisper_busy
            _system._on_trans_busy = on_trans_busy
            _system._stop_event.clear()
            # 翻訳エンジンを GUI の選択に合わせて更新
            from main import TranslationService
            _system._config.setdefault("translation", {})["translation_model"] = selected_trans
            _system._translator = TranslationService(_system._config)
            # VAD を GUI の値に更新
            _system._config.setdefault("vad", {})["silero_sensitivity"] = dpg.get_value(TAG_VAD_SENSITIVITY)
            _system._config.setdefault("vad", {})["post_speech_silence_duration"] = dpg.get_value(TAG_VAD_SILENCE)
            try:
                _system._recorder.silero_sensitivity = dpg.get_value(TAG_VAD_SENSITIVITY)
                _system._recorder.post_speech_silence_duration = dpg.get_value(TAG_VAD_SILENCE)
            except Exception:
                pass
            loading = False
        else:
            _system = CaptionSystem(_config, device_info, model_name,
                                    on_result=on_result, on_ready=on_ready,
                                    on_whisper_busy=on_whisper_busy,
                                    on_trans_busy=on_trans_busy)
            loading = True

    _system.gain_mode = gain_mode
    _system.manual_gain = gain_value
    _system.verbose = _verbose_state

    dpg.configure_item(TAG_START_BTN, label="停止")
    if loading:
        if selected_trans == "openai-realtime":
            _enqueue("set_status", text="▸ OpenAI Realtime 接続中...")
        else:
            _enqueue("set_status", text=f"▸ モデル読み込み中: {model_name} ...")
            print(f"[INFO] Whisper {model_name} model loading, please wait...")
            _start_loading(model_name)
    else:
        _enqueue("set_status", text="▸ 起動中...")

    def run_in_thread():
        asyncio.run(_system.run())
        _enqueue("set_running", value=False)

    _save_settings()
    _system_thread = threading.Thread(target=run_in_thread, daemon=True)
    _system_thread.start()


def _do_start(device_index: int | None = None, model: str | None = None):
    global _is_running

    if _is_running:
        return

    # device_index が指定されていればそちらを優先、なければコンボボックスの選択を使う
    if device_index is not None:
        device_info = next((d for d in _devices if d["index"] == device_index), None)
        if device_info is not None:
            dpg.set_value(TAG_DEVICE_COMBO, _device_label(device_info))
    else:
        device_label = dpg.get_value(TAG_DEVICE_COMBO)
        device_info = next((d for d in _devices if _device_label(d) == device_label), None)
    if device_info is None:
        return

    if model in ("small", "medium"):
        dpg.set_value(TAG_MODEL_COMBO, model)

    model_name = dpg.get_value(TAG_MODEL_COMBO)

    # GUI の設定を config に反映（ラベル -> 内部キーに変換して保存）
    selected_trans_label = dpg.get_value(TAG_TRANS_COMBO)
    selected_trans = _trans_label_to_key(selected_trans_label)
    if selected_trans in ("openai", "deepl", "openai-realtime"):
        _config.setdefault("translation", {})["translation_model"] = selected_trans

    # openai-realtime モードの場合はコスト警告ダイアログを表示
    if selected_trans == "openai-realtime":
        def _on_realtime_confirm(sender, app_data, user_data):
            if dpg.does_item_exist("realtime_cost_modal"):
                dpg.delete_item("realtime_cost_modal")
            _proceed_start(device_info, model_name, selected_trans)

        def _on_realtime_cancel(sender, app_data, user_data):
            if dpg.does_item_exist("realtime_cost_modal"):
                dpg.delete_item("realtime_cost_modal")
            _enqueue("set_status", text="■ 待機中")
            dpg.configure_item(TAG_START_BTN, label="開始")

        with dpg.window(
            label="コスト確認",
            tag="realtime_cost_modal",
            modal=True,
            width=420,
            height=160,
            no_resize=True,
            no_move=True,
            pos=(270, 250),
        ):
            dpg.add_text("OpenAI Realtime モードは従量課金制です。")
            dpg.add_text("コスト: USD 0.034/分（約 USD 2.04/時間）")
            dpg.add_text("Whisper local + gpt-4o-mini より大幅に高コストです。")
            dpg.add_separator()
            with dpg.group(horizontal=True):
                dpg.add_button(label="はい（続ける）", width=180,
                               callback=_on_realtime_confirm)
                dpg.add_button(label="いいえ（キャンセル）", width=180,
                               callback=_on_realtime_cancel)
        return

    _proceed_start(device_info, model_name, selected_trans)


def _do_stop():
    global _system, _is_running
    if _system is not None:
        _system.shutdown()
        _system = None
    _is_running = False
    dpg.configure_item(TAG_START_BTN, label="開始")
    dpg.set_value(TAG_STATUS_STATE, "■ 待機中")
    # プリロード機能は一時無効化（調査中）
    # threading.Thread(target=_trigger_preload, daemon=True).start()


def _on_start_stop_click():
    if _is_running:
        _do_stop()
    else:
        _do_start()


# ---------------------------------------------------------------------------
# デバイス一覧ヘルパー
# ---------------------------------------------------------------------------

def _device_label(d: dict) -> str:
    name = d["name"].replace(" [Loopback]", "")
    if d.get("isLoopback"):
        return f"{name} [Loopback]"
    return name


# ---------------------------------------------------------------------------
# RPC サーバー
# ---------------------------------------------------------------------------

class _RPCHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # アクセスログ抑制

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            ws_clients = _system._broadcaster.client_count if _system else 0
            device_label = dpg.get_value(TAG_DEVICE_COMBO) if dpg.does_item_exist(TAG_DEVICE_COMBO) else ""
            model = dpg.get_value(TAG_MODEL_COMBO) if dpg.does_item_exist(TAG_MODEL_COMBO) else ""
            peak = _system.audio_peak if _system else 0
            chunks = _system.audio_chunks_per_sec if _system else 0
            self._send_json({
                "state": "running" if _is_running else "stopped",
                "device": device_label,
                "model": model,
                "ws_clients": ws_clients,
                "audio_peak": peak,
                "audio_peak_pct": peak * 100 // 32767,
                "audio_chunks_per_sec": chunks,
            })

        elif self.path == "/api/log":
            self._send_json(_log_entries[-100:])

        elif self.path == "/api/devices":
            self._send_json(_devices)

        elif self.path == "/api/audio":
            peak = _system.audio_peak if _system else 0
            chunks = _system.audio_chunks_per_sec if _system else 0
            gain = _system.effective_gain if _system else 1.0
            mode = _system.gain_mode if _system else "off"
            self._send_json({
                "peak": peak,
                "peak_pct": peak * 100 // 32767,
                "chunks_per_sec": chunks,
                "gain": round(gain, 2),
                "gain_mode": mode,
            })

        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path == "/api/stop":
            _enqueue("stop_system")
            self._send_json({"ok": True})
        elif self.path == "/api/start":
            body = {}
            length = int(self.headers.get("Content-Length", 0))
            if length:
                try:
                    body = json.loads(self.rfile.read(length))
                except Exception:
                    pass
            _enqueue("start_system", device_index=body.get("device_index"),
                     model=body.get("model"))
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, status=404)


def _start_rpc_server(port: int):
    global _rpc_server
    _rpc_server = HTTPServer(("localhost", port), _RPCHandler)
    t = threading.Thread(target=_rpc_server.serve_forever, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# GUI 構築
# ---------------------------------------------------------------------------

# 翻訳エンジン: 内部キー -> 表示ラベル のマッピング
_TRANS_MODEL_LABELS = {
    "openai": "OpenAI (gpt-4o-mini)",
    "deepl": "DeepL",
    "openai-realtime": "OpenAI Realtime",
}
# 表示ラベル -> 内部キー の逆引き
_TRANS_LABEL_TO_KEY = {v: k for k, v in _TRANS_MODEL_LABELS.items()}


def _trans_key_to_label(key: str) -> str:
    """内部キーを表示ラベルに変換する。未知のキーはそのまま返す。"""
    return _TRANS_MODEL_LABELS.get(key, key)


def _trans_label_to_key(label: str) -> str:
    """表示ラベルを内部キーに変換する。未知のラベルはそのまま返す。"""
    return _TRANS_LABEL_TO_KEY.get(label, label)


def _looks_like_openai_key(key: str) -> bool:
    """OpenAI API キーらしい形式かを判定する。

    プレースホルダー文字列が将来変わっても引っかからないよう、
    プレフィックス（sk-）と最低長（20+）でチェックする。
    """
    if not key or len(key) < 20:
        return False
    lowered = key.lower()
    # 既知のプレースホルダー文字列を除外（互換性のため）
    if "xxx" in lowered or lowered.startswith("your-") or "placeholder" in lowered:
        return False
    return key.startswith("sk-")


def _looks_like_deepl_key(key: str) -> bool:
    """DeepL API キーらしい形式かを判定する（free/pro tier 両対応）。

    DeepL の鍵は UUID 形式（8-4-4-4-12）+ オプションの :fx サフィックスだが、
    厳格な正規表現は将来の形式変更で破綻するため最低長＋プレースホルダー除外で判定する。
    """
    if not key or len(key) < 30:
        return False
    lowered = key.lower()
    if "xxx" in lowered or lowered.startswith("your-") or "placeholder" in lowered:
        return False
    return True


def _available_trans_models(cfg: dict) -> list[str]:
    """有効な API キーが設定されている翻訳エンジンの表示ラベル一覧を返す。"""
    result = []
    # decode_api_key 経由で b64: 形式にも対応
    openai_key = decode_api_key(cfg.get("openai", {}).get("api_key", ""))
    if _looks_like_openai_key(openai_key):
        result.append(_trans_key_to_label("openai"))
        result.append(_trans_key_to_label("openai-realtime"))
    deepl_key = decode_api_key(cfg.get("deepl", {}).get("api_key", ""))
    if _looks_like_deepl_key(deepl_key):
        result.append(_trans_key_to_label("deepl"))
    return result


_font_main: int | None = None    # 日本語テキスト用（Meiryo 等）
_font_emoji: int | None = None   # アイコン用（Segoe UI Emoji）


def _load_fonts(size: int = 16):
    global _font_main, _font_emoji

    jp_candidates = [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
    ]
    emoji_path = "C:/Windows/Fonts/seguiemj.ttf"

    jp_font_path = next((p for p in jp_candidates if _Path(p).exists()), None)

    with dpg.font_registry():
        if jp_font_path:
            # dearpygui 2.x では文字範囲は自動（add_font_range_hint は no-op）
            _font_main = dpg.add_font(jp_font_path, size)
        _font_emoji = None


def _build_gui():
    global _dpg_ready
    dpg.create_context()
    _dpg_ready = True

    _load_fonts(16)
    if _font_main:
        dpg.bind_font(_font_main)  # 日本語テキストをデフォルトに

    # レベルメーター色テーマ（progress bar の塗りを 緑/黄/赤 に切替）
    with dpg.theme(tag=TAG_LEVEL_THEME_GREEN):
        with dpg.theme_component(dpg.mvProgressBar):
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, (60, 180, 75))
    with dpg.theme(tag=TAG_LEVEL_THEME_YELLOW):
        with dpg.theme_component(dpg.mvProgressBar):
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, (240, 200, 40))
    with dpg.theme(tag=TAG_LEVEL_THEME_RED):
        with dpg.theme_component(dpg.mvProgressBar):
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, (230, 60, 60))

    # viewport title は Windows API 経由で ANSI 変換されるため ASCII で設定し、
    # 表示後に Win32 API (SetWindowTextW) で UTF-16 に書き換える
    dpg.create_viewport(title="Realtime Caption", width=960, height=680, resizable=True)
    dpg.setup_dearpygui()

    rpc_port = _config.get("rpc", {}).get("port", 8767)
    saved = _load_settings()
    route_a_saved = saved.get("route_a", {})
    route_b_saved = saved.get("route_b", {})

    # 起動時: config.yaml からデコード済みのキーを input_text の default_value に設定
    # パスワードモードで表示するため、実際のキーを初期表示する（空なら空のまま）
    _init_openai_key = decode_api_key(_config.get("openai", {}).get("api_key", ""))
    _init_deepl_key = decode_api_key(_config.get("deepl", {}).get("api_key", ""))

    default_model = saved.get("model") or _config.get("whisper", {}).get("model", "small")
    trans_models = _available_trans_models(_config)
    # settings.json には内部キーで保存されている。表示ラベルに変換してコンボボックスに設定する。
    _saved_trans_key = saved.get("trans") or _config.get("translation", {}).get("translation_model", "openai").lower()
    default_trans = _trans_key_to_label(_saved_trans_key)
    if default_trans not in trans_models:
        default_trans = trans_models[0] if trans_models else _trans_key_to_label("openai")
    vad_cfg = _config.get("vad", {})
    default_sensitivity = saved.get("vad_sensitivity", vad_cfg.get("silero_sensitivity", VAD_DEFAULT_SENSITIVITY))
    default_silence = saved.get("vad_silence", vad_cfg.get("post_speech_silence_duration", VAD_DEFAULT_SILENCE))
    default_gain_mode = saved.get("gain_mode", GAIN_DEFAULT_MODE)
    if default_gain_mode not in ("off", "manual", "auto"):
        default_gain_mode = GAIN_DEFAULT_MODE
    default_gain_value = float(saved.get("gain_value", GAIN_DEFAULT_VALUE))
    global _verbose_state
    _verbose_state = bool(saved.get("verbose", False))

    device_labels = [_device_label(d) for d in _devices]
    saved_device = saved.get("device", "")
    default_device = (
        saved_device if saved_device in device_labels
        else next((lbl for lbl in device_labels if "[Loopback]" in lbl),
                  device_labels[0] if device_labels else "")
    )

    # 出力デバイス一覧（VB-CABLE 等）
    _host_api_cfg = _config.get("audio", {}).get("host_api", "wasapi")
    _output_devices = list_audio_devices(device_type="output", host_api=_host_api_cfg)
    output_device_labels = ["(なし)"] + [d["name"] for d in _output_devices]
    saved_output_device = saved.get("output_device", "")
    default_output_device = (
        saved_output_device if saved_output_device in output_device_labels
        else "(なし)"
    )

    with dpg.window(tag="main_window", no_title_bar=True, no_resize=True,
                    no_move=True, no_scrollbar=True):

        # ログクリア・Verbose ボタン
        with dpg.group(horizontal=True):
            dpg.add_button(label="ログクリア", width=100, callback=_clear_log)
            dpg.add_button(
                tag=TAG_VERBOSE_BTN,
                label="Verbose ●" if _verbose_state else "Verbose ○",
                width=110, callback=_on_verbose_toggle,
            )

        # --- 詳細設定（初期状態は折りたたみ） ---
        with dpg.collapsing_header(label="詳細設定", default_open=False):

            # --- API キー設定（全モード共通：OpenAI は常時表示） ---
            dpg.add_text("API キー設定（b64 難読化して config.yaml に保存）")
            with dpg.group(horizontal=True):
                dpg.add_text("OpenAI:  ")
                dpg.add_input_text(
                    tag=TAG_OPENAI_KEY_INPUT,
                    default_value=_init_openai_key,
                    password=True,
                    width=500,
                    hint="sk-... (空白のままなら変更しない)",
                )
                dpg.add_button(
                    tag=TAG_KEY_SHOW_OPENAI,
                    label="表示",
                    width=70,
                    callback=_on_key_show_toggle,
                    user_data=TAG_OPENAI_KEY_INPUT,
                )

            # DeepL キーは openai-realtime モード時のみ非表示
            with dpg.group(tag=TAG_DEEPL_KEY_GROUP, horizontal=False):
                with dpg.group(horizontal=True):
                    dpg.add_text("DeepL:   ")
                    dpg.add_input_text(
                        tag=TAG_DEEPL_KEY_INPUT,
                        default_value=_init_deepl_key,
                        password=True,
                        width=500,
                        hint="xxxxxxxx-xxxx-... (空白のままなら変更しない)",
                    )
                    dpg.add_button(
                        tag=TAG_KEY_SHOW_DEEPL,
                        label="表示",
                        width=70,
                        callback=_on_key_show_toggle,
                        user_data=TAG_DEEPL_KEY_INPUT,
                    )

            with dpg.group(horizontal=True):
                dpg.add_button(
                    tag=TAG_KEY_SAVE_BTN,
                    label="保存",
                    width=80,
                    callback=_on_save_api_keys,
                )
                dpg.add_text("", tag=TAG_KEY_STATUS)

            # issue #80: クォータ確認ボタン（クォータ超過時以外でもいつでも確認可能）
            dpg.add_button(
                tag=TAG_QUOTA_WEB_BTN,
                label="クォータを Web で確認",
                width=200,
                callback=_on_open_quota_page,
            )

            dpg.add_separator()

            # --- Host API フィルタ（全モード共通） ---
            with dpg.group(horizontal=True):
                dpg.add_text("デバイスフィルタ:")
                _host_api_items = ["wasapi", "all", "mme", "directsound"]
                _host_api_default = _config.get("audio", {}).get("host_api", "wasapi")
                if _host_api_default not in _host_api_items:
                    _host_api_default = "wasapi"
                dpg.add_combo(
                    tag=TAG_HOST_API_COMBO,
                    items=_host_api_items,
                    default_value=_host_api_default,
                    width=140,
                    callback=_on_host_api_change,
                )
                dpg.add_text("  ※ 同名デバイスが重複する場合は「all」に切り替え")

            dpg.add_separator()

            # --- PTT 設定グループ（issue #82 / ptt-mode-design.md F-5） ---
            _ptt_saved_settings = _load_ptt_settings(saved)
            _ptt_default_enabled = _ptt_saved_settings["ptt_enabled"]
            _ptt_default_hotkey = _ptt_saved_settings["ptt_hotkey"]

            with dpg.group(tag=TAG_PTT_SECTION, horizontal=False):
                dpg.add_text("PTT (Push-to-Talk) 設定  系統2を F8 押下中のみ稼働させてコストを削減")
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(
                        tag=TAG_PTT_ENABLED,
                        label="PTT モード有効",
                        default_value=_ptt_default_enabled,
                        callback=lambda s, a, u: _on_ptt_enabled_change(enabled=bool(a)),
                    )
                    dpg.add_text("  ※ ON にするとキー押下中のみ系統2が起動します")
                with dpg.group(horizontal=True):
                    dpg.add_text("ホットキー:")
                    dpg.add_input_text(
                        tag=TAG_PTT_HOTKEY,
                        default_value=_ptt_default_hotkey,
                        width=80,
                        hint="f8",
                        on_enter=True,
                        callback=lambda s, a, u: _on_ptt_hotkey_change(new_hotkey=str(a).strip()),
                    )
                    dpg.add_text("  (Enter で確定。例: f8, f9, ctrl+shift+t)")

        dpg.add_separator()

        # --- 翻訳こんにゃくモード（メインコンテンツ） ---
        # 旧: collapsing_header（折りたたみ）→ 常時展開に昇格（Issue #38 GUI 統一）
        dpg.add_text("双方向同時翻訳  [系統1] 相手→自分（聞き取り字幕） / [系統2] 自分→相手（同時通訳）")
        with dpg.group(tag=TAG_KONNYAKU_SECTION):
            # 開始/停止ボタン + 系統一括
            with dpg.group(horizontal=True):
                dpg.add_button(
                    tag=TAG_KONNYAKU_START_BTN,
                    label="開始",
                    width=130,
                    callback=_on_konnyaku_start_stop_click,
                    enabled=bool(trans_models),
                )
                dpg.add_text("  系統一括:")
                dpg.add_button(
                    label="両方 ON",
                    width=90,
                    callback=_on_both_routes_on,
                )
                dpg.add_button(
                    label="両方 OFF",
                    width=90,
                    callback=_on_both_routes_off,
                )

            dpg.add_separator()

            _lang_display_names = get_language_display_names()

            # --- 系統1: 相手→自分（聞き取り字幕）経路 ---
            _route_a_default_loopback = next(
                (lbl for lbl in device_labels if "[Loopback]" in lbl),
                device_labels[0] if device_labels else "",
            )
            _route_a_saved_device = route_a_saved.get("device", "")
            if _route_a_saved_device and _route_a_saved_device in device_labels:
                _route_a_default_device = _route_a_saved_device
                print(f"[INFO] route_a device restored: '{_route_a_default_device}'", flush=True)
            elif _route_a_saved_device:
                _route_a_default_device = _route_a_default_loopback
                print(f"[WARN] route_a saved device not in list. "
                      f"saved='{_route_a_saved_device}', fallback='{_route_a_default_device}'", flush=True)
                print(f"[WARN] available device_labels: {device_labels}", flush=True)
            else:
                _route_a_default_device = _route_a_default_loopback
                print(f"[INFO] route_a no saved device, using default: '{_route_a_default_device}'", flush=True)
            _route_a_default_lang = route_a_saved.get("lang", _lang_display_names[0] if _lang_display_names else "")
            if _route_a_default_lang not in _lang_display_names:
                _route_a_default_lang = _lang_display_names[0] if _lang_display_names else ""
            _route_a_saved_out_dev = route_a_saved.get("output_device", "(なし)")
            _route_a_default_out_dev = (
                _route_a_saved_out_dev if _route_a_saved_out_dev in output_device_labels
                else "(なし)"
            )
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    tag=TAG_ROUTE_A_ENABLE,
                    label="",
                    default_value=bool(route_a_saved.get("enabled", True)),
                    callback=_on_route_a_enable_change,
                )
                dpg.add_text("【系統1】相手→自分（聞き取り字幕）  You speak, I hear")
            with dpg.group(horizontal=True):
                dpg.add_text("入力デバイス:")
                dpg.add_combo(
                    tag=TAG_ROUTE_A_DEVICE_COMBO,
                    items=device_labels,
                    default_value=_route_a_default_device,
                    width=360,
                    callback=_on_route_a_device_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("入力レベル:")
                dpg.add_progress_bar(
                    tag=TAG_LEVEL_METER_A_IN,
                    default_value=0.0,
                    width=200, overlay="0%",
                )
            with dpg.group(horizontal=True):
                dpg.add_text("翻訳先言語:")
                dpg.add_combo(
                    tag=TAG_ROUTE_A_LANG_COMBO,
                    items=_lang_display_names,
                    default_value=_route_a_default_lang,
                    width=120,
                    callback=lambda s, a, u: _save_settings(),
                )
            with dpg.group(horizontal=True):
                dpg.add_text("音声出力:")
                dpg.add_checkbox(
                    tag=TAG_ROUTE_A_OUTPUT_ENABLE,
                    label="有効",
                    default_value=bool(route_a_saved.get("output_enabled", False)),
                    callback=_on_route_a_output_enable_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("出力デバイス:")
                dpg.add_combo(
                    tag=TAG_ROUTE_A_OUTPUT_DEVICE_COMBO,
                    items=output_device_labels,
                    default_value=_route_a_default_out_dev,
                    width=300,
                    callback=_on_route_a_output_device_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("出力音量:")
                dpg.add_slider_float(
                    tag=TAG_ROUTE_A_OUTPUT_VOLUME,
                    default_value=float(route_a_saved.get("output_volume", 1.0)),
                    min_value=0.0, max_value=2.0,
                    width=200, format="%.2f",
                    callback=_on_route_a_volume_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("出力レベル:")
                dpg.add_progress_bar(
                    tag=TAG_LEVEL_METER_A_OUT,
                    default_value=0.0,
                    width=200, overlay="0%",
                )

            dpg.add_separator()

            # --- 系統2: 自分→相手（同時通訳）経路 ---
            _route_b_default_non_loopback = next(
                (lbl for lbl in device_labels if "[Loopback]" not in lbl),
                device_labels[0] if device_labels else "",
            )
            _route_b_saved_device = route_b_saved.get("device", "")
            if _route_b_saved_device and _route_b_saved_device in device_labels:
                _route_b_default_device = _route_b_saved_device
                print(f"[INFO] route_b device restored: '{_route_b_default_device}'", flush=True)
            elif _route_b_saved_device:
                _route_b_default_device = _route_b_default_non_loopback
                print(f"[WARN] route_b saved device not in list. "
                      f"saved='{_route_b_saved_device}', fallback='{_route_b_default_device}'", flush=True)
            else:
                _route_b_default_device = _route_b_default_non_loopback
                print(f"[INFO] route_b no saved device, using default: '{_route_b_default_device}'", flush=True)
            _route_b_default_lang = route_b_saved.get("lang", _lang_display_names[-1] if _lang_display_names else "")
            if _route_b_default_lang not in _lang_display_names:
                _route_b_default_lang = _lang_display_names[-1] if _lang_display_names else ""
            _route_b_default_cable = next(
                (lbl for lbl in output_device_labels if "cable input" in lbl.lower()),
                "(なし)",
            )
            _route_b_saved_out_dev = route_b_saved.get("output_device", "")
            _route_b_default_out_dev = (
                _route_b_saved_out_dev if _route_b_saved_out_dev in output_device_labels
                else _route_b_default_cable
            )
            with dpg.group(horizontal=True):
                dpg.add_checkbox(
                    tag=TAG_ROUTE_B_ENABLE,
                    label="",
                    default_value=bool(route_b_saved.get("enabled", True)),
                    callback=_on_route_b_enable_change,
                )
                # PTT モード時はラベルを動的に切替（F-5.3 / F-6）
                _route_b_initial_label = (
                    f"【系統2 (PTT: {_ptt_hotkey} 押下中)】自分→相手（同時通訳）  I speak, they hear"
                    if _ptt_enabled
                    else "【系統2】自分→相手（同時通訳）  I speak, they hear"
                )
                dpg.add_text(
                    _route_b_initial_label,
                    tag=TAG_ROUTE_B_LABEL,
                )
                # PTT 押下中ステータス表示（F-6）
                dpg.add_text(
                    "",
                    tag=TAG_PTT_STATUS_LABEL,
                    show=_ptt_enabled,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("入力デバイス:")
                dpg.add_combo(
                    tag=TAG_ROUTE_B_DEVICE_COMBO,
                    items=device_labels,
                    default_value=_route_b_default_device,
                    width=360,
                    callback=_on_route_b_device_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("入力レベル:")
                dpg.add_progress_bar(
                    tag=TAG_LEVEL_METER_B_IN,
                    default_value=0.0,
                    width=200, overlay="0%",
                )
            with dpg.group(horizontal=True):
                dpg.add_text("翻訳先言語:")
                dpg.add_combo(
                    tag=TAG_ROUTE_B_LANG_COMBO,
                    items=_lang_display_names,
                    default_value=_route_b_default_lang,
                    width=120,
                    callback=lambda s, a, u: _save_settings(),
                )
            with dpg.group(horizontal=True):
                dpg.add_text("音声出力:")
                dpg.add_checkbox(
                    tag=TAG_ROUTE_B_OUTPUT_ENABLE,
                    label="有効",
                    default_value=bool(route_b_saved.get("output_enabled", True)),
                    callback=_on_route_b_output_enable_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("出力デバイス:")
                dpg.add_combo(
                    tag=TAG_ROUTE_B_OUTPUT_DEVICE_COMBO,
                    items=output_device_labels,
                    default_value=_route_b_default_out_dev,
                    width=300,
                    callback=_on_route_b_output_device_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("出力音量:")
                dpg.add_slider_float(
                    tag=TAG_ROUTE_B_OUTPUT_VOLUME,
                    default_value=float(route_b_saved.get("output_volume", 1.0)),
                    min_value=0.0, max_value=2.0,
                    width=200, format="%.2f",
                    callback=_on_route_b_volume_change,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("出力レベル:")
                dpg.add_progress_bar(
                    tag=TAG_LEVEL_METER_B_OUT,
                    default_value=0.0,
                    width=200, overlay="0%",
                )

        dpg.add_separator()

        # --- ログエリア（ウィンドウ高さに追従） ---
        # height=-60 はステータスバー + プログレスバー + separator 分の余白
        with dpg.child_window(tag=TAG_LOG_SCROLL, height=-60, border=True,
                               autosize_x=True, no_scrollbar=False):
            with dpg.group(tag=TAG_LOG_GROUP):
                pass

        dpg.add_separator()

        # --- プログレスバー（ロード中のみ表示） ---
        dpg.add_text("", tag=TAG_PROGRESS_TEXT, show=False)
        dpg.add_progress_bar(tag=TAG_PROGRESS_BAR, default_value=0.0,
                             width=-1, show=False)

        # --- ステータスバー ---
        with dpg.group(horizontal=True):
            dpg.add_text("■ 待機中", tag=TAG_STATUS_STATE)
            dpg.add_text("  |  認識 ○", tag=TAG_STATUS_STT)
            dpg.add_text("  翻訳 ○", tag=TAG_STATUS_TRL)
            dpg.add_text("  |  OBS接続:")
            dpg.add_text("0", tag=TAG_STATUS_WS)
            dpg.add_text("  |  RPC:")
            dpg.add_text(f"http://localhost:{rpc_port}", tag=TAG_STATUS_RPC)
            dpg.add_text("", tag=TAG_STATUS_COST)

    dpg.set_primary_window("main_window", True)
    # ステータスバーの文字は Meiryo（グローバル）で統一する。
    # ⏹⏳⚪🔵🟡🔴 等は Meiryo に収録されているためモノクロで表示可能。


# ---------------------------------------------------------------------------
# WS クライアント数の定期更新
# ---------------------------------------------------------------------------

_last_ws_count = -1
_last_level_theme = ""


def _update_ws_status():
    global _last_ws_count
    count = _system._broadcaster.client_count if _system else 0
    if count != _last_ws_count:
        _last_ws_count = count
        dpg.set_value(TAG_STATUS_WS, str(count))


def _update_cost_status():
    """コストモニターの経過時間・想定コストをステータスバーに反映する。"""
    if _system is None or not getattr(_system, "_realtime_mode", False):
        if dpg.does_item_exist(TAG_STATUS_COST):
            dpg.set_value(TAG_STATUS_COST, "")
        return
    monitor = getattr(_system, "_cost_monitor", None)
    if monitor is None:
        return
    elapsed_sec = int(monitor.elapsed_minutes() * 60)
    h = elapsed_sec // 3600
    m = (elapsed_sec % 3600) // 60
    s = elapsed_sec % 60
    cost = monitor.estimated_cost_usd()
    text = f"  |  経過: {h:02d}:{m:02d}:{s:02d} / 想定コスト: ${cost:.2f}"
    if dpg.does_item_exist(TAG_STATUS_COST):
        dpg.set_value(TAG_STATUS_COST, text)


def _update_level_meter():
    global _last_level_theme
    # TAG_LEVEL_METER ウィジェットは _build_gui から削除済み（単独モード廃止）。
    # _system が稼働中でもウィジェットが存在しない場合は何もしない。
    if not dpg.does_item_exist(TAG_LEVEL_METER):
        return
    peak = _system.audio_peak_now if _system else 0
    gain = _system.effective_gain if _system else 1.0
    level = min(1.0, peak / 32767.0)
    pct = int(level * 100)
    dpg.set_value(TAG_LEVEL_METER, level)
    if gain > 1.05:
        dpg.configure_item(TAG_LEVEL_METER, overlay=f"{pct}%  x{gain:.1f}")
    else:
        dpg.configure_item(TAG_LEVEL_METER, overlay=f"{pct}%")
    # 色: 0-40%緑 / 40-80%黄 / 80-100%赤
    if pct < 40:
        theme = TAG_LEVEL_THEME_GREEN
    elif pct < 80:
        theme = TAG_LEVEL_THEME_YELLOW
    else:
        theme = TAG_LEVEL_THEME_RED
    if theme != _last_level_theme:
        _last_level_theme = theme
        dpg.bind_item_theme(TAG_LEVEL_METER, theme)


def _update_konnyaku_level_meters():
    """翻訳こんにゃくモードの入力・出力レベルメーターを更新する。

    _konnyaku_system が None のときは何もしない（単独モード稼働中 / 未起動 時に安全）。
    入力レベル: CaptionSystem.audio_peak_now（capture スレッドが毎チャンク更新）
    出力レベル: AudioOutputStream.audio_peak_now（write 時に更新）
    """
    if _konnyaku_system is None:
        return

    route_a = _konnyaku_system.route_a_system
    route_b = _konnyaku_system.route_b_system

    # 各系統の有効/無効を読む（稼働中に OFF されたら見かけ上停止して見せる）
    route_a_enabled = (
        bool(dpg.get_value(TAG_ROUTE_A_ENABLE))
        if dpg.does_item_exist(TAG_ROUTE_A_ENABLE) else True
    )
    # PTT モード ON 時は RouteState.RUNNING で判定する（F-7.1）
    # PTT モード OFF 時は従来通りチェックボックスの値で判定する
    if _ptt_enabled:
        route_b_enabled = _is_route_b_active_for_meter(ptt_enabled=True)
    else:
        route_b_enabled = (
            bool(dpg.get_value(TAG_ROUTE_B_ENABLE))
            if dpg.does_item_exist(TAG_ROUTE_B_ENABLE) else True
        )

    # 経路A 入力レベル（route_a が None または Enable=OFF なら 0）
    if route_a is not None and route_a_enabled:
        peak_a_in = route_a.audio_peak_now
    else:
        peak_a_in = 0
    level_a_in = min(1.0, peak_a_in / 32767.0)
    if dpg.does_item_exist(TAG_LEVEL_METER_A_IN):
        dpg.set_value(TAG_LEVEL_METER_A_IN, level_a_in)
        dpg.configure_item(TAG_LEVEL_METER_A_IN, overlay=f"{int(level_a_in * 100)}%")

    # 経路B 入力レベル
    if route_b is not None and route_b_enabled:
        peak_b_in = route_b.audio_peak_now
    else:
        peak_b_in = 0
    level_b_in = min(1.0, peak_b_in / 32767.0)
    if dpg.does_item_exist(TAG_LEVEL_METER_B_IN):
        dpg.set_value(TAG_LEVEL_METER_B_IN, level_b_in)
        dpg.configure_item(TAG_LEVEL_METER_B_IN, overlay=f"{int(level_b_in * 100)}%")

    # 経路A 出力レベル（AudioOutputStream が起動していれば peak 取得）
    if route_a is not None and route_a_enabled:
        stream_a = getattr(route_a, "_audio_stream", None)
        peak_a_out = stream_a.audio_peak_now if stream_a is not None else 0
    else:
        peak_a_out = 0
    level_a_out = min(1.0, peak_a_out / 32767.0)
    if dpg.does_item_exist(TAG_LEVEL_METER_A_OUT):
        dpg.set_value(TAG_LEVEL_METER_A_OUT, level_a_out)
        dpg.configure_item(TAG_LEVEL_METER_A_OUT, overlay=f"{int(level_a_out * 100)}%")

    # 経路B 出力レベル
    if route_b is not None and route_b_enabled:
        stream_b = getattr(route_b, "_audio_stream", None)
        peak_b_out = stream_b.audio_peak_now if stream_b is not None else 0
    else:
        peak_b_out = 0
    level_b_out = min(1.0, peak_b_out / 32767.0)
    if dpg.does_item_exist(TAG_LEVEL_METER_B_OUT):
        dpg.set_value(TAG_LEVEL_METER_B_OUT, level_b_out)
        dpg.configure_item(TAG_LEVEL_METER_B_OUT, overlay=f"{int(level_b_out * 100)}%")


# ---------------------------------------------------------------------------
# CLI 自動操作モード
# ---------------------------------------------------------------------------

def _inject_test_transcripts() -> None:
    """偽の transcript を route_a / route_b の RealtimeTranslator コールバックに注入する。

    実音声なしで _realtime_broadcast → broadcaster.broadcast を発火させる。
    AI 自動デバッグで Issue #42（overlay 字幕表示）を検証するため。
    """
    if _konnyaku_system is None:
        print("[INJECT] _konnyaku_system is None, skip", flush=True)
        return
    route_a = _konnyaku_system.route_a_system
    route_b = _konnyaku_system.route_b_system
    print("[INJECT] route_a: source 'Hello from A'", flush=True)
    route_a._on_realtime_source_transcript("Hello from A")
    time.sleep(0.3)
    print("[INJECT] route_a: translated '経路Aテスト翻訳'", flush=True)
    route_a._on_realtime_transcript("経路Aテスト翻訳")
    time.sleep(0.3)
    print("[INJECT] route_b: source 'こんにちは経路B'", flush=True)
    route_b._on_realtime_source_transcript("こんにちは経路B")
    time.sleep(0.3)
    print("[INJECT] route_b: translated 'Test from B'", flush=True)
    route_b._on_realtime_transcript("Test from B")


def _auto_konnyaku_runner(duration: int, inject_test: bool = False) -> None:
    """別スレッドで実行される自動操作（--auto-konnyaku 用）。

    AI が Bash 経由でアプリを実行 → ログ取得 → クラッシュ原因解析 → 修正のループを
    自律的に回せるようにするためのヘルパー。
    """
    try:
        print(f"[AUTO] Phase 1/4: モデルロード待機 (5s)...", flush=True)
        time.sleep(5)

        print(f"[AUTO] Phase 2/4: こんにゃく開始ボタン押下", flush=True)
        _on_konnyaku_start_stop_click()

        print(f"[AUTO] Phase 3/4: {duration}秒間動作中...", flush=True)
        if inject_test:
            # WS サーバー起動 + クライアント接続待ち
            time.sleep(3)
            print("[AUTO] 偽 transcript を注入してbroadcast 経路を検証", flush=True)
            _inject_test_transcripts()
            time.sleep(max(0, duration - 3))
        else:
            time.sleep(duration)

        print(f"[AUTO] Phase 4/4: こんにゃく停止ボタン押下", flush=True)
        _on_konnyaku_start_stop_click()

        # バックグラウンド shutdown スレッドの完了を待つ（最大15秒）
        deadline = time.monotonic() + 15.0
        while _konnyaku_running and time.monotonic() < deadline:
            time.sleep(0.2)

        print(f"[AUTO] アプリ終了", flush=True)
        dpg.stop_dearpygui()
    except Exception as e:
        import traceback
        print(f"[AUTO ERROR] {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        try:
            dpg.stop_dearpygui()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# メインループ
# ---------------------------------------------------------------------------

def main():
    global _config, _devices

    parser = argparse.ArgumentParser(description="Realtime Caption")
    parser.add_argument(
        "--auto-konnyaku", type=int, default=None,
        help="Auto-test konnyaku mode for N seconds then exit (for AI debugging)",
    )
    parser.add_argument(
        "--inject-test-transcripts", action="store_true",
        help="Inject fake transcripts to verify broadcast path (use with --auto-konnyaku)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable verbose logging (RT_* events to _verbose.txt) from startup",
    )
    args = parser.parse_args()

    if args.verbose:
        global _verbose_state
        _verbose_state = True

    _config = load_config("config.yaml")
    _host_api = _config.get("audio", {}).get("host_api", "wasapi")
    _devices = list_audio_devices(host_api=_host_api)

    rpc_port = _config.get("rpc", {}).get("port", 8767)
    _start_rpc_server(rpc_port)

    # 常駐モデル: MultiCaptionSystem を即生成
    _create_konnyaku_system()

    # PTT マネージャー初期化（_konnyaku_system 生成後に行う）
    _ptt_saved = _load_ptt_settings(_load_settings())
    _init_ptt_manager(
        ptt_enabled=_ptt_saved["ptt_enabled"],
        ptt_hotkey=_ptt_saved["ptt_hotkey"],
    )

    _build_gui()
    dpg.show_viewport()

    # Windows 上でウィンドウタイトルを UTF-16 で上書きして日本語化
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "Realtime Caption")
        if hwnd:
            user32.SetWindowTextW(hwnd, "リアルタイム字幕・翻訳")
    except Exception:
        pass

    # プリロード機能は一時無効化（RealtimeSTT のスレッド問題調査中）
    # threading.Thread(target=_trigger_preload, daemon=True).start()

    # CLI 自動操作モード: --auto-konnyaku=N 指定時はバックグラウンドスレッドで操作を自動実行
    if args.auto_konnyaku is not None:
        print(
            f"[AUTO] auto-konnyaku モード開始 (duration={args.auto_konnyaku}s,"
            f" inject_test={args.inject_test_transcripts})",
            flush=True,
        )
        threading.Thread(
            target=_auto_konnyaku_runner,
            args=(args.auto_konnyaku, args.inject_test_transcripts),
            daemon=True,
            name="AutoKonnyakuRunner",
        ).start()

    frame_count = 0
    while dpg.is_dearpygui_running():
        _drain_queue()

        # レベルメーター: 毎 2 フレーム（~30Hz）で更新
        if frame_count % 2 == 0:
            _update_level_meter()
            _update_konnyaku_level_meters()

        # 1秒ごと（約60fps想定で60フレームごと）に WS クライアント数・コストを更新
        frame_count += 1
        if frame_count >= 60:
            frame_count = 0
            _update_ws_status()
            _update_cost_status()

        # 保留中のコスト警告モーダルを処理（毎フレーム）
        _check_pending_cost_warnings()

        # ロード中は毎 10 フレーム（~6Hz）進捗更新
        if _loading_active and frame_count % 10 == 0:
            _update_loading_progress()

        dpg.render_dearpygui_frame()

    # ウィンドウを閉じたらシステムを停止・設定を保存
    _save_settings()
    if _system is not None:
        _system.shutdown()
    # PTT ホットキー登録解除（terminate より前に行う）（F-7.5）
    _cleanup_ptt_manager()
    if _konnyaku_system is not None:
        try:
            _konnyaku_system.terminate()
        except Exception as e:
            print(f"[WARN] terminate failed: {e}", flush=True)

    _release_subst(_subst_letter)
    dpg.destroy_context()


if __name__ == "__main__":
    main()
