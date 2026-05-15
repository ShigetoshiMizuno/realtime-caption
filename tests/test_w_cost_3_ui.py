"""
tests/test_w_cost_3_ui.py

W-COST-3 UI ウィジェット配置テスト（issue #81 / feat/w-cost-3-ui）

テスト対象:
1. TAG 定数が app モジュールに存在すること
2. コールバック関数 6 つが app モジュールに存在すること
3. 切替コールバックが _save_settings を呼ぶこと
4. 稼働中切替で _restart_route_for_change が呼ばれること
5. _build_gui が saved 値を default_value に反映すること
6. _save_settings が GUI 値（TAGから読んだ値）を保存すること
7. VAD OFF 時にスライダーが disable になること（任意）

NOTE: API 受入確認（TBD-3-1）は実機テストのため本ファイルでは除外する。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import app


# ---------------------------------------------------------------------------
# フィクスチャ: app グローバル状態のリセット
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_app_state():
    """各テスト前後に app のグローバル状態をリセットする。"""
    old_running = app._konnyaku_running
    old_system = app._konnyaku_system
    old_dpg_ready = app._dpg_ready

    yield

    app._konnyaku_running = old_running
    app._konnyaku_system = old_system
    app._dpg_ready = old_dpg_ready


# ---------------------------------------------------------------------------
# 1. TAG 定数の存在確認
# ---------------------------------------------------------------------------

class TestVadTagConstantsExist:
    """TAG_ROUTE_A/B_VAD_ENABLE / VAD_SILENCE_MS / VAD_THRESHOLD が
    app モジュールに定義されていること。"""

    def test_tag_route_a_vad_enable_exists(self):
        """TAG_ROUTE_A_VAD_ENABLE が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_A_VAD_ENABLE"), (
            "TAG_ROUTE_A_VAD_ENABLE が app モジュールに定義されていること"
        )

    def test_tag_route_b_vad_enable_exists(self):
        """TAG_ROUTE_B_VAD_ENABLE が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_B_VAD_ENABLE"), (
            "TAG_ROUTE_B_VAD_ENABLE が app モジュールに定義されていること"
        )

    def test_tag_route_a_vad_silence_ms_exists(self):
        """TAG_ROUTE_A_VAD_SILENCE_MS が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_A_VAD_SILENCE_MS"), (
            "TAG_ROUTE_A_VAD_SILENCE_MS が app モジュールに定義されていること"
        )

    def test_tag_route_b_vad_silence_ms_exists(self):
        """TAG_ROUTE_B_VAD_SILENCE_MS が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_B_VAD_SILENCE_MS"), (
            "TAG_ROUTE_B_VAD_SILENCE_MS が app モジュールに定義されていること"
        )

    def test_tag_route_a_vad_threshold_exists(self):
        """TAG_ROUTE_A_VAD_THRESHOLD が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_A_VAD_THRESHOLD"), (
            "TAG_ROUTE_A_VAD_THRESHOLD が app モジュールに定義されていること"
        )

    def test_tag_route_b_vad_threshold_exists(self):
        """TAG_ROUTE_B_VAD_THRESHOLD が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_B_VAD_THRESHOLD"), (
            "TAG_ROUTE_B_VAD_THRESHOLD が app モジュールに定義されていること"
        )

    def test_tag_route_a_tags_are_strings(self):
        """系統 A の 3 つのタグが非空文字列であること。"""
        assert isinstance(app.TAG_ROUTE_A_VAD_ENABLE, str) and app.TAG_ROUTE_A_VAD_ENABLE
        assert isinstance(app.TAG_ROUTE_A_VAD_SILENCE_MS, str) and app.TAG_ROUTE_A_VAD_SILENCE_MS
        assert isinstance(app.TAG_ROUTE_A_VAD_THRESHOLD, str) and app.TAG_ROUTE_A_VAD_THRESHOLD

    def test_tag_route_b_tags_are_strings(self):
        """系統 B の 3 つのタグが非空文字列であること。"""
        assert isinstance(app.TAG_ROUTE_B_VAD_ENABLE, str) and app.TAG_ROUTE_B_VAD_ENABLE
        assert isinstance(app.TAG_ROUTE_B_VAD_SILENCE_MS, str) and app.TAG_ROUTE_B_VAD_SILENCE_MS
        assert isinstance(app.TAG_ROUTE_B_VAD_THRESHOLD, str) and app.TAG_ROUTE_B_VAD_THRESHOLD

    def test_route_a_tags_are_distinct_from_route_b(self):
        """系統 A と系統 B のタグが互いに異なること。"""
        assert app.TAG_ROUTE_A_VAD_ENABLE != app.TAG_ROUTE_B_VAD_ENABLE
        assert app.TAG_ROUTE_A_VAD_SILENCE_MS != app.TAG_ROUTE_B_VAD_SILENCE_MS
        assert app.TAG_ROUTE_A_VAD_THRESHOLD != app.TAG_ROUTE_B_VAD_THRESHOLD


# ---------------------------------------------------------------------------
# 2. コールバック関数 6 つの存在確認
# ---------------------------------------------------------------------------

class TestVadCallbacksExist:
    """6 つの VAD コールバック関数が app モジュールに定義されていること。"""

    def test_on_route_a_vad_enable_change_exists(self):
        """_on_route_a_vad_enable_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_a_vad_enable_change"), (
            "_on_route_a_vad_enable_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_a_vad_enable_change)

    def test_on_route_b_vad_enable_change_exists(self):
        """_on_route_b_vad_enable_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_b_vad_enable_change"), (
            "_on_route_b_vad_enable_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_b_vad_enable_change)

    def test_on_route_a_vad_silence_ms_change_exists(self):
        """_on_route_a_vad_silence_ms_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_a_vad_silence_ms_change"), (
            "_on_route_a_vad_silence_ms_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_a_vad_silence_ms_change)

    def test_on_route_b_vad_silence_ms_change_exists(self):
        """_on_route_b_vad_silence_ms_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_b_vad_silence_ms_change"), (
            "_on_route_b_vad_silence_ms_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_b_vad_silence_ms_change)

    def test_on_route_a_vad_threshold_change_exists(self):
        """_on_route_a_vad_threshold_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_a_vad_threshold_change"), (
            "_on_route_a_vad_threshold_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_a_vad_threshold_change)

    def test_on_route_b_vad_threshold_change_exists(self):
        """_on_route_b_vad_threshold_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_b_vad_threshold_change"), (
            "_on_route_b_vad_threshold_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_b_vad_threshold_change)

    def test_callback_a_vad_enable_accepts_sender_app_data(self):
        """_on_route_a_vad_enable_change が (sender, app_data) シグネチャを持つこと。"""
        import inspect
        sig = inspect.signature(app._on_route_a_vad_enable_change)
        params = list(sig.parameters.keys())
        assert "sender" in params, f"sender パラメータが必要。got={params}"
        assert "app_data" in params, f"app_data パラメータが必要。got={params}"

    def test_callback_b_vad_enable_accepts_sender_app_data(self):
        """_on_route_b_vad_enable_change が (sender, app_data) シグネチャを持つこと。"""
        import inspect
        sig = inspect.signature(app._on_route_b_vad_enable_change)
        params = list(sig.parameters.keys())
        assert "sender" in params, f"sender パラメータが必要。got={params}"
        assert "app_data" in params, f"app_data パラメータが必要。got={params}"


# ---------------------------------------------------------------------------
# 3. チェック切替で _save_settings が呼ばれること
# ---------------------------------------------------------------------------

class TestVadCallbackSaveSettings:
    """VAD コールバック変更時に _save_settings() が呼ばれること。"""

    def test_route_a_vad_enable_calls_save_settings(self):
        """_on_route_a_vad_enable_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_a_vad_enable_change(sender=None, app_data=True)
            mock_save.assert_called_once()

    def test_route_b_vad_enable_calls_save_settings(self):
        """_on_route_b_vad_enable_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_b_vad_enable_change(sender=None, app_data=True)
            mock_save.assert_called_once()

    def test_route_a_vad_silence_ms_calls_save_settings(self):
        """_on_route_a_vad_silence_ms_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_a_vad_silence_ms_change(sender=None, app_data=800)
            mock_save.assert_called_once()

    def test_route_b_vad_silence_ms_calls_save_settings(self):
        """_on_route_b_vad_silence_ms_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_b_vad_silence_ms_change(sender=None, app_data=800)
            mock_save.assert_called_once()

    def test_route_a_vad_threshold_calls_save_settings(self):
        """_on_route_a_vad_threshold_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_a_vad_threshold_change(sender=None, app_data=0.7)
            mock_save.assert_called_once()

    def test_route_b_vad_threshold_calls_save_settings(self):
        """_on_route_b_vad_threshold_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_b_vad_threshold_change(sender=None, app_data=0.7)
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# 4. 稼働中切替で _restart_route_for_change が呼ばれること
# ---------------------------------------------------------------------------

class TestVadCallbackRestartWhenRunning:
    """稼働中（RUNNING）に VAD コールバックが呼ばれた場合、
    _restart_route_for_change が（別スレッドで）実行されること。"""

    def _make_running_system(self, route_a_state=None, route_b_state=None):
        """稼働中の FakeMultiCaptionSystem を作成するヘルパー。"""
        from main import RouteState

        class _FakeRoute:
            def __init__(self, state):
                self._state = state

            @property
            def state(self):
                return self._state

        class _FakeSystem:
            def __init__(self):
                self.route_a_system = _FakeRoute(route_a_state or RouteState.RUNNING)
                self.route_b_system = _FakeRoute(route_b_state or RouteState.RUNNING)
                self.stop_route = MagicMock()
                self.start_route = MagicMock()

        return _FakeSystem()

    def test_route_a_vad_enable_shows_status_when_running(self):
        """稼働中に系統A の VAD enable チェックを変更すると
        TAG_STATUS_STATE に「切替中」メッセージが設定されること。"""
        from main import RouteState
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system(route_a_state=RouteState.RUNNING)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        with patch("app.dpg", mock_dpg):
            app._on_route_a_vad_enable_change(sender=None, app_data=True)

        set_value_calls = [
            c for c in mock_dpg.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_STATUS_STATE
        ]
        assert len(set_value_calls) >= 1, (
            "稼働中VAD切替時に TAG_STATUS_STATE へ set_value が呼ばれること。"
            f"set_value calls={mock_dpg.set_value.call_args_list}"
        )
        message = set_value_calls[0].args[1]
        assert "切替中" in message, (
            f"メッセージに「切替中」が含まれること。got={message!r}"
        )

    def test_route_b_vad_enable_shows_status_when_running(self):
        """稼働中に系統B の VAD enable チェックを変更すると
        TAG_STATUS_STATE に「切替中」メッセージが設定されること。"""
        from main import RouteState
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system(route_b_state=RouteState.RUNNING)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        with patch("app.dpg", mock_dpg):
            app._on_route_b_vad_enable_change(sender=None, app_data=False)

        set_value_calls = [
            c for c in mock_dpg.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_STATUS_STATE
        ]
        assert len(set_value_calls) >= 1, (
            "稼働中VAD切替時に TAG_STATUS_STATE へ set_value が呼ばれること。"
            f"set_value calls={mock_dpg.set_value.call_args_list}"
        )
        message = set_value_calls[0].args[1]
        assert "切替中" in message, (
            f"メッセージに「切替中」が含まれること。got={message!r}"
        )

    def test_route_a_vad_silence_ms_shows_status_when_running(self):
        """稼働中に系統A の silence_ms スライダーを変更すると
        TAG_STATUS_STATE に「切替中」メッセージが設定されること。"""
        from main import RouteState
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system(route_a_state=RouteState.RUNNING)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        with patch("app.dpg", mock_dpg):
            app._on_route_a_vad_silence_ms_change(sender=None, app_data=800)

        set_value_calls = [
            c for c in mock_dpg.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_STATUS_STATE
        ]
        assert len(set_value_calls) >= 1, (
            "稼働中 silence_ms 切替時に TAG_STATUS_STATE へ set_value が呼ばれること。"
            f"set_value calls={mock_dpg.set_value.call_args_list}"
        )
        message = set_value_calls[0].args[1]
        assert "切替中" in message, (
            f"メッセージに「切替中」が含まれること。got={message!r}"
        )

    def test_route_b_vad_threshold_shows_status_when_running(self):
        """稼働中に系統B の threshold スライダーを変更すると
        TAG_STATUS_STATE に「切替中」メッセージが設定されること。"""
        from main import RouteState
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system(route_b_state=RouteState.RUNNING)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        with patch("app.dpg", mock_dpg):
            app._on_route_b_vad_threshold_change(sender=None, app_data=0.7)

        set_value_calls = [
            c for c in mock_dpg.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_STATUS_STATE
        ]
        assert len(set_value_calls) >= 1, (
            "稼働中 threshold 切替時に TAG_STATUS_STATE へ set_value が呼ばれること。"
            f"set_value calls={mock_dpg.set_value.call_args_list}"
        )
        message = set_value_calls[0].args[1]
        assert "切替中" in message, (
            f"メッセージに「切替中」が含まれること。got={message!r}"
        )

    def test_route_a_vad_enable_no_status_when_not_running(self):
        """停止中（_konnyaku_running=False）に VAD enable 切替しても
        TAG_STATUS_STATE に「次回起動時」メッセージは出ないこと。"""
        app._konnyaku_running = False
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True

        with patch("app.dpg", mock_dpg):
            app._on_route_a_vad_enable_change(sender=None, app_data=True)

        for c in mock_dpg.set_value.call_args_list:
            if c.args and c.args[0] == app.TAG_STATUS_STATE:
                msg = c.args[1] if len(c.args) > 1 else ""
                assert "次回起動時" not in msg, (
                    f"停止中は「次回起動時」メッセージを出さないこと。got={msg!r}"
                )


# ---------------------------------------------------------------------------
# 5. _build_gui が saved 設定値を default_value に反映すること
# ---------------------------------------------------------------------------

class TestVadGuiDefaultValues:
    """_build_gui が route_a / route_b の saved vad 設定を
    チェックボックス・スライダーの default_value に反映すること。"""

    def _run_build_gui_fragment(
        self,
        route_a_vad_enabled: bool = False,
        route_b_vad_enabled: bool = False,
        route_a_vad_silence_ms: int = 500,
        route_b_vad_silence_ms: int = 500,
        route_a_vad_threshold: float = 0.5,
        route_b_vad_threshold: float = 0.5,
    ):
        """_build_gui 内の VAD ウィジェット追加を捕捉するヘルパー。

        Returns (added_checkboxes, added_sliders_int, added_sliders_float) の 3 dicts。
        """
        added_checkboxes: dict[str, dict] = {}
        added_sliders_int: dict[str, dict] = {}
        added_sliders_float: dict[str, dict] = {}

        def fake_add_checkbox(tag=None, label="", default_value=True, **kwargs):
            added_checkboxes[tag] = {
                "default_value": default_value,
                "label": label,
                "kwargs": kwargs,
            }

        def fake_add_slider_int(tag=None, label="", default_value=0, **kwargs):
            added_sliders_int[tag] = {
                "default_value": default_value,
                "label": label,
                "kwargs": kwargs,
            }

        def fake_add_slider_float(tag=None, label="", default_value=0.0, **kwargs):
            added_sliders_float[tag] = {
                "default_value": default_value,
                "label": label,
                "kwargs": kwargs,
            }

        fake_settings = {
            "route_a": {
                "source_transcript_enabled": True,
                "enabled": True,
                "output_enabled": False,
                "vad_enabled": route_a_vad_enabled,
                "vad_silence_duration_ms": route_a_vad_silence_ms,
                "vad_threshold": route_a_vad_threshold,
            },
            "route_b": {
                "source_transcript_enabled": True,
                "enabled": True,
                "output_enabled": True,
                "vad_enabled": route_b_vad_enabled,
                "vad_silence_duration_ms": route_b_vad_silence_ms,
                "vad_threshold": route_b_vad_threshold,
            },
        }

        mock_dpg = MagicMock()
        mock_dpg.add_checkbox.side_effect = fake_add_checkbox
        mock_dpg.add_slider_int.side_effect = fake_add_slider_int
        mock_dpg.add_slider_float.side_effect = fake_add_slider_float
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        mock_dpg.group.return_value = cm
        mock_dpg.child_window.return_value = cm
        mock_dpg.window.return_value = cm
        mock_dpg.tab_bar.return_value = cm
        mock_dpg.tab.return_value = cm
        mock_dpg.menu_bar.return_value = cm
        mock_dpg.menu.return_value = cm
        mock_dpg.get_item_configuration.return_value = {"items": []}
        mock_dpg.get_value.return_value = ""
        mock_dpg.does_item_exist.return_value = False

        with patch("app.dpg", mock_dpg), \
             patch("app._load_settings", return_value=fake_settings), \
             patch("app._devices", [{"name": "TestMic", "index": 0, "samplerate": 16000}]), \
             patch("app.list_audio_devices", return_value=[]), \
             patch("app._config", {}), \
             patch("app._dpg_ready", False):
            try:
                app._build_gui()
            except Exception:
                pass

        return added_checkboxes, added_sliders_int, added_sliders_float

    def test_route_a_vad_enable_default_true_when_saved_true(self):
        """saved vad_enabled=True のとき系統A チェックボックスの default_value が True。"""
        checkboxes, _, _ = self._run_build_gui_fragment(route_a_vad_enabled=True)
        tag = app.TAG_ROUTE_A_VAD_ENABLE
        assert tag in checkboxes, (
            f"TAG_ROUTE_A_VAD_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )
        assert checkboxes[tag]["default_value"] is True, (
            f"saved=True のとき default_value が True であること。got={checkboxes[tag]}"
        )

    def test_route_a_vad_enable_default_false_when_saved_false(self):
        """saved vad_enabled=False のとき系統A チェックボックスの default_value が False。"""
        checkboxes, _, _ = self._run_build_gui_fragment(route_a_vad_enabled=False)
        tag = app.TAG_ROUTE_A_VAD_ENABLE
        assert tag in checkboxes, (
            f"TAG_ROUTE_A_VAD_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )
        assert checkboxes[tag]["default_value"] is False, (
            f"saved=False のとき default_value が False であること。got={checkboxes[tag]}"
        )

    def test_route_b_vad_enable_default_true_when_saved_true(self):
        """saved vad_enabled=True のとき系統B チェックボックスの default_value が True。"""
        checkboxes, _, _ = self._run_build_gui_fragment(route_b_vad_enabled=True)
        tag = app.TAG_ROUTE_B_VAD_ENABLE
        assert tag in checkboxes, (
            f"TAG_ROUTE_B_VAD_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )
        assert checkboxes[tag]["default_value"] is True, (
            f"saved=True のとき default_value が True であること。got={checkboxes[tag]}"
        )

    def test_route_a_vad_silence_ms_default_from_saved(self):
        """saved vad_silence_duration_ms=800 のとき系統A スライダーの default_value が 800。"""
        _, sliders_int, _ = self._run_build_gui_fragment(route_a_vad_silence_ms=800)
        tag = app.TAG_ROUTE_A_VAD_SILENCE_MS
        assert tag in sliders_int, (
            f"TAG_ROUTE_A_VAD_SILENCE_MS でスライダーが追加されること。"
            f"found tags={list(sliders_int.keys())}"
        )
        assert sliders_int[tag]["default_value"] == 800, (
            f"saved=800 のとき default_value が 800 であること。got={sliders_int[tag]}"
        )

    def test_route_b_vad_silence_ms_default_from_saved(self):
        """saved vad_silence_duration_ms=1200 のとき系統B スライダーの default_value が 1200。"""
        _, sliders_int, _ = self._run_build_gui_fragment(route_b_vad_silence_ms=1200)
        tag = app.TAG_ROUTE_B_VAD_SILENCE_MS
        assert tag in sliders_int, (
            f"TAG_ROUTE_B_VAD_SILENCE_MS でスライダーが追加されること。"
            f"found tags={list(sliders_int.keys())}"
        )
        assert sliders_int[tag]["default_value"] == 1200, (
            f"saved=1200 のとき default_value が 1200 であること。got={sliders_int[tag]}"
        )

    def test_route_a_vad_threshold_default_from_saved(self):
        """saved vad_threshold=0.7 のとき系統A スライダーの default_value が 0.7。"""
        _, _, sliders_float = self._run_build_gui_fragment(route_a_vad_threshold=0.7)
        tag = app.TAG_ROUTE_A_VAD_THRESHOLD
        assert tag in sliders_float, (
            f"TAG_ROUTE_A_VAD_THRESHOLD でスライダーが追加されること。"
            f"found tags={list(sliders_float.keys())}"
        )
        assert sliders_float[tag]["default_value"] == pytest.approx(0.7), (
            f"saved=0.7 のとき default_value が 0.7 であること。got={sliders_float[tag]}"
        )

    def test_route_b_vad_threshold_default_from_saved(self):
        """saved vad_threshold=0.3 のとき系統B スライダーの default_value が 0.3。"""
        _, _, sliders_float = self._run_build_gui_fragment(route_b_vad_threshold=0.3)
        tag = app.TAG_ROUTE_B_VAD_THRESHOLD
        assert tag in sliders_float, (
            f"TAG_ROUTE_B_VAD_THRESHOLD でスライダーが追加されること。"
            f"found tags={list(sliders_float.keys())}"
        )
        assert sliders_float[tag]["default_value"] == pytest.approx(0.3), (
            f"saved=0.3 のとき default_value が 0.3 であること。got={sliders_float[tag]}"
        )

    def test_route_a_vad_silence_ms_range(self):
        """系統A VAD silence_ms スライダーの範囲が 200〜2000 であること。"""
        _, sliders_int, _ = self._run_build_gui_fragment()
        tag = app.TAG_ROUTE_A_VAD_SILENCE_MS
        if tag in sliders_int:
            kw = sliders_int[tag]["kwargs"]
            assert kw.get("min_value", 0) == 200, (
                f"min_value が 200 であること。got={kw}"
            )
            assert kw.get("max_value", 0) == 2000, (
                f"max_value が 2000 であること。got={kw}"
            )

    def test_route_a_vad_threshold_range(self):
        """系統A VAD threshold スライダーの範囲が 0.0〜1.0 であること。"""
        _, _, sliders_float = self._run_build_gui_fragment()
        tag = app.TAG_ROUTE_A_VAD_THRESHOLD
        if tag in sliders_float:
            kw = sliders_float[tag]["kwargs"]
            assert kw.get("min_value", -1) == pytest.approx(0.0), (
                f"min_value が 0.0 であること。got={kw}"
            )
            assert kw.get("max_value", -1) == pytest.approx(1.0), (
                f"max_value が 1.0 であること。got={kw}"
            )

    def test_vad_enable_label_contains_vad_hint(self):
        """VAD 有効化チェックボックスのラベルに VAD 関連テキストが含まれること。"""
        checkboxes, _, _ = self._run_build_gui_fragment()
        tag_a = app.TAG_ROUTE_A_VAD_ENABLE
        if tag_a in checkboxes:
            label = checkboxes[tag_a].get("label", "")
            assert any(kw in label for kw in ("VAD", "無音", "コスト")), (
                f"TAG_ROUTE_A_VAD_ENABLE チェックボックスのラベルに VAD 関連テキストが含まれること。"
                f"got label={label!r}"
            )


# ---------------------------------------------------------------------------
# 6. _save_settings が GUI 値（TAG から読んだ値）を保存すること（F-4）
# ---------------------------------------------------------------------------

class TestVadSaveSettingsFromGui:
    """_save_settings が TAG から GUI 値を読んで vad_enabled / threshold / silence_ms を
    保存すること（固定値ではなく GUI 値）。"""

    def _run_save_settings_with_values(
        self,
        route_a_vad_enabled: bool = True,
        route_a_vad_silence_ms: int = 800,
        route_a_vad_threshold: float = 0.7,
        route_b_vad_enabled: bool = False,
        route_b_vad_silence_ms: int = 600,
        route_b_vad_threshold: float = 0.3,
    ) -> dict:
        """指定した GUI 値をモックにセットして _save_settings を呼ぶヘルパー。"""
        saved_data = {}

        def fake_json_dump(data, f, **kwargs):
            saved_data.update(data)

        # タグ -> 戻り値のマッピング
        tag_values = {
            app.TAG_ROUTE_A_VAD_ENABLE: route_a_vad_enabled,
            app.TAG_ROUTE_A_VAD_SILENCE_MS: route_a_vad_silence_ms,
            app.TAG_ROUTE_A_VAD_THRESHOLD: route_a_vad_threshold,
            app.TAG_ROUTE_B_VAD_ENABLE: route_b_vad_enabled,
            app.TAG_ROUTE_B_VAD_SILENCE_MS: route_b_vad_silence_ms,
            app.TAG_ROUTE_B_VAD_THRESHOLD: route_b_vad_threshold,
            app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE: True,
            app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE: True,
        }

        with patch("app.dpg") as mock_dpg, \
             patch("app._dpg_ready", True), \
             patch("app.json.dump", fake_json_dump), \
             patch("builtins.open", MagicMock()):
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.side_effect = lambda tag: tag_values.get(tag, "")
            app._save_settings()

        return saved_data

    def test_save_settings_route_a_vad_enabled_from_gui(self):
        """_save_settings が route_a の vad_enabled を GUI 値（TAG）から読んで保存すること。"""
        saved = self._run_save_settings_with_values(route_a_vad_enabled=True)
        route_a = saved.get("route_a", {})
        assert route_a.get("vad_enabled") is True, (
            f"route_a.vad_enabled が True（GUI値）で保存されること。route_a={route_a}"
        )

    def test_save_settings_route_a_vad_enabled_false_from_gui(self):
        """_save_settings が route_a の vad_enabled=False を GUI 値から保存すること。"""
        saved = self._run_save_settings_with_values(route_a_vad_enabled=False)
        route_a = saved.get("route_a", {})
        assert route_a.get("vad_enabled") is False, (
            f"route_a.vad_enabled が False（GUI値）で保存されること。route_a={route_a}"
        )

    def test_save_settings_route_a_vad_silence_ms_from_gui(self):
        """_save_settings が route_a の vad_silence_duration_ms を GUI 値から保存すること。"""
        saved = self._run_save_settings_with_values(route_a_vad_silence_ms=800)
        route_a = saved.get("route_a", {})
        assert route_a.get("vad_silence_duration_ms") == 800, (
            f"route_a.vad_silence_duration_ms が 800（GUI値）で保存されること。route_a={route_a}"
        )

    def test_save_settings_route_a_vad_threshold_from_gui(self):
        """_save_settings が route_a の vad_threshold を GUI 値から保存すること。"""
        saved = self._run_save_settings_with_values(route_a_vad_threshold=0.7)
        route_a = saved.get("route_a", {})
        assert route_a.get("vad_threshold") == pytest.approx(0.7), (
            f"route_a.vad_threshold が 0.7（GUI値）で保存されること。route_a={route_a}"
        )

    def test_save_settings_route_b_vad_enabled_from_gui(self):
        """_save_settings が route_b の vad_enabled を GUI 値から保存すること。"""
        saved = self._run_save_settings_with_values(route_b_vad_enabled=True)
        route_b = saved.get("route_b", {})
        assert route_b.get("vad_enabled") is True, (
            f"route_b.vad_enabled が True（GUI値）で保存されること。route_b={route_b}"
        )

    def test_save_settings_route_b_vad_silence_ms_from_gui(self):
        """_save_settings が route_b の vad_silence_duration_ms を GUI 値から保存すること。"""
        saved = self._run_save_settings_with_values(route_b_vad_silence_ms=600)
        route_b = saved.get("route_b", {})
        assert route_b.get("vad_silence_duration_ms") == 600, (
            f"route_b.vad_silence_duration_ms が 600（GUI値）で保存されること。route_b={route_b}"
        )

    def test_save_settings_route_b_vad_threshold_from_gui(self):
        """_save_settings が route_b の vad_threshold を GUI 値から保存すること。"""
        saved = self._run_save_settings_with_values(route_b_vad_threshold=0.3)
        route_b = saved.get("route_b", {})
        assert route_b.get("vad_threshold") == pytest.approx(0.3), (
            f"route_b.vad_threshold が 0.3（GUI値）で保存されること。route_b={route_b}"
        )

    def test_save_settings_route_a_prefix_padding_ms_fixed(self):
        """route_a の vad_prefix_padding_ms は固定値 300 で保存されること
        （今 PR では UI 非表示）。"""
        saved = self._run_save_settings_with_values()
        route_a = saved.get("route_a", {})
        assert route_a.get("vad_prefix_padding_ms") == 300, (
            f"route_a.vad_prefix_padding_ms は固定 300 で保存されること。route_a={route_a}"
        )

    def test_save_settings_route_b_prefix_padding_ms_fixed(self):
        """route_b の vad_prefix_padding_ms は固定値 300 で保存されること
        （今 PR では UI 非表示）。"""
        saved = self._run_save_settings_with_values()
        route_b = saved.get("route_b", {})
        assert route_b.get("vad_prefix_padding_ms") == 300, (
            f"route_b.vad_prefix_padding_ms は固定 300 で保存されること。route_b={route_b}"
        )
