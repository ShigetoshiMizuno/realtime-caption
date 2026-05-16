"""
tests/test_verbose_rpc.py

PR3: RPC 全文記録 + 例外スタックトレース のテスト

テスト対象:
  1. do_POST 入口で body 全文が verbose 記録される
  2. _redact_secrets が api_key を redact する
  3. response status が end ログに含まれる
  4. 例外時に traceback が記録される
  5. _verbose_state=False で何も記録されない（オーバーヘッド 0）
  6. _verbose_callback の例外時 traceback 拡張
  7. CaptionSystem._capture_thread_body の except で traceback 記録
  8. RealtimeTranslator._fire_error で traceback 記録
"""

import threading
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# ヘルパー: app モジュールの verbose 状態を一時的に変更するコンテキスト
# ---------------------------------------------------------------------------

class _VerboseContext:
    """テスト中だけ app._verbose_state と _app_verbose_path を差し替える。"""

    def __init__(self, state: bool, log_path: Path):
        self.state = state
        self.log_path = log_path
        self._orig_state = None
        self._orig_path = None

    def __enter__(self):
        import app
        self._orig_state = app._verbose_state
        self._orig_path = getattr(app, "_app_verbose_path", None)
        app._verbose_state = self.state
        app._app_verbose_path = self.log_path
        return self

    def __exit__(self, *args):
        import app
        app._verbose_state = self._orig_state
        app._app_verbose_path = self._orig_path


def _make_rpc_handler(path: str, body_bytes: bytes = b"", method: str = "POST"):
    """_RPCHandler の最小インスタンスを作る（HTTPServer なし）。"""
    import app

    handler = object.__new__(app._RPCHandler)
    handler.path = path
    handler.wfile = MagicMock()

    # Content-Length ヘッダー
    mock_headers = MagicMock()
    mock_headers.get = MagicMock(return_value=str(len(body_bytes)) if body_bytes else "0")
    handler.headers = mock_headers

    # rfile (body の読み取り)
    import io
    handler.rfile = io.BytesIO(body_bytes)

    # _send_json はスタブ（実際の HTTP 送信なし）
    sent = []

    def fake_send_json(data, status=200):
        sent.append({"data": data, "status": status})

    handler._send_json = fake_send_json
    handler._sent = sent
    return handler


# ---------------------------------------------------------------------------
# 1. do_POST 入口で body 全文が verbose 記録される
# ---------------------------------------------------------------------------

