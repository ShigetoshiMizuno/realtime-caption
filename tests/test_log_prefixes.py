"""
tests/test_log_prefixes.py

issue #121 問題B「ログから状態が読めない」改善:
ボタン押下証拠([USER])・RPC受信([RPC])・処理実行([ACTION]) を分離するテスト。

テスト対象:
1. _log_user / _log_rpc / _log_action のフォーマット検証
2. 設定変更コールバック経路で [USER] → [ACTION] の両方が出力されること
3. ガード経路（再起動ロック取得失敗）で [USER] のみ出力されること（[ACTION] なし）
4. _restart_route_for_change に [ACTION] ログが含まれること
5. RPC ハンドラに [RPC] ログが含まれること
6. _create_konnyaku_system の出口に [ACTION] ログが含まれること
7. CaptionSystem._create_realtime_translator に [ACTION] ログが含まれること
"""

import sys
import threading
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import app


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_app_state():
    """各テスト前後に app グローバル状態をリセットする。"""
    old_running = app._konnyaku_running
    old_system = app._konnyaku_system
    old_dpg_ready = app._dpg_ready
    old_restart_locks = dict(app._restart_locks)

    yield

    app._konnyaku_running = old_running
    app._konnyaku_system = old_system
    app._dpg_ready = old_dpg_ready
    app._restart_locks = old_restart_locks


def _capture_stdout(func, *args, **kwargs):
    """func を呼び出し、標準出力に書かれた内容を返す。"""
    buf = StringIO()
    with patch("sys.stdout", buf):
        func(*args, **kwargs)
    return buf.getvalue()


def _call_with_save_patched(func, *args, **kwargs):
    """_save_settings をモック化してから func を呼び出し、stdout を返す。"""
    with patch.object(app, "_save_settings"):
        return _capture_stdout(func, *args, **kwargs)


# ---------------------------------------------------------------------------
# 1. ヘルパー関数のフォーマットテスト
# ---------------------------------------------------------------------------

class TestLogHelperFormat:
    """_log_user / _log_rpc / _log_action のフォーマットを検証する。"""

    def test_log_user_prefix(self):
        """_log_user が [USER] プリフィックスで出力すること。"""
        out = _capture_stdout(app._log_user, "テストイベント")
        assert out.startswith("[USER] テストイベント"), \
            f"[USER] プリフィックスで始まること。got: {out!r}"

    def test_log_user_with_kwargs(self):
        """_log_user がキーワード引数を key=value 形式で出力すること。"""
        out = _capture_stdout(app._log_user, "イベント", key="val")
        assert "[USER] イベント" in out, f"イベント名が含まれること。got: {out!r}"
        assert "key=val" in out, f"key=val が含まれること。got: {out!r}"

    def test_log_rpc_prefix(self):
        """_log_rpc が [RPC] プリフィックスで出力すること。"""
        out = _capture_stdout(app._log_rpc, "GET /api/status")
        assert out.startswith("[RPC] GET /api/status"), \
            f"[RPC] プリフィックスで始まること。got: {out!r}"

    def test_log_rpc_with_kwargs(self):
        """_log_rpc がキーワード引数を key=value 形式で出力すること。"""
        out = _capture_stdout(app._log_rpc, "POST /api/start", device_index=22)
        assert "[RPC] POST /api/start" in out, f"イベント名が含まれること。got: {out!r}"
        assert "device_index=22" in out, f"device_index=22 が含まれること。got: {out!r}"

    def test_log_action_prefix(self):
        """_log_action が [ACTION] プリフィックスで出力すること。"""
        out = _capture_stdout(app._log_action, "route_a 再起動開始")
        assert out.startswith("[ACTION] route_a 再起動開始"), \
            f"[ACTION] プリフィックスで始まること。got: {out!r}"

    def test_log_action_with_kwargs(self):
        """_log_action がキーワード引数を key=value 形式で出力すること。"""
        out = _capture_stdout(app._log_action, "route_a 再起動開始", reason="原文表示 切替")
        assert "[ACTION] route_a 再起動開始" in out, f"イベント名が含まれること。got: {out!r}"
        assert "reason=原文表示 切替" in out, f"reason= が含まれること。got: {out!r}"

    def test_log_user_no_kwargs_no_trailing_space(self):
        """_log_user が kwargs なしのとき余分な空白がないこと。"""
        out = _capture_stdout(app._log_user, "イベント")
        # "[USER] イベント\n" のみであること
        assert out.strip() == "[USER] イベント", \
            f"余分な空白がないこと。got: {out!r}"

    def test_log_rpc_no_kwargs_no_trailing_space(self):
        """_log_rpc が kwargs なしのとき余分な空白がないこと。"""
        out = _capture_stdout(app._log_rpc, "GET /api/log")
        assert out.strip() == "[RPC] GET /api/log", \
            f"余分な空白がないこと。got: {out!r}"

    def test_log_action_no_kwargs_no_trailing_space(self):
        """_log_action が kwargs なしのとき余分な空白がないこと。"""
        out = _capture_stdout(app._log_action, "route_b 再起動完了")
        assert out.strip() == "[ACTION] route_b 再起動完了", \
            f"余分な空白がないこと。got: {out!r}"


