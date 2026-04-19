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


_MODEL_BASE = _Path(__file__).parent.resolve() / "models"
_ascii_models, _subst_letter = _ensure_ascii_path(_MODEL_BASE)
os.environ.setdefault("HF_HOME", str(_ascii_models / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(_ascii_models / "torch"))

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

# GUI タグ
TAG_DEVICE_COMBO = "device_combo"
TAG_MODEL_COMBO = "model_combo"
TAG_START_BTN = "start_btn"
TAG_LOG_GROUP = "log_group"
TAG_LOG_SCROLL = "log_scroll"
TAG_STATUS_DEVICE = "status_device"
TAG_STATUS_WS = "status_ws"
TAG_STATUS_RPC = "status_rpc"
TAG_STATUS_STATE = "status_state"


# ---------------------------------------------------------------------------
# GUI キューコマンド処理
# ---------------------------------------------------------------------------

def _enqueue(cmd: str, **kwargs):
    _gui_queue.put({"cmd": cmd, **kwargs})


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

        elif cmd == "set_running":
            global _is_running
            _is_running = item["value"]
            if _is_running:
                dpg.configure_item(TAG_START_BTN, label="Stop")
                dpg.set_value(TAG_STATUS_STATE, "● Recording")
            else:
                dpg.configure_item(TAG_START_BTN, label="Start")
                dpg.set_value(TAG_STATUS_STATE, "○ Stopped")

        elif cmd == "stop_system":
            _do_stop()

        elif cmd == "start_system":
            _do_start()


def _append_log_item(ts: str, original: str, translated: str):
    with dpg.group(parent=TAG_LOG_GROUP):
        dpg.add_text(f"[{ts}] EN: {original}", wrap=860)
        dpg.add_text(f"             JP: {translated}", wrap=860)
        dpg.add_separator()

    # 自動スクロール
    dpg.set_y_scroll(TAG_LOG_SCROLL, dpg.get_y_scroll_max(TAG_LOG_SCROLL))


# ---------------------------------------------------------------------------
# CaptionSystem の起動・停止
# ---------------------------------------------------------------------------

def _do_start():
    global _system, _system_thread, _is_running

    if _is_running:
        return

    # 選択されたデバイスを取得
    device_label = dpg.get_value(TAG_DEVICE_COMBO)
    device_info = next((d for d in _devices if _device_label(d) == device_label), None)
    if device_info is None:
        return

    model_name = dpg.get_value(TAG_MODEL_COMBO)

    def on_result(original: str, translated: str):
        ts = datetime.now().strftime("%H:%M:%S")
        _log_entries.append({"ts": ts, "original": original, "translated": translated})
        if len(_log_entries) > 200:
            _log_entries.pop(0)
        _enqueue("append_log", ts=ts, original=original, translated=translated)

    _system = CaptionSystem(_config, device_info, model_name, on_result=on_result)

    def run_in_thread():
        asyncio.run(_system.run())
        _enqueue("set_running", value=False)

    _system_thread = threading.Thread(target=run_in_thread, daemon=True)
    _system_thread.start()
    _enqueue("set_running", value=True)


def _do_stop():
    global _system, _is_running
    if _system is not None:
        _system.shutdown()
        _system = None
    _is_running = False
    dpg.configure_item(TAG_START_BTN, label="Start")
    dpg.set_value(TAG_STATUS_STATE, "○ Stopped")


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
            self._send_json({
                "state": "running" if _is_running else "stopped",
                "device": device_label,
                "model": model,
                "ws_clients": ws_clients,
            })

        elif self.path == "/api/log":
            self._send_json(_log_entries[-100:])

        elif self.path == "/api/devices":
            self._send_json(_devices)

        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path == "/api/stop":
            _enqueue("stop_system")
            self._send_json({"ok": True})
        elif self.path == "/api/start":
            _enqueue("start_system")
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

def _build_gui():
    dpg.create_context()
    dpg.create_viewport(title="Realtime Caption & Translation", width=900, height=650, resizable=False)
    dpg.setup_dearpygui()

    rpc_port = _config.get("rpc", {}).get("port", 8767)
    default_model = _config.get("whisper", {}).get("model", "small")

    device_labels = [_device_label(d) for d in _devices]
    default_device = next(
        (lbl for lbl in device_labels if "[Loopback]" in lbl),
        device_labels[0] if device_labels else "",
    )

    with dpg.window(tag="main_window", no_title_bar=True, no_resize=True,
                    no_move=True, no_scrollbar=True):

        # --- ツールバー ---
        with dpg.group(horizontal=True):
            dpg.add_text("Device:")
            dpg.add_combo(
                tag=TAG_DEVICE_COMBO,
                items=device_labels,
                default_value=default_device,
                width=480,
            )
            dpg.add_text("  Model:")
            dpg.add_combo(
                tag=TAG_MODEL_COMBO,
                items=["small", "medium"],
                default_value=default_model if default_model in ["small", "medium"] else "small",
                width=100,
            )
            dpg.add_button(tag=TAG_START_BTN, label="Start", width=120,
                           callback=_on_start_stop_click)

        dpg.add_separator()

        # --- ログエリア ---
        with dpg.child_window(tag=TAG_LOG_SCROLL, height=530, border=True,
                               autosize_x=True):
            with dpg.group(tag=TAG_LOG_GROUP):
                pass

        dpg.add_separator()

        # --- ステータスバー ---
        with dpg.group(horizontal=True):
            dpg.add_text("○ Stopped", tag=TAG_STATUS_STATE)
            dpg.add_text("  |  WS clients:")
            dpg.add_text("0", tag=TAG_STATUS_WS)
            dpg.add_text("  |  RPC:")
            dpg.add_text(f"http://localhost:{rpc_port}", tag=TAG_STATUS_RPC)

    dpg.set_primary_window("main_window", True)


# ---------------------------------------------------------------------------
# WS クライアント数の定期更新
# ---------------------------------------------------------------------------

_last_ws_count = -1


def _update_ws_status():
    global _last_ws_count
    count = _system._broadcaster.client_count if _system else 0
    if count != _last_ws_count:
        _last_ws_count = count
        dpg.set_value(TAG_STATUS_WS, str(count))


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

    frame_count = 0
    while dpg.is_dearpygui_running():
        _drain_queue()

        # 1秒ごと（約60fps想定で60フレームごと）に WS クライアント数を更新
        frame_count += 1
        if frame_count >= 60:
            frame_count = 0
            _update_ws_status()

        dpg.render_dearpygui_frame()

    # ウィンドウを閉じたらシステムを停止
    if _system is not None:
        _system.shutdown()

    _release_subst(_subst_letter)
    dpg.destroy_context()


if __name__ == "__main__":
    main()
