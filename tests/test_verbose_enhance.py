"""
tests/test_verbose_enhance.py

PR1: WS transport + state 遷移の徹底ログ化 のテスト

テスト対象:
  1. _log_verbose が verbose=False で no-op
  2. RT_WS_RECV イベントが全 type で出力されること（mock）
  3. RT_SESSION_UPDATE_SEND で payload 全文が記録されること
  4. _verbose_write が _verbose_state=True 時のみ書き出すこと
  5. _log_user / _log_action / _log_rpc が verbose ファイルにも追記すること
  6. payload 5000 文字超で truncate 動作
  7. STATE_TRANSITION verbose ログ
  8. RT_INIT verbose ログ
  9. CALLBACK verbose ログ
"""

import asyncio
import json
import socket
import threading
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# ヘルパー: CaptionSystem のテスト用最小インスタンス生成
# ---------------------------------------------------------------------------

def _make_caption_system(tmp_path: Path):
    """object.__new__ で CaptionSystem の最小インスタンスを作る。
    verbose ログ機能のみテストする場合に使用。
    """
    from main import AudioStats, CaptionSystem, RouteState

    cs = object.__new__(CaptionSystem)
    cs._state = RouteState.IDLE
    cs._state_lock = threading.Lock()
    cs._stop_event = threading.Event()
    cs._realtime_translator = None
    cs._cost_monitor = None
    cs._recorder = None
    cs._loop = None
    cs._stop_event_async = None
    cs._audio_stream = None
    cs._capture_stream = None
    cs._capture_thread = None
    cs._audio_stats_lock = threading.Lock()
    cs._audio_stats = AudioStats()
    cs._route_id = "test"
    cs._idle_monitor = None
    cs._config = {
        "openai": {"api_key": "FAKE-TEST-KEY-0000"},
        "openai_realtime": {"model": "gpt-realtime-translate"},
        "output": {"log_dir": str(tmp_path)},
    }
    cs._request_source_transcript = True
    cs._vad_enabled = False
    cs._vad_threshold = 0.5
    cs._vad_prefix_padding_ms = 300
    cs._vad_silence_duration_ms = 500
    cs._audio_output_mode = False
    cs._target_language_code = "ja"
    cs._idle_disconnect_enabled = False
    cs._idle_timeout_sec = 300.0
    cs._idle_audio_threshold = 200
    # コールバック（_create_realtime_translator が参照する）
    cs._on_ready = None
    cs._on_audio_delta = lambda pcm: None
    cs._on_realtime_error_external = None
    cs._pa_instance = None
    cs._audio_stream_lock = threading.Lock()
    # verbose ログ関連
    cs.verbose = False
    cs._verbose_log_path = None
    cs._verbose_lock = threading.Lock()
    # ログパス
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    cs._log_path = tmp_path / f"{today}-1_translate.txt"
    return cs


# ---------------------------------------------------------------------------
# ヘルパー: モック WebSocket サーバー（既存テストと同パターン）
# ---------------------------------------------------------------------------

async def _run_mock_ws_server(host, port, handler, stop_event, ready_event=None):
    import websockets
    async with websockets.serve(handler, host, port):
        if ready_event is not None:
            ready_event.set()
        await stop_event.wait()


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _start_mock_server_in_thread(handler, port=None):
    if port is None:
        port = _get_free_port()
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()
    ready_event = threading.Event()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            _run_mock_ws_server("localhost", port, handler, stop_event, ready_event)
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    ready_event.wait(timeout=5.0)
    return loop, stop_event, t, port


def _stop_mock_server(loop, stop_event):
    loop.call_soon_threadsafe(stop_event.set)
    time.sleep(0.1)


# ---------------------------------------------------------------------------
# 1. _log_verbose が verbose=False で no-op
# ---------------------------------------------------------------------------

