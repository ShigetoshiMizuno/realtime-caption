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

import asyncio
import io
import json
import queue
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import dearpygui.dearpygui as dpg

from main import CaptionSystem, list_audio_devices, find_device_by_name, load_config
from config_utils import decode_api_key, encode_api_key

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

VAD_DEFAULT_SENSITIVITY = 0.4
# 0.6 秒: 自然な息継ぎ程度の沈黙では文を切らず、文末の本格的な無音で確定する。
# 短すぎる（0.2 等）と長文の途中で分断され、Whisper が冒頭/末尾を取りこぼしやすい。
VAD_DEFAULT_SILENCE = 0.6
GAIN_DEFAULT_MODE = "off"
GAIN_DEFAULT_VALUE = 1.0

_SETTINGS_PATH = _SCRIPT_DIR / "settings.json"
CONFIG_PATH = _SCRIPT_DIR / "config.yaml"

# API キー UI タグ
TAG_OPENAI_KEY_INPUT = "openai_key_input"
TAG_DEEPL_KEY_INPUT = "deepl_key_input"
TAG_KEY_SHOW_OPENAI = "key_show_openai"
TAG_KEY_SHOW_DEEPL = "key_show_deepl"
TAG_KEY_SAVE_BTN = "key_save_btn"
TAG_KEY_STATUS = "key_status"

# Verbose ロギング状態（settings.json で永続化）
_verbose_state: bool = False

# コスト警告: スレッドセーフなフラグ（メインスレッドの描画ループで検査）
_pending_cost_warnings: list[float] = []
_cost_warning_lock = threading.Lock()


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings():
    try:
        # 翻訳エンジンは表示ラベルではなく内部キーで保存する
        trans_label = dpg.get_value(TAG_TRANS_COMBO)
        trans_key = _trans_label_to_key(trans_label)
        output_device = ""
        if dpg.does_item_exist(TAG_OUTPUT_DEVICE_COMBO):
            output_device = dpg.get_value(TAG_OUTPUT_DEVICE_COMBO)
        data = {
            "device": dpg.get_value(TAG_DEVICE_COMBO),
            "model": dpg.get_value(TAG_MODEL_COMBO),
            "trans": trans_key,
            "vad_sensitivity": dpg.get_value(TAG_VAD_SENSITIVITY),
            "vad_silence": dpg.get_value(TAG_VAD_SILENCE),
            "gain_mode": dpg.get_value(TAG_GAIN_MODE),
            "gain_value": dpg.get_value(TAG_GAIN_SLIDER),
            "verbose": _verbose_state,
            "output_device": output_device,
        }
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


# ---------------------------------------------------------------------------
# Host API フィルタ変更
# ---------------------------------------------------------------------------

def _on_host_api_change(sender, value, user_data):
    """Host API フィルタ変更時にデバイスコンボを再列挙する。"""
    global _devices
    host_api_value = value.split()[0].lower()  # "wasapi (default)" -> "wasapi"
    _devices = list_audio_devices(host_api=host_api_value)
    device_labels = [_device_label(d) for d in _devices]
    if dpg.does_item_exist(TAG_DEVICE_COMBO):
        dpg.configure_item(TAG_DEVICE_COMBO, items=device_labels)
        if device_labels:
            dpg.set_value(TAG_DEVICE_COMBO, device_labels[0])
    # 出力デバイスも再列挙
    output_devices = list_audio_devices(device_type="output", host_api=host_api_value)
    output_labels = ["(なし)"] + [d["name"] for d in output_devices]
    if dpg.does_item_exist(TAG_OUTPUT_DEVICE_COMBO):
        dpg.configure_item(TAG_OUTPUT_DEVICE_COMBO, items=output_labels)
        dpg.set_value(TAG_OUTPUT_DEVICE_COMBO, "(なし)")
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
        print(f"[INFO] Preloading Whisper {model_name} ...")
        system.prepare()
        with _preload_lock:
            # キャンセルされていなければキャッシュに格納
            if _preload_key == key and not system._stop_event.is_set():
                _preloaded_system = system
                print(f"[INFO] Preload done: {model_name}")
            else:
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

    with dpg.group(parent=TAG_LOG_GROUP):
        dpg.add_text(f"[{ts}] EN: {original}", wrap=860)
        dpg.add_text(f"             JP: {translated}", wrap=860)
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
        print(f"[{ts}] EN: {original}")
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
        key = (model_name, device_info["index"])
        with _preload_lock:
            cached = _preloaded_system if (_preload_key == key
                                           and _preloaded_system is not None
                                           and _preloaded_system._recorder is not None) else None
            if cached is not None:
                _preloaded_system = None
                _preload_key = None

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


