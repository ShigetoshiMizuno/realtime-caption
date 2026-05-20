"""
tests/test_bugfix_b1_b2.py

不具合 B-1 / B-2 の回帰テスト。

B-1: WebSocket ポート衝突時のエラーハンドリング
  - OSError が raise された場合に _log("ERROR", ...) が呼ばれること
  - _on_realtime_error_external が設定されていれば呼び出されること
  - OSError が伝播すること（raise されること）
  - errno.EADDRINUSE / 10048 の場合は「ポートが使用中」メッセージであること
  - それ以外の OSError は「汎用エラー」メッセージであること（ポート使用中と誤報しない）

B-2: session.created / session.updated イベントのハンドリング
  - これらのイベントが RT_RAW_UNKNOWN として記録されないこと
  - RT_SESSION_EVENT として記録されること
  - RT_SESSION_EVENT に session_id フィールドが含まれること
"""

import asyncio
import json
import socket
import threading
import time
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# B-1: WebSocket ポート衝突エラーハンドリング
# ---------------------------------------------------------------------------

class TestWebSocketPortCollision:
    """B-1: ポート衝突時に OSError を適切にハンドリングすること。"""

    def _make_caption_system_minimal(self):
        """CaptionSystem の __init__ をバイパスして最小限インスタンスを作る。

        run() が参照するフィールドをすべてスタブで埋める。
        """
        from main import CaptionSystem, RouteState, AudioStats
        import threading

        cs = object.__new__(CaptionSystem)
        cs._state = RouteState.IDLE
        cs._state_lock = threading.Lock()
        cs._stop_event = threading.Event()
        cs._stop_event_async = None
        cs._realtime_translator = None
        cs._cost_monitor = None
        cs._recorder = None
        cs._loop = None
        cs._audio_stream = None
        cs._capture_stream = None
        cs._capture_thread = None
        cs._audio_stats_lock = threading.Lock()
        cs._audio_stats = AudioStats()
        cs._route_id = "test"
        cs.verbose = False
        cs._verbose_log_path = None
        cs._verbose_lock = threading.Lock()
        cs._idle_monitor = None
        cs._owns_broadcaster = True
        cs._broadcaster = MagicMock()
        cs._on_realtime_error_external = None
        # run() が参照するフィールド
        cs._config = {
            "websocket": {"host": "127.0.0.1", "port": 8765},
        }
        cs._audio_output_mode = False
        cs._realtime_mode = False
        cs._log_path = "test.log"
        # _start_recorder をスタブ化（スレッド内で何もしない）
        cs._start_recorder = lambda: None
        # ログキャプチャ用
        cs._logged_messages = []
        def _mock_log(level, msg, *args, **kwargs):
            cs._logged_messages.append((level, msg))
        cs._log = _mock_log
        return cs

    def test_oserror_logs_error_message(self):
        """OSError 発生時に _log("ERROR", ...) が呼ばれること。"""
        cs = self._make_caption_system_minimal()

        stop_event_async = asyncio.Event()
        cs._stop_event_async = stop_event_async

        async def _run_and_raise_oserror():
            """websockets.serve が OSError を raise するシナリオをシミュレート。"""
            with patch("websockets.serve") as mock_serve:
                # serve の __aenter__ が OSError を raise するようにモック
                mock_context = AsyncMock()
                mock_context.__aenter__.side_effect = OSError(
                    10048, "error while attempting to bind on address"
                )
                mock_serve.return_value = mock_context
                await cs.run()

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(OSError):
                loop.run_until_complete(_run_and_raise_oserror())
        finally:
            loop.close()

        # ERROR レベルのログが出力されていること
        error_logs = [msg for level, msg in cs._logged_messages if level == "ERROR"]
        assert len(error_logs) >= 1, f"ERROR ログが記録されていない。logged: {cs._logged_messages}"
        # メッセージにポートに関する内容が含まれること
        assert any("ポート" in msg or "port" in msg.lower() or "WebSocket" in msg for msg in error_logs), \
            f"ポート関連メッセージがない: {error_logs}"

    def test_oserror_calls_external_error_callback(self):
        """OSError 発生時に _on_realtime_error_external が呼び出されること。"""
        cs = self._make_caption_system_minimal()

        stop_event_async = asyncio.Event()
        cs._stop_event_async = stop_event_async

        external_errors = []
        cs._on_realtime_error_external = lambda msg: external_errors.append(msg)

        async def _run_and_raise_oserror():
            with patch("websockets.serve") as mock_serve:
                mock_context = AsyncMock()
                mock_context.__aenter__.side_effect = OSError(
                    10048, "error while attempting to bind on address"
                )
                mock_serve.return_value = mock_context
                await cs.run()

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(OSError):
                loop.run_until_complete(_run_and_raise_oserror())
        finally:
            loop.close()

        assert len(external_errors) >= 1, \
            f"_on_realtime_error_external が呼ばれていない: {external_errors}"

    def test_oserror_is_propagated(self):
        """OSError が raise されて伝播すること。"""
        cs = self._make_caption_system_minimal()

        stop_event_async = asyncio.Event()
        cs._stop_event_async = stop_event_async

        async def _run_and_raise_oserror():
            with patch("websockets.serve") as mock_serve:
                mock_context = AsyncMock()
                mock_context.__aenter__.side_effect = OSError(
                    10048, "error while attempting to bind on address"
                )
                mock_serve.return_value = mock_context
                await cs.run()

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(OSError):
                loop.run_until_complete(_run_and_raise_oserror())
        finally:
            loop.close()

    def test_oserror_without_external_callback_does_not_raise_attributeerror(self):
        """_on_realtime_error_external が None でも AttributeError にならないこと。"""
        cs = self._make_caption_system_minimal()
        cs._on_realtime_error_external = None  # 明示的に None

        stop_event_async = asyncio.Event()
        cs._stop_event_async = stop_event_async

        async def _run_and_raise_oserror():
            with patch("websockets.serve") as mock_serve:
                mock_context = AsyncMock()
                mock_context.__aenter__.side_effect = OSError(10048, "bind error")
                mock_serve.return_value = mock_context
                await cs.run()

        loop = asyncio.new_event_loop()
        try:
            # OSError は伝播するが AttributeError は発生しないこと
            with pytest.raises(OSError):
                loop.run_until_complete(_run_and_raise_oserror())
        finally:
            loop.close()

    def test_eaddrinuse_message_mentions_port_in_use(self):
        """EADDRINUSE (errno 98 / 10048) の場合は「ポートが使用中」メッセージであること。"""
        import errno as _errno
        cs = self._make_caption_system_minimal()
        cs._stop_event_async = asyncio.Event()

        async def _run_with_eaddrinuse():
            with patch("websockets.serve") as mock_serve:
                mock_context = AsyncMock()
                # Linux では errno.EADDRINUSE=98、Windows では 10048
                err = OSError(10048, "error while attempting to bind on address")
                err.errno = 10048
                mock_context.__aenter__.side_effect = err
                mock_serve.return_value = mock_context
                await cs.run()

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(OSError):
                loop.run_until_complete(_run_with_eaddrinuse())
        finally:
            loop.close()

        error_logs = [msg for level, msg in cs._logged_messages if level == "ERROR"]
        assert len(error_logs) >= 1
        # 「ポートが使用中」「二重起動」に類する文言が含まれること
        assert any("使用中" in msg or "二重起動" in msg for msg in error_logs), \
            f"ポート使用中メッセージが含まれていない: {error_logs}"

    def test_other_oserror_does_not_mention_port_in_use(self):
        """EADDRINUSE 以外の OSError（例: 権限拒否）は「ポートが使用中」と誤報しないこと。"""
        import errno as _errno
        cs = self._make_caption_system_minimal()
        cs._stop_event_async = asyncio.Event()

        async def _run_with_eacces():
            with patch("websockets.serve") as mock_serve:
                mock_context = AsyncMock()
                err = OSError(_errno.EACCES, "Permission denied")
                err.errno = _errno.EACCES
                mock_context.__aenter__.side_effect = err
                mock_serve.return_value = mock_context
                await cs.run()

        loop = asyncio.new_event_loop()
        try:
            with pytest.raises(OSError):
                loop.run_until_complete(_run_with_eacces())
        finally:
            loop.close()

        error_logs = [msg for level, msg in cs._logged_messages if level == "ERROR"]
        assert len(error_logs) >= 1
        # 「ポートが使用中」「二重起動」という誤報が含まれないこと
        assert not any("使用中" in msg or "二重起動" in msg for msg in error_logs), \
            f"権限拒否エラーなのに「ポート使用中」メッセージが出ている: {error_logs}"