class TestLogVerboseNoOp:
    """verbose=False のとき _log_verbose は何も書かない。"""

    def test_no_write_when_verbose_false(self, tmp_path):
        """verbose=False の CaptionSystem._log_verbose はファイルに書かない。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = False

        cs._log_verbose("TEST_EVENT", foo="bar")

        # verbose_log_path は生成されていないか、生成されていても空
        if cs._verbose_log_path is not None and cs._verbose_log_path.exists():
            content = cs._verbose_log_path.read_text(encoding="utf-8")
            assert content == ""

    def test_no_write_when_verbose_false_realtime_translator(self):
        """RealtimeTranslator._log_verbose: verbose_callback=None + logger.debug のみ（no-op相当）。"""
        from realtime_translator import RealtimeTranslator

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        # verbose_callback を設定しない状態で呼んでも例外なし
        rt._log_verbose("DUMMY_EVENT", key="value")
        # コールバックが設定されていないことを確認
        assert rt._verbose_callback is None


# ---------------------------------------------------------------------------
# 2. RT_WS_RECV イベントが全 type で出力されること（mock）
# ---------------------------------------------------------------------------

class TestRtWsRecvAllTypes:
    """_recv_loop の async for ループ冒頭で RT_WS_RECV が全 type に対して呼ばれる。"""

    def test_rt_ws_recv_logged_for_known_type(self):
        """既知イベント (session.output_transcript.delta) でも RT_WS_RECV が記録される。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback

        msg = {"type": "session.output_transcript.delta", "delta": "hello"}
        msg_text = json.dumps(msg)
        ws_messages = [msg_text]

        async def run():
            class FakeWs:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if ws_messages:
                        return ws_messages.pop(0)
                    raise StopAsyncIteration

            rt._stop_event = asyncio.Event()
            try:
                await rt._recv_loop(FakeWs())
            except Exception:
                pass

        asyncio.run(run())

        recv_events = [e for e in logged_events if e[0] == "RT_WS_RECV"]
        assert len(recv_events) >= 1, f"RT_WS_RECV not found in {logged_events}"
        assert "event_type" in recv_events[0][1], f"event_type missing: {recv_events[0][1]}"
        assert recv_events[0][1]["event_type"] == "session.output_transcript.delta"

    def test_rt_ws_recv_logged_for_unknown_type(self):
        """未知イベント type でも RT_WS_RECV が記録される。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback

        msg = {"type": "some.unknown.event.type", "data": "xyz"}
        msg_text = json.dumps(msg)
        ws_messages = [msg_text]

        async def run():
            class FakeWs:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if ws_messages:
                        return ws_messages.pop(0)
                    raise StopAsyncIteration

            rt._stop_event = asyncio.Event()
            try:
                await rt._recv_loop(FakeWs())
            except Exception:
                pass

        asyncio.run(run())

        recv_events = [e for e in logged_events if e[0] == "RT_WS_RECV"]
        assert len(recv_events) >= 1, f"RT_WS_RECV not found in {logged_events}"
        assert recv_events[0][1]["event_type"] == "some.unknown.event.type"

    def test_rt_ws_recv_includes_payload_full_text(self):
        """RT_WS_RECV の payload フィールドに元の JSON 文字列が含まれる。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback

        msg = {"type": "session.input_transcript.delta", "delta": "hello-world"}
        msg_text = json.dumps(msg, ensure_ascii=False)
        ws_messages = [msg_text]

        async def run():
            class FakeWs:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if ws_messages:
                        return ws_messages.pop(0)
                    raise StopAsyncIteration

            rt._stop_event = asyncio.Event()
            try:
                await rt._recv_loop(FakeWs())
            except Exception:
                pass

        asyncio.run(run())

        recv_events = [e for e in logged_events if e[0] == "RT_WS_RECV"]
        assert len(recv_events) >= 1
        assert "payload" in recv_events[0][1], f"payload missing: {recv_events[0][1]}"
        assert "hello-world" in recv_events[0][1]["payload"]


# ---------------------------------------------------------------------------
# 3. RT_SESSION_UPDATE_SEND で payload 全文が記録されること
# ---------------------------------------------------------------------------