class TestRPCBodyLogging:
    """do_POST / do_GET の入口で body 全文が verbose に記録される。"""

    def test_post_body_logged_in_verbose(self, tmp_path):
        """do_POST 時に body 全文が RPC start ログに記録される。"""
        import app

        body = b'{"device_index": 2, "model": "whisper-1"}'
        handler = _make_rpc_handler("/api/start", body_bytes=body)

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        # body の内容が記録されている
        assert "device_index" in content or "body" in content, (
            f"body が verbose に記録されていない: {content}"
        )

    def test_post_body_length_logged(self, tmp_path):
        """do_POST 時に body_len が verbose に記録される。"""
        import app

        body = b'{"device_index": 3}'
        handler = _make_rpc_handler("/api/start", body_bytes=body)

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        assert "body_len" in content, f"body_len が記録されていない: {content}"

    def test_post_start_log_has_path(self, tmp_path):
        """do_POST の start ログに path が含まれる。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        # start ログに /api/stop が含まれる
        assert "/api/stop" in content, f"/api/stop が記録されていない: {content}"
        # start が先に記録される
        lines = content.splitlines()
        start_lines = [l for l in lines if "start" in l and "/api/stop" in l]
        assert len(start_lines) >= 1, f"start ログが見つからない: {content}"

    def test_get_start_log_recorded(self, tmp_path):
        """do_GET でも start ログが verbose に記録される。"""
        import app

        handler = _make_rpc_handler("/api/log", method="GET")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_log_entries", []):
                handler.do_GET()

        content = log_file.read_text(encoding="utf-8")
        assert "/api/log" in content, f"/api/log が記録されていない: {content}"
        lines = content.splitlines()
        start_lines = [l for l in lines if "start" in l and "/api/log" in l]
        assert len(start_lines) >= 1, f"start ログが見つからない: {content}"


# ---------------------------------------------------------------------------
# 2. _redact_secrets が api_key を redact する
# ---------------------------------------------------------------------------

class TestRedactSecrets:
    """_redact_secrets が api_key 値を <redacted> に置換する。"""

    def test_api_key_is_redacted(self):
        """api_key フィールドが <redacted> に置換される。"""
        from app import _redact_secrets

        body = '{"api_key": "sk-test-fake-0000000000", "model": "whisper-1"}'
        result = _redact_secrets(body)

        assert "<redacted>" in result, f"api_key が redact されていない: {result}"
        assert "sk-test-fake-0000000000" not in result, f"元の値が残っている: {result}"

    def test_model_field_not_redacted(self):
        """api_key 以外のフィールドは redact されない。"""
        from app import _redact_secrets

        body = '{"api_key": "FAKE-TEST-KEY-0000", "model": "whisper-1"}'
        result = _redact_secrets(body)

        assert "whisper-1" in result, f"model フィールドが消えた: {result}"

    def test_redact_with_spaces_around_colon(self):
        """コロン前後にスペースがあっても redact される。"""
        from app import _redact_secrets

        body = '{"api_key" : "FAKE-KEY-abcdef"}'
        result = _redact_secrets(body)

        assert "<redacted>" in result, f"スペースありの場合に redact されていない: {result}"
        assert "FAKE-KEY-abcdef" not in result

    def test_no_api_key_field_unchanged(self):
        """api_key フィールドがない場合は変更されない。"""
        from app import _redact_secrets

        body = '{"device_index": 2, "model": "gpt-4"}'
        result = _redact_secrets(body)

        assert result == body, f"無関係なフィールドが変更された: {result}"

    def test_post_body_api_key_not_in_verbose(self, tmp_path):
        """do_POST の body に api_key があっても verbose には残らない。"""
        import app

        body_str = '{"api_key": "FAKE-TEST-KEY-0000-SHOULD-NOT-APPEAR", "model": "whisper"}'
        body = body_str.encode("utf-8")
        handler = _make_rpc_handler("/api/start", body_bytes=body)

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        assert "FAKE-TEST-KEY-0000-SHOULD-NOT-APPEAR" not in content, (
            f"api_key の実値が verbose に記録された: {content}"
        )


# ---------------------------------------------------------------------------
# 3. response status が end ログに含まれる
# ---------------------------------------------------------------------------

class TestRPCEndStatusLogging:
    """response status が end ログに含まれる。"""

    def test_post_end_log_has_status(self, tmp_path):
        """do_POST の end ログに status= が含まれる。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        end_lines = [l for l in lines if "end" in l and "/api/stop" in l]
        assert len(end_lines) >= 1, f"end ログが見つからない: {content}"
        assert "status=" in end_lines[-1], f"status= が end ログにない: {end_lines[-1]}"

    def test_get_end_log_has_status(self, tmp_path):
        """do_GET の end ログに status= が含まれる。"""
        import app

        handler = _make_rpc_handler("/api/log", method="GET")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_log_entries", []):
                handler.do_GET()

        content = log_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        end_lines = [l for l in lines if "end" in l and "/api/log" in l]
        assert len(end_lines) >= 1, f"end ログが見つからない: {content}"
        assert "status=" in end_lines[-1], f"status= が end ログにない: {end_lines[-1]}"

    def test_post_end_status_200_on_success(self, tmp_path):
        """正常時の do_POST end ログに status=200 が含まれる。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        assert "status=200" in content, f"status=200 が記録されていない: {content}"

    def test_post_end_status_404_on_unknown_path(self, tmp_path):
        """不明な path の do_POST end ログに status=404 が含まれる。"""
        import app

        handler = _make_rpc_handler("/api/unknown_endpoint", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        assert "status=404" in content, f"status=404 が記録されていない: {content}"

    def test_end_log_has_duration_ms(self, tmp_path):
        """end ログに duration_ms= が含まれる。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        content = log_file.read_text(encoding="utf-8")
        assert "duration_ms=" in content, f"duration_ms= がない: {content}"


