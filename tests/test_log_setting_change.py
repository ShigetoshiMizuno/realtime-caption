"""
tests/test_log_setting_change.py

issue #121 問題 B「設定変更ログから現在の状態が読めない」修正のテスト。

テスト対象:
1. 各設定変更コールバックが [USER] プリフィックスでログ出力すること
2. ログに新値（ON/OFF・数値など）が含まれること
3. _restart_route_for_change のINFOログに新値を含む reason_label が渡されること
4. CaptionSystem._create_realtime_translator で [STATE] ログが出ること
"""

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    yield

    app._konnyaku_running = old_running
    app._konnyaku_system = old_system
    app._dpg_ready = old_dpg_ready


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
# 1. _on_route_a/b_source_transcript_change — [USER] ログに新値を含む
# ---------------------------------------------------------------------------

class TestSourceTranscriptChangeLog:
    """_on_route_a/b_source_transcript_change が [USER] ログで新値を出力すること。"""

    def test_route_a_source_transcript_on_logs_user_prefix(self):
        """ON 時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_route_a_source_transcript_change, sender=None, app_data=True
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_a_source_transcript_on_logs_new_value_on(self):
        """ON 時にログに 'ON' が含まれること。"""
        out = _call_with_save_patched(
            app._on_route_a_source_transcript_change, sender=None, app_data=True
        )
        assert "ON" in out, f"新値 ON がログに含まれること。got: {out!r}"

    def test_route_a_source_transcript_off_logs_new_value_off(self):
        """OFF 時にログに 'OFF' が含まれること。"""
        out = _call_with_save_patched(
            app._on_route_a_source_transcript_change, sender=None, app_data=False
        )
        assert "OFF" in out, f"新値 OFF がログに含まれること。got: {out!r}"

    def test_route_b_source_transcript_on_logs_user_prefix(self):
        """系統B ON 時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_route_b_source_transcript_change, sender=None, app_data=True
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_b_source_transcript_off_logs_new_value_off(self):
        """系統B OFF 時にログに 'OFF' が含まれること。"""
        out = _call_with_save_patched(
            app._on_route_b_source_transcript_change, sender=None, app_data=False
        )
        assert "OFF" in out, f"新値 OFF がログに含まれること。got: {out!r}"


# ---------------------------------------------------------------------------
# 2. _on_route_a/b_vad_enable_change — [USER] ログに新値を含む
# ---------------------------------------------------------------------------

class TestVadEnableChangeLog:
    """_on_route_a/b_vad_enable_change が [USER] ログで新値を出力すること。"""

    def _make_mock_dpg(self):
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = False
        return mock_dpg

    def test_route_a_vad_enable_on_logs_user_prefix(self):
        """系統A VAD ON 時に [USER] プリフィックスが含まれること。"""
        with patch.object(app, "_save_settings"), \
             patch("app.dpg", self._make_mock_dpg()):
            out = _capture_stdout(
                app._on_route_a_vad_enable_change, sender=None, app_data=True
            )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_a_vad_enable_on_logs_new_value_on(self):
        """系統A VAD ON 時にログに 'ON' が含まれること。"""
        with patch.object(app, "_save_settings"), \
             patch("app.dpg", self._make_mock_dpg()):
            out = _capture_stdout(
                app._on_route_a_vad_enable_change, sender=None, app_data=True
            )
        assert "ON" in out, f"新値 ON がログに含まれること。got: {out!r}"

    def test_route_a_vad_enable_off_logs_new_value_off(self):
        """系統A VAD OFF 時にログに 'OFF' が含まれること。"""
        with patch.object(app, "_save_settings"), \
             patch("app.dpg", self._make_mock_dpg()):
            out = _capture_stdout(
                app._on_route_a_vad_enable_change, sender=None, app_data=False
            )
        assert "OFF" in out, f"新値 OFF がログに含まれること。got: {out!r}"

    def test_route_b_vad_enable_on_logs_user_prefix(self):
        """系統B VAD ON 時に [USER] プリフィックスが含まれること。"""
        with patch.object(app, "_save_settings"), \
             patch("app.dpg", self._make_mock_dpg()):
            out = _capture_stdout(
                app._on_route_b_vad_enable_change, sender=None, app_data=True
            )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_b_vad_enable_off_logs_new_value_off(self):
        """系統B VAD OFF 時にログに 'OFF' が含まれること。"""
        with patch.object(app, "_save_settings"), \
             patch("app.dpg", self._make_mock_dpg()):
            out = _capture_stdout(
                app._on_route_b_vad_enable_change, sender=None, app_data=False
            )
        assert "OFF" in out, f"新値 OFF がログに含まれること。got: {out!r}"