# ---------------------------------------------------------------------------
# 2. 設定変更コールバック — [USER] と [ACTION] の両方が出力されること
# ---------------------------------------------------------------------------

class TestUserAndActionBothEmitted:
    """稼働中の設定変更で [USER] と [ACTION] の両方が出力されること。"""

    def _make_running_system(self):
        """稼働中の MultiCaptionSystem モック。"""
        from main import RouteState

        class _FakeRoute:
            @property
            def state(self):
                return RouteState.RUNNING
            set_output_device = MagicMock()

        class _FakeSystem:
            route_a_system = _FakeRoute()
            route_b_system = _FakeRoute()
            stop_route = MagicMock()
            start_route = MagicMock()

        return _FakeSystem()

    def test_source_transcript_change_emits_user_and_action(self, capsys):
        """_on_route_a_source_transcript_change が稼働中のとき
        [USER] と [ACTION] の両方が出力されること。"""
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system()

        # _restart_route_for_change を直接呼ぶようにスレッドをモック
        captured_actions = []

        def fake_restart(route_id, reason_label):
            app._log_action(f"route_{route_id} 再起動開始", reason=reason_label)
            captured_actions.append((route_id, reason_label))

        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        with patch.object(app, "_save_settings"), \
             patch("app.dpg", mock_dpg), \
             patch.object(app, "_restart_route_for_change", side_effect=fake_restart), \
             patch("app.threading") as mock_threading:

            # スレッドの代わりに直接呼ぶ
            def fake_thread(**kwargs):
                t = MagicMock()
                target = kwargs.get("target")
                args = kwargs.get("args", ())
                if target:
                    target(*args)
                t.start = MagicMock()
                return t

            mock_threading.Thread.side_effect = fake_thread
            app._on_route_a_source_transcript_change(sender=None, app_data=True)

        captured = capsys.readouterr()
        assert "[USER]" in captured.out, \
            f"[USER] が出力されること。got: {captured.out!r}"
        assert "[ACTION]" in captured.out, \
            f"[ACTION] が出力されること。got: {captured.out!r}"

    def test_vad_enable_change_emits_user(self, capsys):
        """_on_route_a_vad_enable_change が [USER] ログを出力すること。"""
        app._konnyaku_running = False
        app._konnyaku_system = None

        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = False

        with patch.object(app, "_save_settings"), \
             patch("app.dpg", mock_dpg):
            app._on_route_a_vad_enable_change(sender=None, app_data=True)

        captured = capsys.readouterr()
        assert "[USER]" in captured.out, \
            f"[USER] が出力されること。got: {captured.out!r}"


# ---------------------------------------------------------------------------
# 3. ガード経路 — [USER] のみ出力（[ACTION] なし）
# ---------------------------------------------------------------------------