class TestRtSessionUpdateSend:
    """_run_session の session.update 送信前後で RT_SESSION_UPDATE_SEND/SENT が記録される。"""

    def test_rt_session_update_send_logged(self):
        """WS 接続成功時に RT_SESSION_UPDATE_SEND イベントが記録される。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        port = _get_free_port()

        async def mock_handler(ws):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except Exception:
                pass

        loop, stop_event, t, port = _start_mock_server_in_thread(mock_handler, port)

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback
        rt._ws_url = f"ws://localhost:{port}"
        rt._connect_timeout = 3

        client_loop = asyncio.new_event_loop()
        rt._stop_event = asyncio.Event()
        rt._audio_queue = asyncio.Queue()

        async def run_once():
            try:
                await asyncio.wait_for(rt._run_session(), timeout=5.0)
            except Exception:
                pass

        try:
            client_loop.run_until_complete(run_once())
        finally:
            client_loop.close()
            _stop_mock_server(loop, stop_event)

        send_events = [e for e in logged_events if e[0] == "RT_SESSION_UPDATE_SEND"]
        assert len(send_events) >= 1, f"RT_SESSION_UPDATE_SEND not found in {logged_events}"
        assert "payload" in send_events[0][1], f"payload missing: {send_events[0][1]}"
        payload_str = send_events[0][1]["payload"]
        assert "session.update" in payload_str or "session" in payload_str

    def test_rt_session_update_sent_logged(self):
        """session.update 送信完了後に RT_SESSION_UPDATE_SENT イベントが記録される。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        port = _get_free_port()

        async def mock_handler(ws):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except Exception:
                pass

        loop, stop_event, t, port = _start_mock_server_in_thread(mock_handler, port)

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback
        rt._ws_url = f"ws://localhost:{port}"
        rt._connect_timeout = 3

        client_loop = asyncio.new_event_loop()
        rt._stop_event = asyncio.Event()
        rt._audio_queue = asyncio.Queue()

        async def run_once():
            try:
                await asyncio.wait_for(rt._run_session(), timeout=5.0)
            except Exception:
                pass

        try:
            client_loop.run_until_complete(run_once())
        finally:
            client_loop.close()
            _stop_mock_server(loop, stop_event)

        sent_events = [e for e in logged_events if e[0] == "RT_SESSION_UPDATE_SENT"]
        assert len(sent_events) >= 1, f"RT_SESSION_UPDATE_SENT not found in {logged_events}"


# ---------------------------------------------------------------------------
# 4. _verbose_write が _verbose_state=True 時のみ書き出すこと
# ---------------------------------------------------------------------------

class TestVerboseWrite:
    """app._verbose_write が _verbose_state ON/OFF で動作を切り替える。"""

    def test_verbose_write_noop_when_disabled(self, tmp_path):
        """_verbose_state=False のとき _verbose_write は何もしない。"""
        import app
        original_state = app._verbose_state
        original_path = getattr(app, "_app_verbose_path", None)
        test_file = tmp_path / "noop_verbose.txt"
        try:
            app._verbose_state = False
            app._app_verbose_path = test_file
            app._verbose_write("USER", "[USER] test event")
            # ファイルが存在しないか空
            if test_file.exists():
                content = test_file.read_text(encoding="utf-8")
                assert content == "", f"verbose OFF なのに書かれた: {content}"
        finally:
            app._verbose_state = original_state
            if original_path is not None:
                app._app_verbose_path = original_path

    def test_verbose_write_writes_when_enabled(self, tmp_path):
        """_verbose_state=True のとき _verbose_write はファイルに書き出す。"""
        import app
        original_state = app._verbose_state
        original_path = getattr(app, "_app_verbose_path", None)
        test_file = tmp_path / "test_app_verbose.txt"
        try:
            app._verbose_state = True
            app._app_verbose_path = test_file
            app._verbose_write("USER", "[USER] click_start_btn")
            assert test_file.exists(), "verbose ファイルが作成されていない"
            content = test_file.read_text(encoding="utf-8")
            assert "click_start_btn" in content, f"期待文字列がない: {content}"
        finally:
            app._verbose_state = original_state
            if original_path is not None:
                app._app_verbose_path = original_path

    def test_verbose_write_includes_timestamp(self, tmp_path):
        """_verbose_write の出力にタイムスタンプが含まれる。"""
        import app
        original_state = app._verbose_state
        original_path = getattr(app, "_app_verbose_path", None)
        test_file = tmp_path / "ts_verbose.txt"
        try:
            app._verbose_state = True
            app._app_verbose_path = test_file
            app._verbose_write("ACTION", "[ACTION] some_action")
            content = test_file.read_text(encoding="utf-8")
            import re
            assert re.search(r"\d{2}:\d{2}:\d{2}", content), f"タイムスタンプなし: {content}"
        finally:
            app._verbose_state = original_state
            if original_path is not None:
                app._app_verbose_path = original_path


# ---------------------------------------------------------------------------
# 5. _log_user / _log_action / _log_rpc が verbose ファイルにも追記すること
# ---------------------------------------------------------------------------