def _available_trans_models(cfg: dict) -> list[str]:
    """有効な API キーが設定されている翻訳エンジンの表示ラベル一覧を返す。"""
    result = []
    # decode_api_key 経由で b64: 形式にも対応
    openai_key = decode_api_key(cfg.get("openai", {}).get("api_key", ""))
    if openai_key and "xxx" not in openai_key and openai_key != "your-api-key-here":
        result.append(_trans_key_to_label("openai"))
        result.append(_trans_key_to_label("openai-realtime"))
    deepl_key = decode_api_key(cfg.get("deepl", {}).get("api_key", ""))
    if deepl_key and "xxx" not in deepl_key and deepl_key != "your-deepl-key-here":
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
    dpg.create_context()

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

        # --- ツールバー 1行目: デバイス + Start ---
        with dpg.group(horizontal=True):
            dpg.add_text("音声入力:")
            dpg.add_combo(
                tag=TAG_DEVICE_COMBO,
                items=device_labels,
                default_value=default_device,
                width=-130,
                callback=lambda: threading.Thread(target=_trigger_preload, daemon=True).start(),
            )
            dpg.add_button(tag=TAG_START_BTN, label="開始", width=120,
                           callback=_on_start_stop_click,
                           enabled=bool(trans_models))

        # --- ツールバー 2行目: 入力ゲイン + レベルメーター + Clear log ---
        with dpg.group(horizontal=True):
            dpg.add_text("入力ゲイン:")
            dpg.add_combo(
                tag=TAG_GAIN_MODE,
                items=["off", "manual", "auto"],
                default_value=default_gain_mode,
                width=90,
                callback=_on_gain_mode_change,
            )
            dpg.add_text("  倍率:", tag=TAG_GAIN_LABEL,
                         show=(default_gain_mode == "manual"))
            dpg.add_slider_float(
                tag=TAG_GAIN_SLIDER,
                default_value=default_gain_value,
                min_value=1.0, max_value=20.0,
                width=200, format="%.2f",
                callback=_on_gain_value_change,
                show=(default_gain_mode == "manual"),
            )
            dpg.add_text("  音量:")
            dpg.add_progress_bar(tag=TAG_LEVEL_METER, default_value=0.0,
                                 width=180, overlay="0%")
            dpg.add_button(label="ログクリア", width=100, callback=_clear_log)
            dpg.add_button(
                tag=TAG_VERBOSE_BTN,
                label="Verbose ●" if _verbose_state else "Verbose ○",
                width=110, callback=_on_verbose_toggle,
            )

        # --- 詳細設定（初期状態は折りたたみ） ---
        with dpg.collapsing_header(label="詳細設定", default_open=False):
            with dpg.group(horizontal=True):
                dpg.add_text("認識モデル:")
                dpg.add_combo(
                    tag=TAG_MODEL_COMBO,
                    items=["small", "medium"],
                    default_value=default_model if default_model in ["small", "medium"] else "small",
                    width=120,
                    callback=lambda: threading.Thread(target=_trigger_preload, daemon=True).start(),
                )
                dpg.add_text("  翻訳エンジン:")
                dpg.add_combo(
                    tag=TAG_TRANS_COMBO,
                    items=trans_models if trans_models else ["(APIキー未設定)"],
                    default_value=default_trans if trans_models else "(APIキー未設定)",
                    width=120,
                    enabled=len(trans_models) > 1,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("音声出力先:")
                dpg.add_combo(
                    tag=TAG_OUTPUT_DEVICE_COMBO,
                    items=output_device_labels,
                    default_value=default_output_device,
                    width=280,
                    callback=_save_settings,
                )
                dpg.add_text("  ")
                dpg.add_button(
                    tag=TAG_ZOOM_PRESET_BTN,
                    label="Zoom 同時通訳プリセット",
                    width=200,
                    callback=_on_zoom_preset_click,
                )
            with dpg.group(horizontal=True):
                dpg.add_text("発話検出感度:")
                dpg.add_slider_float(
                    tag=TAG_VAD_SENSITIVITY,
                    default_value=default_sensitivity,
                    min_value=0.0, max_value=1.0,
                    width=160, format="%.2f",
                    callback=_on_vad_sensitivity_change,
                )
                dpg.add_text("  無音待機(秒):")
                dpg.add_slider_float(
                    tag=TAG_VAD_SILENCE,
                    default_value=default_silence,
                    min_value=0.1, max_value=3.0,
                    width=160, format="%.1f",
                    callback=_on_vad_silence_change,
                )
                dpg.add_button(label="既定値に戻す", width=110,
                               callback=lambda: (
                                   dpg.set_value(TAG_VAD_SENSITIVITY, VAD_DEFAULT_SENSITIVITY),
                                   dpg.set_value(TAG_VAD_SILENCE, VAD_DEFAULT_SILENCE),
                               ))

            # --- API キー設定 ---
            dpg.add_separator()
            dpg.add_text("API キー設定（b64 難読化して config.yaml に保存）")
            with dpg.group(horizontal=True):
                dpg.add_text("OpenAI:  ", )
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

            # --- Host API フィルタ（詳細設定最下部） ---
            dpg.add_separator()
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


# ---------------------------------------------------------------------------
# メインループ
# ---------------------------------------------------------------------------

def main():
    global _config, _devices

    _config = load_config("config.yaml")
    _host_api = _config.get("audio", {}).get("host_api", "wasapi")
    _devices = list_audio_devices(host_api=_host_api)

    rpc_port = _config.get("rpc", {}).get("port", 8767)
    _start_rpc_server(rpc_port)

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

    frame_count = 0
    while dpg.is_dearpygui_running():
        _drain_queue()

        # レベルメーター: 毎 2 フレーム（~30Hz）で更新
        if frame_count % 2 == 0:
            _update_level_meter()

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

    _release_subst(_subst_letter)
    dpg.destroy_context()


if __name__ == "__main__":
    main()