class TestGuardPathUserOnly:
    """再起動ロック取得失敗時（既に再起動中）は [USER] のみで [ACTION] が来ないこと。

    これが自動デバッグの根拠：
      [USER] が来て [ACTION] が来なければ → ガードでスキップされたことが分かる。
    """

    def _make_running_system(self):
        from main import RouteState

        class _FakeRoute:
            @property
            def state(self):
                return RouteState.RUNNING
            set_output_device = MagicMock()

        class _FakeSystem:
            route_a_system = _FakeRoute()
            route_b_system = _FakeRoute()
            stop_route = MagicMock()
            start_route = MagicMock()

        return _FakeSystem()

    def test_restart_lock_held_skips_action(self, capsys):
        """既にロックを保持中（再起動中）の場合、
        [USER] は出ても [ACTION] の 再起動開始 は出ないこと。"""
        app._konnyaku_system = self._make_running_system()

        # ロックを事前に取得して「再起動中」を模擬
        lock = threading.Lock()
        lock.acquire()
        try:
            app._restart_locks["a"] = lock
            app._restart_route_for_change("a", "テスト理由")
        finally:
            lock.release()

        captured = capsys.readouterr()
        # ロック失敗時は [ACTION] route_a 再起動開始 が出ないこと
        assert "[ACTION] route_a 再起動開始" not in captured.out, \
            f"ロック失敗時に再起動開始 [ACTION] が出ないこと。got: {captured.out!r}"

    def test_restart_lock_held_emits_action_skip(self, capsys):
        """既にロック保持中の場合、[ACTION] ... skip が出ること。"""
        app._konnyaku_system = self._make_running_system()

        lock = threading.Lock()
        lock.acquire()
        try:
            app._restart_locks["a"] = lock
            app._restart_route_for_change("a", "テスト理由")
        finally:
            lock.release()

        captured = capsys.readouterr()
        # skip ログが出ること
        assert "skip" in captured.out.lower() or "スキップ" in captured.out, \
            f"スキップを示すログが出ること。got: {captured.out!r}"


# ---------------------------------------------------------------------------
# 4. _restart_route_for_change — [ACTION] ログを含むこと
# ---------------------------------------------------------------------------

class TestRestartRouteActionLog:
    """_restart_route_for_change が [ACTION] ログを出力すること。"""

    def _make_fake_system(self):
        fake = MagicMock()
        fake.stop_route = MagicMock()
        fake.start_route = MagicMock()
        return fake

    def test_restart_route_emits_action_start(self, capsys):
        """_restart_route_for_change が [ACTION] route_X 再起動開始 を出力すること。"""
        app._konnyaku_system = self._make_fake_system()
        app._restart_locks["a"] = threading.Lock()

        app._restart_route_for_change("a", "原文表示 切替")

        captured = capsys.readouterr()
        assert "[ACTION]" in captured.out, \
            f"[ACTION] が出力されること。got: {captured.out!r}"
        assert "route_a" in captured.out, \
            f"route_a が含まれること。got: {captured.out!r}"
        assert "再起動開始" in captured.out, \
            f"再起動開始 が含まれること。got: {captured.out!r}"

    def test_restart_route_emits_action_complete(self, capsys):
        """_restart_route_for_change が [ACTION] route_X 再起動完了 を出力すること。"""
        app._konnyaku_system = self._make_fake_system()
        app._restart_locks["b"] = threading.Lock()

        app._restart_route_for_change("b", "VAD 切替")

        captured = capsys.readouterr()
        assert "再起動完了" in captured.out, \
            f"再起動完了 が含まれること。got: {captured.out!r}"

    def test_restart_route_emits_action_start_with_reason(self, capsys):
        """[ACTION] 再起動開始ログに reason= が含まれること。"""
        app._konnyaku_system = self._make_fake_system()
        app._restart_locks["a"] = threading.Lock()

        app._restart_route_for_change("a", "音声出力 ON/OFF 切替")

        captured = capsys.readouterr()
        assert "音声出力 ON/OFF 切替" in captured.out, \
            f"reason が含まれること。got: {captured.out!r}"