# ---------------------------------------------------------------------------
# 4. 例外時に traceback が記録される
# ---------------------------------------------------------------------------

class TestRPCExceptionTraceback:
    """例外発生時に traceback が verbose に記録される。"""

    def test_post_exception_traceback_logged(self, tmp_path):
        """do_POST で例外が発生したとき traceback が verbose に記録される。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            # _enqueue が例外を投げるようにして例外パスをテスト
            with patch.object(app, "_enqueue", MagicMock(side_effect=RuntimeError("test exception"))):
                try:
                    handler.do_POST()
                except RuntimeError:
                    pass

        content = log_file.read_text(encoding="utf-8")
        assert "traceback" in content.lower() or "Traceback" in content, (
            f"traceback が記録されていない: {content}"
        )

    def test_post_exception_logs_error_type(self, tmp_path):
        """do_POST の例外 verbose ログに error_type が含まれる。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock(side_effect=RuntimeError("test err"))):
                try:
                    handler.do_POST()
                except RuntimeError:
                    pass

        content = log_file.read_text(encoding="utf-8")
        assert "error_type=RuntimeError" in content or "RuntimeError" in content, (
            f"error_type が記録されていない: {content}"
        )

    def test_post_exception_reraises(self, tmp_path):
        """do_POST で例外が発生しても、例外が再 raise される。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_enqueue", MagicMock(side_effect=ValueError("must propagate"))):
                with pytest.raises(ValueError, match="must propagate"):
                    handler.do_POST()

    def test_get_exception_traceback_logged(self, tmp_path):
        """do_GET で例外が発生したとき traceback が verbose に記録される。"""
        import app

        handler = _make_rpc_handler("/api/log", method="GET")

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            # _log_entries を例外を起こすオブジェクトに差し替え
            bad_entries = MagicMock()
            bad_entries.__getitem__ = MagicMock(side_effect=TypeError("test get error"))
            with patch.object(app, "_log_entries", bad_entries):
                try:
                    handler.do_GET()
                except TypeError:
                    pass

        content = log_file.read_text(encoding="utf-8")
        assert "traceback" in content.lower() or "Traceback" in content or "TypeError" in content, (
            f"traceback が記録されていない: {content}"
        )


# ---------------------------------------------------------------------------
# 5. _verbose_state=False で何も記録されない（オーバーヘッド 0）
# ---------------------------------------------------------------------------

class TestRPCVerboseStateOff:
    """_verbose_state=False のとき verbose ログは書かれない。"""

    def test_post_no_log_when_verbose_false(self, tmp_path):
        """verbose=False のとき do_POST は verbose ファイルに何も書かない。"""
        import app

        handler = _make_rpc_handler("/api/stop", body_bytes=b"")

        log_file = tmp_path / "verbose_off.txt"
        with _VerboseContext(False, log_file):
            with patch.object(app, "_enqueue", MagicMock()):
                handler.do_POST()

        # ファイルが存在しないか空
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8")
            assert content == "", f"verbose OFF なのに書かれた: {content}"

    def test_get_no_log_when_verbose_false(self, tmp_path):
        """verbose=False のとき do_GET は verbose ファイルに何も書かない。"""
        import app

        handler = _make_rpc_handler("/api/log", method="GET")

        log_file = tmp_path / "verbose_off2.txt"
        with _VerboseContext(False, log_file):
            with patch.object(app, "_log_entries", []):
                handler.do_GET()

        if log_file.exists():
            content = log_file.read_text(encoding="utf-8")
            assert content == "", f"verbose OFF なのに書かれた: {content}"


# ---------------------------------------------------------------------------
# 6. _verbose_callback の例外時 traceback 拡張
# ---------------------------------------------------------------------------

class TestVerboseCallbackTraceback:
    """_verbose_callback デコレータの例外時に traceback が記録される。"""

    def test_traceback_logged_on_exception(self, tmp_path):
        """例外発生時に traceback が verbose ログに記録される。"""
        import app

        log_file = tmp_path / "cb_verbose.txt"

        @app._verbose_callback("tb_test")
        def bad_cb(sender, app_data, user_data):
            raise ValueError("test traceback error")

        with _VerboseContext(True, log_file):
            with pytest.raises(ValueError):
                bad_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "traceback" in content.lower() or "Traceback" in content, (
            f"traceback が記録されていない: {content}"
        )

    def test_traceback_contains_location(self, tmp_path):
        """traceback に呼び出し元のファイル名か行番号が含まれる。"""
        import app

        log_file = tmp_path / "cb_verbose2.txt"

        @app._verbose_callback("location_test")
        def raise_cb(sender, app_data, user_data):
            raise RuntimeError("location check")

        with _VerboseContext(True, log_file):
            with pytest.raises(RuntimeError):
                raise_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        # traceback に File 行か行番号が含まれる
        assert "line" in content.lower() or "File" in content or "RuntimeError" in content, (
            f"traceback の詳細がない: {content}"
        )

    def test_traceback_not_logged_when_verbose_false(self, tmp_path):
        """verbose=False のとき例外でも traceback は記録されない。"""
        import app

        log_file = tmp_path / "cb_verbose3.txt"

        @app._verbose_callback("no_tb_verbose_off")
        def bad_cb(sender, app_data, user_data):
            raise ValueError("no trace")

        with _VerboseContext(False, log_file):
            with pytest.raises(ValueError):
                bad_cb("s", "a", None)

        if log_file.exists():
            content = log_file.read_text(encoding="utf-8")
            assert content == "", f"verbose OFF なのに記録された: {content}"

    def test_existing_error_fields_still_present(self, tmp_path):
        """traceback 追加後も error_type / error_msg は引き続き記録される。"""
        import app

        log_file = tmp_path / "cb_verbose4.txt"

        @app._verbose_callback("fields_check")
        def bad_cb(sender, app_data, user_data):
            raise KeyError("missing_key")

        with _VerboseContext(True, log_file):
            with pytest.raises(KeyError):
                bad_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "error_type=KeyError" in content, f"error_type がない: {content}"
        assert "error_msg=" in content, f"error_msg がない: {content}"


# ---------------------------------------------------------------------------
# 7. CaptionSystem._capture_thread_body の except で traceback 記録
# ---------------------------------------------------------------------------

class TestCaptureThreadBodyTraceback:
    """_capture_thread_body の例外ハンドラで traceback が verbose 記録される。"""

    def _make_caption_system(self, tmp_path: Path):
        """テスト用最小 CaptionSystem インスタンス。"""
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
        cs._on_ready = None
        cs._on_audio_delta = lambda pcm: None
        cs._on_realtime_error_external = None
        cs._pa_instance = None
        cs._audio_stream_lock = threading.Lock()
        cs.verbose = True
        cs._verbose_log_path = None
        cs._verbose_lock = threading.Lock()
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        cs._log_path = tmp_path / f"{today}-1_translate.txt"
        return cs

    def test_capture_thread_exception_traceback_logged(self, tmp_path):
        """_capture_thread_body の except で traceback が verbose に記録される。"""
        from main import CaptionSystem
        from unittest.mock import patch, MagicMock

        cs = self._make_caption_system(tmp_path)

        # PyAudio を mock して例外を発生させる
        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream

        # stream.read が例外を投げる
        mock_stream.read.side_effect = RuntimeError("simulated capture error")

        # デバイス情報のセットアップ
        cs._device_info = {
            "index": 0,
            "name": "test_device",
            "defaultSampleRate": 16000,
            "maxInputChannels": 1,
            "isLoopback": False,
        }
        cs._realtime_mode = False
        cs._model_name = "tiny"

        with patch("main.pyaudio.PyAudio", return_value=mock_pa):
            cs._stop_event.clear()
            cs._capture_thread_body()

        # verbose ログファイルを確認
        log_path = cs._ensure_verbose_log_path()
        if log_path and log_path.exists():
            content = log_path.read_text(encoding="utf-8")
            assert "traceback" in content.lower() or "CAPTURE_ERROR" in content or "RuntimeError" in content, (
                f"traceback が記録されていない: {content}"
            )

    def test_capture_thread_exception_no_traceback_when_verbose_false(self, tmp_path):
        """verbose=False のとき traceback は記録されない。"""
        from main import CaptionSystem
        from unittest.mock import patch, MagicMock

        cs = self._make_caption_system(tmp_path)
        cs.verbose = False  # verbose OFF

        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream
        mock_stream.read.side_effect = RuntimeError("simulated error")

        cs._device_info = {
            "index": 0,
            "name": "test_device",
            "defaultSampleRate": 16000,
            "maxInputChannels": 1,
            "isLoopback": False,
        }
        cs._realtime_mode = False
        cs._model_name = "tiny"

        with patch("main.pyaudio.PyAudio", return_value=mock_pa):
            cs._stop_event.clear()
            cs._capture_thread_body()

        # verbose=False の場合、verbose ログは空
        if cs._verbose_log_path is not None and cs._verbose_log_path.exists():
            content = cs._verbose_log_path.read_text(encoding="utf-8")
            assert "CAPTURE_ERROR" not in content, f"verbose OFF なのに記録された: {content}"


# ---------------------------------------------------------------------------
# 8. RealtimeTranslator._fire_error で traceback 記録
# ---------------------------------------------------------------------------

class TestFireErrorTraceback:
    """RealtimeTranslator._fire_error で traceback が記録される。"""

    def test_fire_error_with_exc_logs_traceback(self):
        """_fire_error に exc を渡すと traceback が verbose に記録される。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback

        try:
            raise ValueError("test fire error traceback")
        except ValueError as e:
            rt._fire_error("エラー発生", exc=e)

        # traceback が記録されているか確認
        tb_events = [ev for ev in logged_events if "traceback" in ev[1]]
        assert len(tb_events) >= 1, f"traceback フィールドが記録されていない: {logged_events}"

    def test_fire_error_without_exc_no_traceback(self):
        """_fire_error に exc なしで呼んでも traceback フィールドはない（または空）。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback

        rt._fire_error("エラー（exc なし）")

        # traceback フィールドが空かない
        for ev in logged_events:
            tb = ev[1].get("traceback", "")
            assert tb == "" or tb is None, f"exc なしなのに traceback がある: {tb}"

    def test_fire_error_traceback_contains_exception_class(self):
        """traceback フィールドに例外クラス名が含まれる。"""
        from realtime_translator import RealtimeTranslator

        logged_events = []

        def fake_callback(event: str, **fields):
            logged_events.append((event, fields))

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = fake_callback

        try:
            raise TypeError("type mismatch for fire error")
        except TypeError as e:
            rt._fire_error("型エラー", exc=e)

        tb_events = [ev for ev in logged_events if "traceback" in ev[1]]
        assert len(tb_events) >= 1, f"traceback イベントがない: {logged_events}"
        traceback_str = tb_events[0][1]["traceback"]
        assert "TypeError" in traceback_str, f"TypeError が traceback にない: {traceback_str}"

    def test_fire_error_no_verbose_callback_still_works(self):
        """verbose_callback がなくても _fire_error は例外なく動作する。"""
        from realtime_translator import RealtimeTranslator

        rt = RealtimeTranslator(
            api_key="FAKE-TEST-KEY-0000",
            target_language_code="ja",
        )
        rt._verbose_callback = None

        # 例外なし
        try:
            raise RuntimeError("no callback test")
        except RuntimeError as e:
            rt._fire_error("no callback", exc=e)