class TestLogHelperVerboseIntegration:
    """_log_user / _log_action / _log_rpc が _verbose_write を呼ぶ。"""

    def test_log_user_calls_verbose_write(self, tmp_path):
        """_log_user 呼び出し時に verbose ファイルにも書かれる。"""
        import app
        original_state = app._verbose_state
        original_path = getattr(app, "_app_verbose_path", None)
        test_file = tmp_path / "log_user_verbose.txt"
        try:
            app._verbose_state = True
            app._app_verbose_path = test_file
            app._log_user("start_btn_click", device="Virtual Cable")
            content = test_file.read_text(encoding="utf-8")
            assert "start_btn_click" in content, f"event名がない: {content}"
        finally:
            app._verbose_state = original_state
            if original_path is not None:
                app._app_verbose_path = original_path

    def test_log_action_calls_verbose_write(self, tmp_path):
        """_log_action 呼び出し時に verbose ファイルにも書かれる。"""
        import app
        original_state = app._verbose_state
        original_path = getattr(app, "_app_verbose_path", None)
        test_file = tmp_path / "log_action_verbose.txt"
        try:
            app._verbose_state = True
            app._app_verbose_path = test_file
            app._log_action("restart_route", route="a")
            content = test_file.read_text(encoding="utf-8")
            assert "restart_route" in content, f"event名がない: {content}"
        finally:
            app._verbose_state = original_state
            if original_path is not None:
                app._app_verbose_path = original_path

    def test_log_rpc_calls_verbose_write(self, tmp_path):
        """_log_rpc 呼び出し時に verbose ファイルにも書かれる。"""
        import app
        original_state = app._verbose_state
        original_path = getattr(app, "_app_verbose_path", None)
        test_file = tmp_path / "log_rpc_verbose.txt"
        try:
            app._verbose_state = True
            app._app_verbose_path = test_file
            app._log_rpc("GET /status", code=200)
            content = test_file.read_text(encoding="utf-8")
            assert "GET /status" in content, f"event名がない: {content}"
        finally:
            app._verbose_state = original_state
            if original_path is not None:
                app._app_verbose_path = original_path

    def test_log_helpers_noop_verbose_write_when_disabled(self, tmp_path):
        """_verbose_state=False のとき _log_user 等は verbose ファイルに書かない。"""
        import app
        original_state = app._verbose_state
        original_path = getattr(app, "_app_verbose_path", None)
        test_file = tmp_path / "noop_verbose2.txt"
        try:
            app._verbose_state = False
            app._app_verbose_path = test_file
            app._log_user("click_something")
            app._log_action("do_something")
            app._log_rpc("GET /foo")
            if test_file.exists():
                content = test_file.read_text(encoding="utf-8")
                assert content == "", f"verbose OFF なのに書かれた: {content}"
        finally:
            app._verbose_state = original_state
            if original_path is not None:
                app._app_verbose_path = original_path


# ---------------------------------------------------------------------------
# 6. payload 5000 文字超で truncate 動作
# ---------------------------------------------------------------------------