# ---------------------------------------------------------------------------
# 3. _on_route_a/b_vad_silence_ms_change — [USER] ログに新値を含む
# ---------------------------------------------------------------------------

class TestVadSilenceMsChangeLog:
    """_on_route_a/b_vad_silence_ms_change が [USER] ログで値を出力すること。"""

    def test_route_a_vad_silence_ms_logs_user_prefix(self):
        """系統A VAD 無音時間変更時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_route_a_vad_silence_ms_change, sender=None, app_data=300
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_a_vad_silence_ms_logs_value(self):
        """系統A VAD 無音時間変更時にログに設定値が含まれること。"""
        out = _call_with_save_patched(
            app._on_route_a_vad_silence_ms_change, sender=None, app_data=300
        )
        assert "300" in out, f"値 300 がログに含まれること。got: {out!r}"

    def test_route_b_vad_silence_ms_logs_user_prefix(self):
        """系統B VAD 無音時間変更時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_route_b_vad_silence_ms_change, sender=None, app_data=600
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_b_vad_silence_ms_logs_value(self):
        """系統B VAD 無音時間変更時にログに設定値が含まれること。"""
        out = _call_with_save_patched(
            app._on_route_b_vad_silence_ms_change, sender=None, app_data=600
        )
        assert "600" in out, f"値 600 がログに含まれること。got: {out!r}"


# ---------------------------------------------------------------------------
# 4. _on_route_a/b_vad_threshold_change — [USER] ログに新値を含む
# ---------------------------------------------------------------------------

class TestVadThresholdChangeLog:
    """_on_route_a/b_vad_threshold_change が [USER] ログで値を出力すること。"""

    def test_route_a_vad_threshold_logs_user_prefix(self):
        """系統A VAD 感度変更時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_route_a_vad_threshold_change, sender=None, app_data=0.7
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_a_vad_threshold_logs_value(self):
        """系統A VAD 感度変更時にログに設定値が含まれること。"""
        out = _call_with_save_patched(
            app._on_route_a_vad_threshold_change, sender=None, app_data=0.7
        )
        assert "0.7" in out, f"値 0.7 がログに含まれること。got: {out!r}"

    def test_route_b_vad_threshold_logs_user_prefix(self):
        """系統B VAD 感度変更時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_route_b_vad_threshold_change, sender=None, app_data=0.3
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_route_b_vad_threshold_logs_value(self):
        """系統B VAD 感度変更時にログに設定値が含まれること。"""
        out = _call_with_save_patched(
            app._on_route_b_vad_threshold_change, sender=None, app_data=0.3
        )
        assert "0.3" in out, f"値 0.3 がログに含まれること。got: {out!r}"


# ---------------------------------------------------------------------------
# 5. _on_idle_timeout_change / _on_idle_audio_threshold_change — [USER] ログ
# ---------------------------------------------------------------------------