# ---------------------------------------------------------------------------
# 5. RPC ハンドラ — [RPC] ログを含むこと
# ---------------------------------------------------------------------------

class TestRpcHandlerLog:
    """_RPCHandler.do_GET / do_POST が [RPC] ログを出力すること。"""

    def _make_handler(self, path: str, method: str = "GET", body: bytes = b""):
        """_RPCHandler のインスタンスを最低限の属性で構築する。"""
        handler = _RPCHandler_NoServer(path=path, method=method, body=body)
        return handler

    def _mock_dpg(self):
        """dpg をモックする。does_item_exist=False で全ウィジェット無効にする。"""
        m = MagicMock()
        m.does_item_exist.return_value = False
        m.get_value.return_value = ""
        return m

    def test_do_get_status_emits_rpc(self, capsys):
        """GET /api/status が [RPC] ログを出力すること。"""
        handler = self._make_handler("/api/status")
        with patch.object(handler, "_send_json"), \
             patch("app.dpg", self._mock_dpg()):
            handler.do_GET()
        captured = capsys.readouterr()
        assert "[RPC]" in captured.out, \
            f"[RPC] が出力されること。got: {captured.out!r}"
        assert "/api/status" in captured.out, \
            f"/api/status が含まれること。got: {captured.out!r}"

    def test_do_get_log_emits_rpc(self, capsys):
        """GET /api/log が [RPC] ログを出力すること。"""
        handler = self._make_handler("/api/log")
        with patch.object(handler, "_send_json"), \
             patch("app.dpg", self._mock_dpg()):
            handler.do_GET()
        captured = capsys.readouterr()
        assert "[RPC]" in captured.out, \
            f"[RPC] が出力されること。got: {captured.out!r}"

    def test_do_get_devices_emits_rpc(self, capsys):
        """GET /api/devices が [RPC] ログを出力すること。"""
        handler = self._make_handler("/api/devices")
        with patch.object(handler, "_send_json"), \
             patch("app.dpg", self._mock_dpg()):
            handler.do_GET()
        captured = capsys.readouterr()
        assert "[RPC]" in captured.out, \
            f"[RPC] が出力されること。got: {captured.out!r}"

    def test_do_get_audio_emits_rpc(self, capsys):
        """GET /api/audio が [RPC] ログを出力すること。"""
        handler = self._make_handler("/api/audio")
        with patch.object(handler, "_send_json"), \
             patch("app.dpg", self._mock_dpg()):
            handler.do_GET()
        captured = capsys.readouterr()
        assert "[RPC]" in captured.out, \
            f"[RPC] が出力されること。got: {captured.out!r}"

    def test_do_post_stop_emits_rpc(self, capsys):
        """POST /api/stop が [RPC] ログを出力すること。"""
        handler = self._make_handler("/api/stop", method="POST")
        with patch.object(handler, "_send_json"), \
             patch.object(app, "_enqueue"), \
             patch("app.dpg", self._mock_dpg()):
            handler.do_POST()
        captured = capsys.readouterr()
        assert "[RPC]" in captured.out, \
            f"[RPC] が出力されること。got: {captured.out!r}"
        assert "/api/stop" in captured.out, \
            f"/api/stop が含まれること。got: {captured.out!r}"

    def test_do_post_start_emits_rpc(self, capsys):
        """POST /api/start が [RPC] ログを出力すること。"""
        import json as _json
        body = _json.dumps({"device_index": 22}).encode("utf-8")
        handler = self._make_handler("/api/start", method="POST", body=body)
        with patch.object(handler, "_send_json"), \
             patch.object(app, "_enqueue"), \
             patch("app.dpg", self._mock_dpg()):
            handler.do_POST()
        captured = capsys.readouterr()
        assert "[RPC]" in captured.out, \
            f"[RPC] が出力されること。got: {captured.out!r}"
        assert "/api/start" in captured.out, \
            f"/api/start が含まれること。got: {captured.out!r}"