class TestPayloadTruncation:
    """_log_verbose の payload フィールドは 5000 文字でトランケートされる。"""

    def test_payload_truncated_at_5000(self, tmp_path):
        """payload が 5001 文字以上のとき 5000 文字 + '...' に切り詰められる。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = True

        big_payload = "x" * 6000
        cs._log_verbose("RT_WS_RECV", event_type="test", payload=big_payload)

        from main import CaptionSystem
        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        payload_lines = [line for line in content.splitlines() if "payload:" in line]
        assert payload_lines, f"payload 行がない: {content}"
        payload_line = payload_lines[0]
        # 5001 文字以上の "x" が含まれないこと
        assert payload_line.count("x") <= 5000, f"5000文字超の payload が記録された"
        # ... で切り詰められていること
        assert "..." in payload_line

    def test_normal_field_still_truncated_at_500(self, tmp_path):
        """payload 以外のフィールドは従来通り 500 文字でトランケートされる。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = True

        big_other = "y" * 600
        cs._log_verbose("RT_WS_RECV", event_type="test", some_field=big_other)

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        field_lines = [line for line in content.splitlines() if "some_field:" in line]
        assert field_lines, f"some_field 行がない: {content}"
        field_line = field_lines[0]
        assert "..." in field_line, f"トランケートされていない: {field_line}"
        y_count = field_line.count("y")
        assert y_count <= 500, f"500 文字を超えた y が {y_count} 文字"

    def test_payload_within_5000_not_truncated(self, tmp_path):
        """payload が 5000 文字以下のときは切り詰めしない。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = True

        exact_payload = "z" * 4999
        cs._log_verbose("RT_WS_RECV", event_type="test", payload=exact_payload)

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        payload_lines = [line for line in content.splitlines() if "payload:" in line]
        assert payload_lines, f"payload 行がない: {content}"
        payload_line = payload_lines[0]
        # 4999 文字全て記録されている（切り詰めなし）
        assert payload_line.count("z") == 4999, f"文字数が一致しない: {payload_line.count('z')}"


# ---------------------------------------------------------------------------
# 7. STATE_TRANSITION verbose ログ (main.py _set_state)
# ---------------------------------------------------------------------------

class TestStateTransitionVerbose:
    """_set_state が STATE_TRANSITION を verbose ログに書く。"""

    def test_state_transition_logged_when_verbose(self, tmp_path):
        """verbose=True のとき _set_state が STATE_TRANSITION を記録する。"""
        from main import RouteState

        cs = _make_caption_system(tmp_path)
        cs.verbose = True

        cs._set_state(RouteState.STARTING)

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        assert "STATE_TRANSITION" in content, f"STATE_TRANSITION が記録されていない: {content}"
        assert "STARTING" in content

    def test_state_transition_not_logged_when_verbose_false(self, tmp_path):
        """verbose=False のとき _set_state が STATE_TRANSITION を記録しない。"""
        from main import RouteState

        cs = _make_caption_system(tmp_path)
        cs.verbose = False

        cs._set_state(RouteState.STARTING)

        if cs._verbose_log_path is not None and cs._verbose_log_path.exists():
            content = cs._verbose_log_path.read_text(encoding="utf-8")
            assert "STATE_TRANSITION" not in content

    def test_state_transition_records_old_and_new(self, tmp_path):
        """STATE_TRANSITION ログに old と new の両方の状態が含まれる。"""
        from main import RouteState

        cs = _make_caption_system(tmp_path)
        cs.verbose = True

        cs._set_state(RouteState.STARTING)

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        assert "idle" in content.lower() or "IDLE" in content, f"old state がない: {content}"
        assert "starting" in content.lower() or "STARTING" in content, f"new state がない: {content}"


# ---------------------------------------------------------------------------
# 8. RT_INIT verbose ログ (main.py _create_realtime_translator)
# ---------------------------------------------------------------------------

class TestRtInitVerbose:
    """_create_realtime_translator が RT_INIT を verbose ログに書く。"""

    def test_rt_init_logged_when_verbose(self, tmp_path):
        """verbose=True のとき _create_realtime_translator が RT_INIT を記録する。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = True
        cs._realtime_translator = None  # 未生成状態

        cs._create_realtime_translator()

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        assert "RT_INIT" in content, f"RT_INIT が記録されていない: {content}"

    def test_rt_init_not_logged_when_verbose_false(self, tmp_path):
        """verbose=False のとき _create_realtime_translator が RT_INIT を記録しない。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = False
        cs._realtime_translator = None

        cs._create_realtime_translator()

        if cs._verbose_log_path is not None and cs._verbose_log_path.exists():
            content = cs._verbose_log_path.read_text(encoding="utf-8")
            assert "RT_INIT" not in content


# ---------------------------------------------------------------------------
# 9. CALLBACK verbose ログ (_on_realtime_transcript / _on_realtime_source_transcript)
# ---------------------------------------------------------------------------

class TestCallbackVerbose:
    """各コールバックが CALLBACK イベントを verbose ログに書く。"""

    def test_on_realtime_transcript_logged(self, tmp_path):
        """_on_realtime_transcript が CALLBACK を記録する。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = True
        # _loop が None の場合、asyncio.run_coroutine_threadsafe が呼ばれない
        cs._loop = None
        cs._latest_translation = ""
        cs._latest_source = ""

        cs._on_realtime_transcript("test translation")

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        assert "CALLBACK" in content, f"CALLBACK が記録されていない: {content}"

    def test_on_realtime_source_transcript_logged(self, tmp_path):
        """_on_realtime_source_transcript が CALLBACK を記録する。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = True
        cs._loop = None
        cs._latest_translation = ""
        cs._latest_source = ""

        cs._on_realtime_source_transcript("test source")

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        assert "CALLBACK" in content, f"CALLBACK が記録されていない: {content}"

    def test_callback_name_recorded(self, tmp_path):
        """CALLBACK ログに name フィールドが含まれる。"""
        cs = _make_caption_system(tmp_path)
        cs.verbose = True
        cs._loop = None
        cs._latest_translation = ""
        cs._latest_source = ""

        cs._on_realtime_transcript("hello")

        log_path = cs._ensure_verbose_log_path()
        content = log_path.read_text(encoding="utf-8")
        # name フィールドが含まれる
        assert "name" in content, f"name フィールドがない: {content}"