# ---------------------------------------------------------------------------
# B-2: session.created / session.updated イベントのハンドリング
# ---------------------------------------------------------------------------

def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


async def _run_mock_ws_server(host, port, handler, stop_event, ready_event=None):
    import websockets
    async with websockets.serve(handler, host, port):
        if ready_event is not None:
            ready_event.set()
        await stop_event.wait()


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


try:
    from realtime_translator import RealtimeTranslator
    _RT_AVAILABLE = True
except ImportError:
    _RT_AVAILABLE = False
    RealtimeTranslator = None


@pytest.mark.skipif(not _RT_AVAILABLE, reason="realtime_translator モジュール未インポート")
class TestSessionEventHandling:
    """B-2: session.created / session.updated イベントのハンドリング。"""

    def test_session_created_not_logged_as_rt_raw_unknown(self):
        """session.created が RT_RAW_UNKNOWN として記録されないこと。"""
        verbose_logs = []

        def capture_verbose(event, **fields):
            verbose_logs.append((event, fields))

        async def mock_handler(websocket):
            # session.update を受信
            await asyncio.wait_for(websocket.recv(), timeout=5)

            # session.created を送信
            await websocket.send(json.dumps({
                "type": "session.created",
                "session": {"id": "fake-session-id-0001"}
            }))
            await asyncio.sleep(0.1)

            # 接続を維持
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-session-created-0000",
                target_language_code="ja",
                reconnect_max_attempts=0,
            )
            translator._verbose_callback = capture_verbose
            translator._ws_url = f"ws://localhost:{port}"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # イベント処理を待つ
            deadline = time.time() + 3
            while time.time() < deadline:
                unknown_events = [
                    ev for ev, fields in verbose_logs
                    if ev == "RT_RAW_UNKNOWN"
                    and fields.get("type") == "session.created"
                ]
                if unknown_events:
                    break
                time.sleep(0.05)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        # RT_RAW_UNKNOWN として記録されていないこと
        unknown_events = [
            ev for ev, fields in verbose_logs
            if ev == "RT_RAW_UNKNOWN"
            and fields.get("type") == "session.created"
        ]
        assert len(unknown_events) == 0, \
            f"session.created が RT_RAW_UNKNOWN として記録されている: {unknown_events}"

    def test_session_updated_not_logged_as_rt_raw_unknown(self):
        """session.updated が RT_RAW_UNKNOWN として記録されないこと。"""
        verbose_logs = []

        def capture_verbose(event, **fields):
            verbose_logs.append((event, fields))

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.updated",
                "session": {"id": "fake-session-id-0002"}
            }))
            await asyncio.sleep(0.1)

            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-session-updated-0000",
                target_language_code="ja",
                reconnect_max_attempts=0,
            )
            translator._verbose_callback = capture_verbose
            translator._ws_url = f"ws://localhost:{port}"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            deadline = time.time() + 3
            while time.time() < deadline:
                unknown_events = [
                    ev for ev, fields in verbose_logs
                    if ev == "RT_RAW_UNKNOWN"
                    and fields.get("type") == "session.updated"
                ]
                if unknown_events:
                    break
                time.sleep(0.05)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        unknown_events = [
            ev for ev, fields in verbose_logs
            if ev == "RT_RAW_UNKNOWN"
            and fields.get("type") == "session.updated"
        ]
        assert len(unknown_events) == 0, \
            f"session.updated が RT_RAW_UNKNOWN として記録されている: {unknown_events}"

    def test_session_created_logged_as_rt_session_event(self):
        """session.created が RT_SESSION_EVENT として記録されること。"""
        verbose_logs = []

        def capture_verbose(event, **fields):
            verbose_logs.append((event, fields))

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.created",
                "session": {"id": "fake-session-id-0003"}
            }))
            await asyncio.sleep(0.1)

            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-session-event-0001",
                target_language_code="ja",
                reconnect_max_attempts=0,
            )
            translator._verbose_callback = capture_verbose
            translator._ws_url = f"ws://localhost:{port}"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # RT_SESSION_EVENT が記録されるまで待つ
            deadline = time.time() + 3
            while time.time() < deadline:
                session_events = [
                    ev for ev, fields in verbose_logs
                    if ev == "RT_SESSION_EVENT"
                    and fields.get("type") == "session.created"
                ]
                if session_events:
                    break
                time.sleep(0.05)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        session_events = [
            ev for ev, fields in verbose_logs
            if ev == "RT_SESSION_EVENT"
            and fields.get("type") == "session.created"
        ]
        assert len(session_events) >= 1, \
            f"session.created が RT_SESSION_EVENT として記録されていない。verbose_logs: {verbose_logs[:20]}"

    def test_session_updated_logged_as_rt_session_event(self):
        """session.updated が RT_SESSION_EVENT として記録されること。"""
        verbose_logs = []

        def capture_verbose(event, **fields):
            verbose_logs.append((event, fields))

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.updated",
                "session": {"id": "fake-session-id-0004"}
            }))
            await asyncio.sleep(0.1)

            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-session-event-0002",
                target_language_code="ja",
                reconnect_max_attempts=0,
            )
            translator._verbose_callback = capture_verbose
            translator._ws_url = f"ws://localhost:{port}"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            deadline = time.time() + 3
            while time.time() < deadline:
                session_events = [
                    ev for ev, fields in verbose_logs
                    if ev == "RT_SESSION_EVENT"
                    and fields.get("type") == "session.updated"
                ]
                if session_events:
                    break
                time.sleep(0.05)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        session_events = [
            ev for ev, fields in verbose_logs
            if ev == "RT_SESSION_EVENT"
            and fields.get("type") == "session.updated"
        ]
        assert len(session_events) >= 1, \
            f"session.updated が RT_SESSION_EVENT として記録されていない。verbose_logs: {verbose_logs[:20]}"

    def test_session_created_rt_session_event_includes_session_id(self):
        """RT_SESSION_EVENT に session_id フィールドが含まれること（session.created）。"""
        verbose_logs = []

        def capture_verbose(event, **fields):
            verbose_logs.append((event, fields))

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.created",
                "session": {"id": "fake-session-id-0005"}
            }))
            await asyncio.sleep(0.1)

            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-session-id-check-0001",
                target_language_code="ja",
                reconnect_max_attempts=0,
            )
            translator._verbose_callback = capture_verbose
            translator._ws_url = f"ws://localhost:{port}"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            deadline = time.time() + 3
            while time.time() < deadline:
                session_events = [
                    (ev, fields) for ev, fields in verbose_logs
                    if ev == "RT_SESSION_EVENT"
                    and fields.get("type") == "session.created"
                ]
                if session_events:
                    break
                time.sleep(0.05)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        session_events = [
            (ev, fields) for ev, fields in verbose_logs
            if ev == "RT_SESSION_EVENT"
            and fields.get("type") == "session.created"
        ]
        assert len(session_events) >= 1, \
            f"RT_SESSION_EVENT が記録されていない: {verbose_logs[:20]}"
        # session_id フィールドが含まれること
        _, fields = session_events[0]
        assert "session_id" in fields, \
            f"RT_SESSION_EVENT に session_id がない: {fields}"
        assert fields["session_id"] == "fake-session-id-0005", \
            f"session_id の値が不一致: {fields['session_id']!r}"

    def test_session_updated_rt_session_event_includes_session_id(self):
        """RT_SESSION_EVENT に session_id フィールドが含まれること（session.updated）。"""
        verbose_logs = []

        def capture_verbose(event, **fields):
            verbose_logs.append((event, fields))

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.updated",
                "session": {"id": "fake-session-id-0006"}
            }))
            await asyncio.sleep(0.1)

            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-session-id-check-0002",
                target_language_code="ja",
                reconnect_max_attempts=0,
            )
            translator._verbose_callback = capture_verbose
            translator._ws_url = f"ws://localhost:{port}"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            deadline = time.time() + 3
            while time.time() < deadline:
                session_events = [
                    (ev, fields) for ev, fields in verbose_logs
                    if ev == "RT_SESSION_EVENT"
                    and fields.get("type") == "session.updated"
                ]
                if session_events:
                    break
                time.sleep(0.05)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        session_events = [
            (ev, fields) for ev, fields in verbose_logs
            if ev == "RT_SESSION_EVENT"
            and fields.get("type") == "session.updated"
        ]
        assert len(session_events) >= 1, \
            f"RT_SESSION_EVENT が記録されていない: {verbose_logs[:20]}"
        _, fields = session_events[0]
        assert "session_id" in fields, \
            f"RT_SESSION_EVENT に session_id がない: {fields}"
        assert fields["session_id"] == "fake-session-id-0006", \
            f"session_id の値が不一致: {fields['session_id']!r}"