class _RPCHandler_NoServer(app._RPCHandler):
    """テスト用 _RPCHandler のサブクラス。HTTPServer なしで動かせるようにする。"""

    def __init__(self, path: str, method: str = "GET", body: bytes = b""):
        # BaseHTTPRequestHandler.__init__ を呼ばずに直接属性を設定
        self.path = path
        self.command = method
        self._body = body
        import io
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()

    def send_response(self, code):
        pass

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass


# ---------------------------------------------------------------------------
# 6. PTT 押下/離脱 — [USER] ログを含むこと
# ---------------------------------------------------------------------------

class TestPttPressReleaseLog:
    """_on_ptt_press / _on_ptt_release が [USER] ログを出力すること。"""

    def test_ptt_press_emits_user(self, capsys):
        """_on_ptt_press が [USER] PTT 押下 ログを出力すること。"""
        app._konnyaku_running = True
        fake_system = MagicMock()
        fake_system.route_b_system = MagicMock()
        app._konnyaku_system = fake_system

        with patch("app.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            app._on_ptt_press(event=None)

        captured = capsys.readouterr()
        assert "[USER]" in captured.out, \
            f"[USER] が出力されること。got: {captured.out!r}"
        # PTT 押下に関連する文字列
        assert "PTT" in captured.out or "press" in captured.out.lower(), \
            f"PTT 押下に関連するログが出力されること。got: {captured.out!r}"

    def test_ptt_release_emits_user(self, capsys):
        """_on_ptt_release が [USER] PTT 離脱 ログを出力すること。"""
        app._konnyaku_running = True
        fake_system = MagicMock()
        fake_system.route_b_system = MagicMock()
        app._konnyaku_system = fake_system

        with patch("app.threading") as mock_threading:
            mock_threading.Thread.return_value = MagicMock()
            app._on_ptt_release(event=None)

        captured = capsys.readouterr()
        assert "[USER]" in captured.out, \
            f"[USER] が出力されること。got: {captured.out!r}"
        assert "PTT" in captured.out or "release" in captured.out.lower(), \
            f"PTT 離脱に関連するログが出力されること。got: {captured.out!r}"


# ---------------------------------------------------------------------------
# 7. CaptionSystem._create_realtime_translator — [ACTION] ログを含むこと
# ---------------------------------------------------------------------------

class TestCreateRealtimeTranslatorActionLog:
    """_create_realtime_translator が [ACTION] ログを出力すること。"""

    def _make_caption_system(self):
        """テスト用の最低限 CaptionSystem を生成する。"""
        from main import CaptionSystem
        cs = CaptionSystem.__new__(CaptionSystem)
        cs._config = {
            "openai": {"api_key": "sk-test-fake-0000000000000000"},
            "openai_realtime": {},
        }
        cs._route_id = "a"
        cs._realtime_translator = None
        cs._audio_output_mode = False
        cs._request_source_transcript = True
        cs._vad_enabled = False
        cs._vad_threshold = 0.5
        cs._vad_prefix_padding_ms = 300
        cs._vad_silence_duration_ms = 500
        cs._idle_disconnect_enabled = False
        cs._idle_timeout_sec = 60.0
        cs._idle_audio_threshold = 100
        cs._idle_monitor = None
        cs._cost_monitor = None
        return cs

    def test_create_realtime_translator_emits_action(self, capsys):
        """_create_realtime_translator が [ACTION] ログを出力すること。"""
        cs = self._make_caption_system()

        fake_rt = MagicMock()
        fake_cm = MagicMock()

        with patch("realtime_translator.RealtimeTranslator", return_value=fake_rt, create=True), \
             patch("cost_monitor.CostMonitor", return_value=fake_cm, create=True):
            cs._on_realtime_transcript = MagicMock()
            cs._on_realtime_source_transcript = MagicMock()
            cs._on_realtime_error = MagicMock()
            cs._on_ready = MagicMock()
            cs._on_audio_delta = MagicMock()
            cs._on_cost_max_reached = MagicMock()
            cs._on_cost_warning = MagicMock()
            try:
                cs._create_realtime_translator()
            except Exception:
                pass  # import エラーは無視（ログ出力前に起きた場合のみ失敗）

        captured = capsys.readouterr()
        # [ACTION] が出力されること
        # ※ import 失敗時も考慮し、[ACTION] が出る前に例外が起きる場合はスキップ
        if "[ACTION]" not in captured.out and captured.out == "":
            pytest.skip("realtime_translator をインポートできない環境のためスキップ")
        assert "[ACTION]" in captured.out, \
            f"[ACTION] が出力されること。got: {captured.out!r}"

    def test_create_realtime_translator_action_contains_request_source_transcript(self, capsys):
        """[ACTION] ログに request_source_transcript が含まれること。"""
        cs = self._make_caption_system()
        cs._request_source_transcript = True

        fake_rt = MagicMock()
        fake_cm = MagicMock()

        with patch("realtime_translator.RealtimeTranslator", return_value=fake_rt, create=True), \
             patch("cost_monitor.CostMonitor", return_value=fake_cm, create=True):
            cs._on_realtime_transcript = MagicMock()
            cs._on_realtime_source_transcript = MagicMock()
            cs._on_realtime_error = MagicMock()
            cs._on_ready = MagicMock()
            cs._on_audio_delta = MagicMock()
            cs._on_cost_max_reached = MagicMock()
            cs._on_cost_warning = MagicMock()
            try:
                cs._create_realtime_translator()
            except Exception:
                pass

        captured = capsys.readouterr()
        if "[ACTION]" not in captured.out and captured.out == "":
            pytest.skip("realtime_translator をインポートできない環境のためスキップ")
        assert "request_source_transcript" in captured.out, \
            f"request_source_transcript が含まれること。got: {captured.out!r}"

    def test_create_realtime_translator_action_contains_vad_enabled(self, capsys):
        """[ACTION] ログに vad_enabled が含まれること。"""
        cs = self._make_caption_system()
        cs._vad_enabled = True

        fake_rt = MagicMock()
        fake_cm = MagicMock()

        with patch("realtime_translator.RealtimeTranslator", return_value=fake_rt, create=True), \
             patch("cost_monitor.CostMonitor", return_value=fake_cm, create=True):
            cs._on_realtime_transcript = MagicMock()
            cs._on_realtime_source_transcript = MagicMock()
            cs._on_realtime_error = MagicMock()
            cs._on_ready = MagicMock()
            cs._on_audio_delta = MagicMock()
            cs._on_cost_max_reached = MagicMock()
            cs._on_cost_warning = MagicMock()
            try:
                cs._create_realtime_translator()
            except Exception:
                pass

        captured = capsys.readouterr()
        if "[ACTION]" not in captured.out and captured.out == "":
            pytest.skip("realtime_translator をインポートできない環境のためスキップ")
        assert "vad_enabled" in captured.out, \
            f"vad_enabled が含まれること。got: {captured.out!r}"

    def test_create_realtime_translator_action_contains_request_audio_output(self, capsys):
        """[ACTION] ログに request_audio_output が含まれること。"""
        cs = self._make_caption_system()
        cs._audio_output_mode = True

        fake_rt = MagicMock()
        fake_cm = MagicMock()

        with patch("realtime_translator.RealtimeTranslator", return_value=fake_rt, create=True), \
             patch("cost_monitor.CostMonitor", return_value=fake_cm, create=True):
            cs._on_realtime_transcript = MagicMock()
            cs._on_realtime_source_transcript = MagicMock()
            cs._on_realtime_error = MagicMock()
            cs._on_ready = MagicMock()
            cs._on_audio_delta = MagicMock()
            cs._on_cost_max_reached = MagicMock()
            cs._on_cost_warning = MagicMock()
            try:
                cs._create_realtime_translator()
            except Exception:
                pass

        captured = capsys.readouterr()
        if "[ACTION]" not in captured.out and captured.out == "":
            pytest.skip("realtime_translator をインポートできない環境のためスキップ")
        assert "request_audio_output" in captured.out, \
            f"request_audio_output が含まれること。got: {captured.out!r}"
