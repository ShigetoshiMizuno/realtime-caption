"""
Realtime Caption & Translation — GUI アプリ
dearpygui を使ったフロントエンド。CaptionSystem をサブスレッドで動かし、
queue 経由で GUI を安全に更新する。
"""

import ctypes
import os
import subprocess
import sys
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

from main import CaptionSystem, list_audio_devices, load_config

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

VAD_DEFAULT_SENSITIVITY = 0.4
VAD_DEFAULT_SILENCE = 0.2
GAIN_DEFAULT_MODE = "off"
GAIN_DEFAULT_VALUE = 1.0

_SETTINGS_PATH = _SCRIPT_DIR / "settings.json"

# Verbose ロギング状態（settings.json で永続化）
_verbose_state: bool = False


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings():
    try:
        data = {
            "device": dpg.get_value(TAG_DEVICE_COMBO),
            "model": dpg.get_value(TAG_MODEL_COMBO),
            "trans": dpg.get_value(TAG_TRANS_COMBO),
            "vad_sensitivity": dpg.get_value(TAG_VAD_SENSITIVITY),
            "vad_silence": dpg.get_value(TAG_VAD_SILENCE),
            "gain_mode": dpg.get_value(TAG_GAIN_MODE),
            "gain_value": dpg.get_value(TAG_GAIN_SLIDER),
            "verbose": _verbose_state,
        }
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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

def _do_start(device_index: int | None = None, model: str | None = None):
    global _system, _system_thread, _is_running, _preloaded_system, _preload_key

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

    def on_result(original: str, translated: str):
        ts = datetime.now().strftime("%H:%M:%S")
        _log_entries.append({"ts": ts, "original": original, "translated": translated})
        if len(_log_entries) > 200:
            _log_entries.pop(0)
        _enqueue("append_log", ts=ts, original=original, translated=translated)
        print(f"[{ts}] EN: {original}")
        print(f"       JP: {translated}")

    # GUI の設定を config に反映
    selected_trans = dpg.get_value(TAG_TRANS_COMBO)
    if selected_trans in ("openai", "deepl"):
        _config.setdefault("translation", {})["translation_model"] = selected_trans
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

def _available_trans_models(cfg: dict) -> list[str]:
    """有効な API キーが設定されている翻訳エンジンだけリストで返す。"""
    result = []
    openai_key = cfg.get("openai", {}).get("api_key", "")
    if openai_key and "xxx" not in openai_key and openai_key != "your-api-key-here":
        result.append("openai")
    deepl_key = cfg.get("deepl", {}).get("api_key", "")
    if deepl_key and "xxx" not in deepl_key and deepl_key != "your-deepl-key-here":
        result.append("deepl")
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

    default_model = saved.get("model") or _config.get("whisper", {}).get("model", "small")
    trans_models = _available_trans_models(_config)
    default_trans = saved.get("trans") or _config.get("translation", {}).get("translation_model", "openai").lower()
    if default_trans not in trans_models:
        default_trans = trans_models[0] if trans_models else "openai"
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
    _devices = list_audio_devices()

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

        # 1秒ごと（約60fps想定で60フレームごと）に WS クライアント数を更新
        frame_count += 1
        if frame_count >= 60:
            frame_count = 0
            _update_ws_status()

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