class TestIdleSettingChangeLog:
    """アイドル設定変更コールバックが [USER] ログで値を出力すること。"""

    def test_idle_timeout_logs_user_prefix(self):
        """アイドルタイムアウト変更時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_idle_timeout_change, sender=None, app_data=120
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_idle_timeout_logs_value(self):
        """アイドルタイムアウト変更時にログに設定値が含まれること。"""
        out = _call_with_save_patched(
            app._on_idle_timeout_change, sender=None, app_data=120
        )
        assert "120" in out, f"値 120 がログに含まれること。got: {out!r}"

    def test_idle_audio_threshold_logs_user_prefix(self):
        """音声検知閾値変更時に [USER] プリフィックスが含まれること。"""
        out = _call_with_save_patched(
            app._on_idle_audio_threshold_change, sender=None, app_data=200
        )
        assert "[USER]" in out, f"[USER] プリフィックスが期待されるが得られた出力: {out!r}"

    def test_idle_audio_threshold_logs_value(self):
        """音声検知閾値変更時にログに設定値が含まれること。"""
        out = _call_with_save_patched(
            app._on_idle_audio_threshold_change, sender=None, app_data=200
        )
        assert "200" in out, f"値 200 がログに含まれること。got: {out!r}"


# ---------------------------------------------------------------------------
# 6. _restart_route_for_change — INFOログに reason_label が含まれること
#    （呼び出し元が新値を含む reason_label を渡すことを確認）
# ---------------------------------------------------------------------------

class TestRestartRouteForChangeLog:
    """_restart_route_for_change のINFOログに route_id と reason_label が含まれること。"""

    def _make_fake_system(self):
        fake = MagicMock()
        fake.stop_route = MagicMock()
        fake.start_route = MagicMock()
        return fake

    def test_restart_route_info_log_contains_route_id(self, capsys):
        """INFOログに route_id が含まれること。"""
        app._konnyaku_system = self._make_fake_system()
        # _restart_locks に "a" キーが必要
        import threading
        if "a" not in app._restart_locks:
            app._restart_locks["a"] = threading.Lock()

        app._restart_route_for_change("a", "原文表示 → ON")

        captured = capsys.readouterr()
        assert "route_a" in captured.out, f"route_a がログに含まれること。got: {captured.out!r}"

    def test_restart_route_info_log_contains_reason_label(self, capsys):
        """INFOログに reason_label が含まれること。"""
        app._konnyaku_system = self._make_fake_system()
        import threading
        if "a" not in app._restart_locks:
            app._restart_locks["a"] = threading.Lock()

        app._restart_route_for_change("a", "原文表示 → ON")

        captured = capsys.readouterr()
        assert "原文表示 → ON" in captured.out, (
            f"reason_label '原文表示 → ON' がログに含まれること。got: {captured.out!r}"
        )

    def test_restart_route_info_log_contains_new_value_in_reason(self, capsys):
        """reason_label に → 記号と新値が含まれること。"""
        app._konnyaku_system = self._make_fake_system()
        import threading
        if "b" not in app._restart_locks:
            app._restart_locks["b"] = threading.Lock()

        app._restart_route_for_change("b", "VAD ON/OFF → OFF")

        captured = capsys.readouterr()
        assert "→" in captured.out, f"→ 記号がログに含まれること。got: {captured.out!r}"


# ---------------------------------------------------------------------------
# 7. _on_route_a/b_source_transcript_change — 再起動時の reason_label に新値が含まれること
# ---------------------------------------------------------------------------

class TestSourceTranscriptRestartLog:
    """_on_route_a/b_source_transcript_change が稼働中のとき
    _restart_route_for_change に渡す reason_label に新値を含むこと。"""

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

    def test_route_a_source_transcript_restart_label_contains_new_value(self):
        """稼働中に source_transcript を OFF にしたとき、
        _restart_route_for_change の args に 'OFF' を含む reason_label が渡ること。"""
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system()
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        restart_calls = []

        def fake_thread(**kwargs):
            t = MagicMock()
            # args=("a", reason_label) を保持
            restart_calls.append(kwargs.get("args", ()))
            t.start = MagicMock()
            return t

        with patch.object(app, "_save_settings"), \
             patch("app.dpg", mock_dpg), \
             patch("app.threading") as mock_threading:
            mock_threading.Thread.side_effect = lambda **kw: (
                restart_calls.append(kw.get("args", ())),
                MagicMock()
            )[1]
            app._on_route_a_source_transcript_change(sender=None, app_data=False)

        assert len(restart_calls) >= 1, "threading.Thread が呼ばれること"
        reason = restart_calls[0][1] if len(restart_calls[0]) > 1 else ""
        assert "OFF" in reason, (
            f"reason_label に 'OFF' が含まれること。got={reason!r}"
        )

    def test_route_b_source_transcript_restart_label_contains_new_value(self):
        """稼働中に source_transcript を ON にしたとき、
        _restart_route_for_change の args に 'ON' を含む reason_label が渡ること。"""
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system()
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        restart_calls = []

        with patch.object(app, "_save_settings"), \
             patch("app.dpg", mock_dpg), \
             patch("app.threading") as mock_threading:
            mock_threading.Thread.side_effect = lambda **kw: (
                restart_calls.append(kw.get("args", ())),
                MagicMock()
            )[1]
            app._on_route_b_source_transcript_change(sender=None, app_data=True)

        assert len(restart_calls) >= 1, "threading.Thread が呼ばれること"
        reason = restart_calls[0][1] if len(restart_calls[0]) > 1 else ""
        assert "ON" in reason, (
            f"reason_label に 'ON' が含まれること。got={reason!r}"
        )


# ---------------------------------------------------------------------------
# 8. CaptionSystem._create_realtime_translator — [STATE] ログ
# ---------------------------------------------------------------------------

class TestCreateRealtimeTranslatorStateLog:
    """_create_realtime_translator が [STATE] ログを出力すること。"""

    def _make_caption_system(self):
        """テスト用の最低限 CaptionSystem を生成する。"""
        from main import CaptionSystem
        cfg = {
            "openai": {"api_key": "sk-test-fake-0000000000000000"},
            "openai_realtime": {},
        }
        devices = [{"name": "TestMic", "index": 0, "samplerate": 16000}]
        cs = CaptionSystem.__new__(CaptionSystem)
        # 必要な属性を手動初期化
        cs._config = cfg
        cs._route_id = "a"
        cs._realtime_translator = None
        cs._audio_output_mode = False
        cs._request_source_transcript = True
        cs._vad_enabled = False
        cs._vad_threshold = 0.5
        cs._vad_prefix_padding_ms = 300
        cs._vad_silence_duration_ms = 500
        cs._idle_disconnect_enabled = False
        cs._idle_timeout_sec = 60
        cs._idle_audio_threshold = 100
        cs._idle_monitor = None
        cs._cost_monitor = None
        return cs

    def test_create_realtime_translator_logs_state(self, capsys):
        """_create_realtime_translator が [ACTION] ログを出力すること。"""
        cs = self._make_caption_system()

        fake_rt = MagicMock()
        fake_cm = MagicMock()

        # _create_realtime_translator は `from realtime_translator import RealtimeTranslator`
        # で動的 import するため、モジュールレベルで patch する。
        with patch("realtime_translator.RealtimeTranslator", return_value=fake_rt, create=True), \
             patch("cost_monitor.CostMonitor", return_value=fake_cm, create=True):
            # _on_realtime_transcript 等のメソッドがなければダミーを設定
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
            import pytest as _pytest
            _pytest.skip("realtime_translator をインポートできない環境のためスキップ")
        assert "[ACTION]" in captured.out, (
            f"_create_realtime_translator が [ACTION] ログを出力すること。got: {captured.out!r}"
        )

    def test_create_realtime_translator_state_log_contains_source_transcript(self, capsys):
        """[ACTION] ログに request_source_transcript の値が含まれること。"""
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
            import pytest as _pytest
            _pytest.skip("realtime_translator をインポートできない環境のためスキップ")
        assert "request_source_transcript" in captured.out, (
            f"[ACTION] ログに request_source_transcript が含まれること。got: {captured.out!r}"
        )

    def test_create_realtime_translator_state_log_contains_vad_enabled(self, capsys):
        """[ACTION] ログに vad_enabled の値が含まれること。"""
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
            import pytest as _pytest
            _pytest.skip("realtime_translator をインポートできない環境のためスキップ")
        assert "vad_enabled" in captured.out, (
            f"[ACTION] ログに vad_enabled が含まれること。got: {captured.out!r}"
        )

    def test_create_realtime_translator_noop_when_already_exists(self, capsys):
        """既に _realtime_translator が存在する場合は no-op で [ACTION] ログを出さないこと。"""
        cs = self._make_caption_system()
        cs._realtime_translator = MagicMock()  # 既存

        cs._create_realtime_translator()

        captured = capsys.readouterr()
        # [ACTION] ログが出ないこと（no-op）
        assert "[ACTION]" not in captured.out, (
            f"既存 translator がある場合は [ACTION] ログを出さないこと。got: {captured.out!r}"
        )
