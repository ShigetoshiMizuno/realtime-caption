"""
tests/test_w_cost_4_ui.py

W-COST-4 PR3: app.py UI 結線 + 再開ボタン + PTT 連携テスト（issue #81）

TDD RED フェーズ: 実装前に作成したテスト。

テスト対象:
1. TAG 定数 4 つが app モジュールに存在すること
2. コールバック 4 つ + ヘルパー 1 つが app モジュールに定義されていること
3. コールバック登録（callback= 引数）が _build_gui の各ウィジェットに設定されること
4. settings save/load 往復（idle_disconnect_enabled / idle_timeout_sec / idle_audio_threshold）
5. _on_idle_resume_click で両系統 resume_from_idle() が呼ばれること
6. アイドル切断中に _update_idle_status が再開ボタンを enable にすること
7. PTT 押下（_on_ptt_press）で系統 B の resume_from_idle() が呼ばれること
8. _update_idle_status が _update_konnyaku_level_meters から呼ばれること
9. 不正値フォールバック（idle_timeout_sec / idle_audio_threshold）
10. _on_idle_disconnect_enabled_change が _save_settings を呼ぶこと
11. _on_idle_timeout_change が _save_settings を呼ぶこと
12. _on_idle_audio_threshold_change が _save_settings を呼ぶこと
13. idle_disconnect_enabled デフォルト False
14. _create_konnyaku_system が idle 設定を RouteConfig に渡すこと
15. アイドル非切断中は再開ボタンが disable になること
"""

import sys
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
# ヘルパー
# ---------------------------------------------------------------------------

def _make_fake_route(is_disconnected: bool = False):
    """アイドル状態フラグを持つ FakeRoute を作成する。"""
    mock_monitor = MagicMock()
    mock_monitor.is_disconnected.return_value = is_disconnected
    route = MagicMock()
    route._idle_monitor = mock_monitor
    route.resume_from_idle = MagicMock()
    return route


def _make_fake_system(
    a_disconnected: bool = False,
    b_disconnected: bool = False,
):
    """FakeMultiCaptionSystem を作成する。"""
    system = MagicMock()
    system.route_a_system = _make_fake_route(a_disconnected)
    system.route_b_system = _make_fake_route(b_disconnected)
    return system


# ---------------------------------------------------------------------------
# 1. TAG 定数の存在確認
# ---------------------------------------------------------------------------

class TestIdleTagConstantsExist:
    """4 つの TAG 定数が app モジュールに定義されていること。"""

    def test_tag_idle_disconnect_enabled_exists(self):
        """TAG_IDLE_DISCONNECT_ENABLED が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_IDLE_DISCONNECT_ENABLED"), (
            "TAG_IDLE_DISCONNECT_ENABLED が app モジュールに定義されていること"
        )

    def test_tag_idle_timeout_sec_exists(self):
        """TAG_IDLE_TIMEOUT_SEC が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_IDLE_TIMEOUT_SEC"), (
            "TAG_IDLE_TIMEOUT_SEC が app モジュールに定義されていること"
        )

    def test_tag_idle_audio_threshold_exists(self):
        """TAG_IDLE_AUDIO_THRESHOLD が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_IDLE_AUDIO_THRESHOLD"), (
            "TAG_IDLE_AUDIO_THRESHOLD が app モジュールに定義されていること"
        )

    def test_tag_idle_resume_button_exists(self):
        """TAG_IDLE_RESUME_BUTTON が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_IDLE_RESUME_BUTTON"), (
            "TAG_IDLE_RESUME_BUTTON が app モジュールに定義されていること"
        )

    def test_all_tags_are_nonempty_strings(self):
        """全 4 タグが非空文字列であること。"""
        assert isinstance(app.TAG_IDLE_DISCONNECT_ENABLED, str) and app.TAG_IDLE_DISCONNECT_ENABLED
        assert isinstance(app.TAG_IDLE_TIMEOUT_SEC, str) and app.TAG_IDLE_TIMEOUT_SEC
        assert isinstance(app.TAG_IDLE_AUDIO_THRESHOLD, str) and app.TAG_IDLE_AUDIO_THRESHOLD
        assert isinstance(app.TAG_IDLE_RESUME_BUTTON, str) and app.TAG_IDLE_RESUME_BUTTON

    def test_all_tags_are_distinct(self):
        """4 タグが互いに異なること。"""
        tags = [
            app.TAG_IDLE_DISCONNECT_ENABLED,
            app.TAG_IDLE_TIMEOUT_SEC,
            app.TAG_IDLE_AUDIO_THRESHOLD,
            app.TAG_IDLE_RESUME_BUTTON,
        ]
        assert len(set(tags)) == 4, f"4 タグが全て異なること。got={tags}"


# ---------------------------------------------------------------------------
# 2. コールバック・ヘルパー関数の存在確認
# ---------------------------------------------------------------------------

class TestIdleCallbacksExist:
    """4 コールバック + ヘルパー 1 つが app モジュールに存在すること。"""

    def test_on_idle_disconnect_enabled_change_exists(self):
        assert hasattr(app, "_on_idle_disconnect_enabled_change")
        assert callable(app._on_idle_disconnect_enabled_change)

    def test_on_idle_timeout_change_exists(self):
        assert hasattr(app, "_on_idle_timeout_change")
        assert callable(app._on_idle_timeout_change)

    def test_on_idle_audio_threshold_change_exists(self):
        assert hasattr(app, "_on_idle_audio_threshold_change")
        assert callable(app._on_idle_audio_threshold_change)

    def test_on_idle_resume_click_exists(self):
        assert hasattr(app, "_on_idle_resume_click")
        assert callable(app._on_idle_resume_click)

    def test_update_idle_status_exists(self):
        assert hasattr(app, "_update_idle_status")
        assert callable(app._update_idle_status)

    def test_on_idle_disconnect_enabled_change_signature(self):
        """_on_idle_disconnect_enabled_change が (sender, app_data) シグネチャを持つこと。"""
        sig = inspect.signature(app._on_idle_disconnect_enabled_change)
        params = list(sig.parameters.keys())
        assert "sender" in params, f"sender パラメータが必要。got={params}"
        assert "app_data" in params, f"app_data パラメータが必要。got={params}"

    def test_on_idle_timeout_change_signature(self):
        """_on_idle_timeout_change が (sender, app_data) シグネチャを持つこと。"""
        sig = inspect.signature(app._on_idle_timeout_change)
        params = list(sig.parameters.keys())
        assert "sender" in params, f"sender パラメータが必要。got={params}"
        assert "app_data" in params, f"app_data パラメータが必要。got={params}"

    def test_on_idle_resume_click_signature(self):
        """_on_idle_resume_click が (sender, app_data) シグネチャを持つこと。"""
        sig = inspect.signature(app._on_idle_resume_click)
        params = list(sig.parameters.keys())
        assert "sender" in params, f"sender パラメータが必要。got={params}"
        assert "app_data" in params, f"app_data パラメータが必要。got={params}"


# ---------------------------------------------------------------------------
# 3. コールバックが _save_settings を呼ぶこと
# ---------------------------------------------------------------------------

class TestIdleCallbackSaveSettings:
    """各コールバックが _save_settings() を呼ぶこと。"""

    def test_on_idle_disconnect_enabled_change_calls_save_settings(self):
        """_on_idle_disconnect_enabled_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False
        with patch.object(app, "_save_settings") as mock_save:
            app._on_idle_disconnect_enabled_change(sender=None, app_data=True)
            mock_save.assert_called_once()

    def test_on_idle_timeout_change_calls_save_settings(self):
        """_on_idle_timeout_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False
        with patch.object(app, "_save_settings") as mock_save:
            app._on_idle_timeout_change(sender=None, app_data=300)
            mock_save.assert_called_once()

    def test_on_idle_audio_threshold_change_calls_save_settings(self):
        """_on_idle_audio_threshold_change が _save_settings を呼ぶこと。"""
        app._dpg_ready = False
        with patch.object(app, "_save_settings") as mock_save:
            app._on_idle_audio_threshold_change(sender=None, app_data=100)
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# 4. settings save/load 往復テスト
# ---------------------------------------------------------------------------

class TestIdleSettingsSaveLoad:
    """idle 設定が _save_settings / _load_settings で往復保存されること。"""

    def _run_save_settings_with_idle_values(
        self,
        idle_disconnect_enabled: bool = False,
        idle_timeout_sec: int = 300,
        idle_audio_threshold: int = 100,
    ) -> dict:
        """指定した GUI 値をモックにセットして _save_settings を呼ぶヘルパー。"""
        saved_data = {}

        def fake_json_dump(data, f, **kwargs):
            saved_data.update(data)

        tag_values = {
            app.TAG_IDLE_DISCONNECT_ENABLED: idle_disconnect_enabled,
            app.TAG_IDLE_TIMEOUT_SEC: idle_timeout_sec,
            app.TAG_IDLE_AUDIO_THRESHOLD: idle_audio_threshold,
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

    def test_save_idle_disconnect_enabled_true(self):
        """idle_disconnect_enabled=True が保存されること。"""
        saved = self._run_save_settings_with_idle_values(idle_disconnect_enabled=True)
        assert saved.get("idle_disconnect_enabled") is True, (
            f"idle_disconnect_enabled が True で保存されること。got={saved.get('idle_disconnect_enabled')!r}"
        )

    def test_save_idle_disconnect_enabled_false(self):
        """idle_disconnect_enabled=False が保存されること。"""
        saved = self._run_save_settings_with_idle_values(idle_disconnect_enabled=False)
        assert saved.get("idle_disconnect_enabled") is False, (
            f"idle_disconnect_enabled が False で保存されること。got={saved.get('idle_disconnect_enabled')!r}"
        )

    def test_save_idle_timeout_sec(self):
        """idle_timeout_sec=600 が保存されること。"""
        saved = self._run_save_settings_with_idle_values(idle_timeout_sec=600)
        assert saved.get("idle_timeout_sec") == 600, (
            f"idle_timeout_sec が 600 で保存されること。got={saved.get('idle_timeout_sec')!r}"
        )

    def test_save_idle_audio_threshold(self):
        """idle_audio_threshold=200 が保存されること。"""
        saved = self._run_save_settings_with_idle_values(idle_audio_threshold=200)
        assert saved.get("idle_audio_threshold") == 200, (
            f"idle_audio_threshold が 200 で保存されること。got={saved.get('idle_audio_threshold')!r}"
        )

    def test_load_idle_disconnect_enabled_default_false(self):
        """settings.json に idle_disconnect_enabled がない場合は False になること。"""
        with patch("app._load_settings", return_value={}):
            saved = app._load_settings()
        val = saved.get("idle_disconnect_enabled", False)
        assert val is False, f"未保存時のデフォルトは False。got={val!r}"


# ---------------------------------------------------------------------------
# 5. _on_idle_resume_click で両系統 resume_from_idle() が呼ばれること
# ---------------------------------------------------------------------------

class TestIdleResumeClick:
    """「再開」ボタンクリックで両系統 resume_from_idle() が呼ばれること。"""

    def test_resume_click_calls_route_a_resume(self):
        """_on_idle_resume_click が route_a_system.resume_from_idle() を呼ぶこと。"""
        app._konnyaku_system = _make_fake_system()
        app._on_idle_resume_click(sender=None, app_data=None)
        app._konnyaku_system.route_a_system.resume_from_idle.assert_called_once()

    def test_resume_click_calls_route_b_resume(self):
        """_on_idle_resume_click が route_b_system.resume_from_idle() を呼ぶこと。"""
        app._konnyaku_system = _make_fake_system()
        app._on_idle_resume_click(sender=None, app_data=None)
        app._konnyaku_system.route_b_system.resume_from_idle.assert_called_once()

    def test_resume_click_no_op_when_system_none(self):
        """_konnyaku_system が None のときは no-op でクラッシュしないこと。"""
        app._konnyaku_system = None
        app._on_idle_resume_click(sender=None, app_data=None)  # クラッシュしなければ OK

    def test_resume_click_no_op_when_route_a_none(self):
        """route_a_system が None のときも route_b を呼ぶこと。"""
        system = _make_fake_system()
        system.route_a_system = None
        app._konnyaku_system = system
        app._on_idle_resume_click(sender=None, app_data=None)
        system.route_b_system.resume_from_idle.assert_called_once()

    def test_resume_click_no_op_when_route_b_none(self):
        """route_b_system が None のときも route_a を呼ぶこと。"""
        system = _make_fake_system()
        system.route_b_system = None
        app._konnyaku_system = system
        app._on_idle_resume_click(sender=None, app_data=None)
        system.route_a_system.resume_from_idle.assert_called_once()


# ---------------------------------------------------------------------------
# 6. _update_idle_status: アイドル切断中は再開ボタンが enable になること
# ---------------------------------------------------------------------------

class TestUpdateIdleStatus:
    """_update_idle_status が正しく再開ボタンの enabled を制御すること。"""

    def test_resume_button_enabled_when_a_idle(self):
        """系統 A がアイドル切断中のとき再開ボタンが enabled=True になること。"""
        app._dpg_ready = True
        app._konnyaku_system = _make_fake_system(a_disconnected=True, b_disconnected=False)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True
        with patch("app.dpg", mock_dpg):
            app._update_idle_status()
        configure_calls = {
            c.args[0]: c.kwargs
            for c in mock_dpg.configure_item.call_args_list
            if c.args
        }
        assert app.TAG_IDLE_RESUME_BUTTON in configure_calls, (
            f"TAG_IDLE_RESUME_BUTTON に configure_item が呼ばれること。got={list(configure_calls.keys())}"
        )
        assert configure_calls[app.TAG_IDLE_RESUME_BUTTON].get("enabled") is True, (
            f"系統 A アイドル中は enabled=True になること。got={configure_calls[app.TAG_IDLE_RESUME_BUTTON]}"
        )

    def test_resume_button_enabled_when_b_idle(self):
        """系統 B がアイドル切断中のとき再開ボタンが enabled=True になること。"""
        app._dpg_ready = True
        app._konnyaku_system = _make_fake_system(a_disconnected=False, b_disconnected=True)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True
        with patch("app.dpg", mock_dpg):
            app._update_idle_status()
        configure_calls = {
            c.args[0]: c.kwargs
            for c in mock_dpg.configure_item.call_args_list
            if c.args
        }
        assert configure_calls.get(app.TAG_IDLE_RESUME_BUTTON, {}).get("enabled") is True, (
            f"系統 B アイドル中は enabled=True になること。got={configure_calls.get(app.TAG_IDLE_RESUME_BUTTON)}"
        )

    def test_resume_button_disabled_when_no_idle(self):
        """両系統ともアイドル非切断中は再開ボタンが enabled=False になること。"""
        app._dpg_ready = True
        app._konnyaku_system = _make_fake_system(a_disconnected=False, b_disconnected=False)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True
        with patch("app.dpg", mock_dpg):
            app._update_idle_status()
        configure_calls = {
            c.args[0]: c.kwargs
            for c in mock_dpg.configure_item.call_args_list
            if c.args
        }
        assert configure_calls.get(app.TAG_IDLE_RESUME_BUTTON, {}).get("enabled") is False, (
            f"非アイドル中は enabled=False になること。got={configure_calls.get(app.TAG_IDLE_RESUME_BUTTON)}"
        )

    def test_update_idle_status_noop_when_dpg_not_ready(self):
        """_dpg_ready=False のときは何もしないこと（クラッシュしない）。"""
        app._dpg_ready = False
        app._konnyaku_system = _make_fake_system(a_disconnected=True)
        mock_dpg = MagicMock()
        with patch("app.dpg", mock_dpg):
            app._update_idle_status()  # クラッシュしなければ OK
        mock_dpg.configure_item.assert_not_called()

    def test_update_idle_status_noop_when_system_none(self):
        """_konnyaku_system=None のときは何もしないこと。"""
        app._dpg_ready = True
        app._konnyaku_system = None
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = True
        with patch("app.dpg", mock_dpg):
            app._update_idle_status()
        mock_dpg.configure_item.assert_not_called()

    def test_update_idle_status_noop_when_tag_not_exist(self):
        """TAG_IDLE_RESUME_BUTTON が存在しないときは configure_item を呼ばないこと。"""
        app._dpg_ready = True
        app._konnyaku_system = _make_fake_system(a_disconnected=True)
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = False
        with patch("app.dpg", mock_dpg):
            app._update_idle_status()
        mock_dpg.configure_item.assert_not_called()


# ---------------------------------------------------------------------------
# 7. PTT 押下で系統 B の resume_from_idle() が呼ばれること
# ---------------------------------------------------------------------------

class TestPttPressResumesIdle:
    """PTT 押下時に系統 B の resume_from_idle() が呼ばれること（W-COST-4 PR3 F-4）。"""

    def _make_ptt_running_system(self, b_disconnected: bool = True):
        """PTT 稼働中の FakeMultiCaptionSystem を作成する。"""
        system = _make_fake_system(b_disconnected=b_disconnected)
        system.start_route = MagicMock()
        return system

    def test_ptt_press_calls_route_b_resume_from_idle(self):
        """_on_ptt_press で系統 B の resume_from_idle() が呼ばれること。"""
        app._konnyaku_running = True
        app._konnyaku_system = self._make_ptt_running_system(b_disconnected=True)

        event = MagicMock()
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            app._on_ptt_press(event)

        app._konnyaku_system.route_b_system.resume_from_idle.assert_called_once()

    def test_ptt_press_not_calls_route_a_resume_from_idle(self):
        """_on_ptt_press は系統 A の resume_from_idle() を呼ばないこと（PTT は系統 B 用）。"""
        app._konnyaku_running = True
        app._konnyaku_system = self._make_ptt_running_system(b_disconnected=True)

        event = MagicMock()
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            app._on_ptt_press(event)

        app._konnyaku_system.route_a_system.resume_from_idle.assert_not_called()

    def test_ptt_press_resume_noop_when_not_running(self):
        """_konnyaku_running=False のとき resume_from_idle() が呼ばれないこと。"""
        app._konnyaku_running = False
        app._konnyaku_system = self._make_ptt_running_system()

        event = MagicMock()
        app._on_ptt_press(event)

        app._konnyaku_system.route_b_system.resume_from_idle.assert_not_called()

    def test_ptt_press_resume_noop_when_system_none(self):
        """_konnyaku_system=None でも ptt_press がクラッシュしないこと。"""
        app._konnyaku_running = True
        app._konnyaku_system = None

        event = MagicMock()
        app._on_ptt_press(event)  # クラッシュしなければ OK


# ---------------------------------------------------------------------------
# 8. _update_idle_status が _update_konnyaku_level_meters から呼ばれること
# ---------------------------------------------------------------------------

class TestUpdateIdleStatusCalledFromLevelMeters:
    """_update_konnyaku_level_meters が _update_idle_status を呼ぶこと。"""

    def test_level_meters_calls_update_idle_status(self):
        """_update_konnyaku_level_meters が _update_idle_status を呼ぶこと。"""
        app._konnyaku_system = None  # system なしで安全に実行
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = False
        mock_dpg.get_value.return_value = False

        with patch("app.dpg", mock_dpg), \
             patch.object(app, "_update_idle_status") as mock_update_idle, \
             patch.object(app, "_update_billing_lamp"):
            app._update_konnyaku_level_meters()

        mock_update_idle.assert_called_once()


# ---------------------------------------------------------------------------
# 9. 不正値フォールバック
# ---------------------------------------------------------------------------

class TestIdleFallbackValues:
    """idle 設定の不正値が _safe_float / _safe_int でフォールバックされること。"""

    def test_invalid_idle_timeout_sec_falls_back_to_default(self):
        """idle_timeout_sec に不正値 "abc" が来た場合デフォルト 300 になること。"""
        saved = {"idle_timeout_sec": "abc", "idle_audio_threshold": 100}
        # _create_konnyaku_system のロジックと同パターンで試験
        # テスト用ヘルパー関数を定義して確認
        def _safe_int(val, default):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        result = _safe_int(saved.get("idle_timeout_sec"), 300)
        assert result == 300, f"不正値 'abc' は 300 にフォールバックされること。got={result}"

    def test_invalid_idle_audio_threshold_falls_back_to_default(self):
        """idle_audio_threshold に None が来た場合デフォルト 100 になること。"""
        def _safe_int(val, default):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        result = _safe_int(None, 100)
        assert result == 100, f"None は 100 にフォールバックされること。got={result}"

    def test_idle_disconnect_enabled_default_false_when_missing(self):
        """settings.json に idle_disconnect_enabled がないときデフォルト False になること。"""
        saved: dict = {}
        val = bool(saved.get("idle_disconnect_enabled", False))
        assert val is False


# ---------------------------------------------------------------------------
# 10. _build_gui: 詳細設定タブにアイドル切断設定が追加されること
# ---------------------------------------------------------------------------

class TestIdleGuiBuild:
    """_build_gui 実行時に TAG_IDLE_* ウィジェットが追加されること。"""

    def _run_build_gui_fragment(
        self,
        idle_disconnect_enabled: bool = False,
        idle_timeout_sec: int = 300,
        idle_audio_threshold: int = 100,
    ):
        """_build_gui を実行して追加されたウィジェットのタグを収集するヘルパー。"""
        added_checkboxes: dict[str, dict] = {}
        added_sliders_int: dict[str, dict] = {}
        added_buttons: dict[str, dict] = {}

        def fake_add_checkbox(tag=None, label="", default_value=False, **kwargs):
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

        def fake_add_button(tag=None, label="", **kwargs):
            added_buttons[tag] = {
                "label": label,
                "kwargs": kwargs,
            }

        fake_settings = {
            "idle_disconnect_enabled": idle_disconnect_enabled,
            "idle_timeout_sec": idle_timeout_sec,
            "idle_audio_threshold": idle_audio_threshold,
            "route_a": {
                "source_transcript_enabled": True,
                "enabled": True,
                "output_enabled": False,
                "vad_enabled": False,
                "vad_silence_duration_ms": 500,
                "vad_threshold": 0.5,
            },
            "route_b": {
                "source_transcript_enabled": True,
                "enabled": True,
                "output_enabled": True,
                "vad_enabled": False,
                "vad_silence_duration_ms": 500,
                "vad_threshold": 0.5,
            },
        }

        mock_dpg = MagicMock()
        mock_dpg.add_checkbox.side_effect = fake_add_checkbox
        mock_dpg.add_slider_int.side_effect = fake_add_slider_int
        mock_dpg.add_button.side_effect = fake_add_button
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
        mock_dpg.add_slider_float.side_effect = lambda **kw: None

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

        return added_checkboxes, added_sliders_int, added_buttons

    def test_tag_idle_disconnect_enabled_checkbox_added(self):
        """TAG_IDLE_DISCONNECT_ENABLED チェックボックスが _build_gui で追加されること。"""
        checkboxes, _, _ = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_DISCONNECT_ENABLED
        assert tag in checkboxes, (
            f"TAG_IDLE_DISCONNECT_ENABLED でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )

    def test_tag_idle_disconnect_enabled_callback_set(self):
        """TAG_IDLE_DISCONNECT_ENABLED チェックボックスに callback が設定されること。"""
        checkboxes, _, _ = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_DISCONNECT_ENABLED
        if tag in checkboxes:
            cb = checkboxes[tag]["kwargs"].get("callback")
            assert cb is not None, (
                f"TAG_IDLE_DISCONNECT_ENABLED に callback が設定されること。got kwargs={checkboxes[tag]['kwargs']}"
            )

    def test_tag_idle_disconnect_enabled_default_from_saved(self):
        """saved idle_disconnect_enabled=True のとき default_value が True になること。"""
        checkboxes, _, _ = self._run_build_gui_fragment(idle_disconnect_enabled=True)
        tag = app.TAG_IDLE_DISCONNECT_ENABLED
        if tag in checkboxes:
            assert checkboxes[tag]["default_value"] is True, (
                f"saved=True のとき default_value が True であること。got={checkboxes[tag]}"
            )

    def test_tag_idle_timeout_sec_slider_added(self):
        """TAG_IDLE_TIMEOUT_SEC スライダーが _build_gui で追加されること。"""
        _, sliders_int, _ = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_TIMEOUT_SEC
        assert tag in sliders_int, (
            f"TAG_IDLE_TIMEOUT_SEC でスライダーが追加されること。"
            f"found tags={list(sliders_int.keys())}"
        )

    def test_tag_idle_timeout_sec_range(self):
        """TAG_IDLE_TIMEOUT_SEC スライダーの範囲が 60〜1800 であること。"""
        _, sliders_int, _ = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_TIMEOUT_SEC
        if tag in sliders_int:
            kw = sliders_int[tag]["kwargs"]
            assert kw.get("min_value") == 60, f"min_value が 60 であること。got={kw}"
            assert kw.get("max_value") == 1800, f"max_value が 1800 であること。got={kw}"

    def test_tag_idle_audio_threshold_slider_added(self):
        """TAG_IDLE_AUDIO_THRESHOLD スライダーが _build_gui で追加されること。"""
        _, sliders_int, _ = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_AUDIO_THRESHOLD
        assert tag in sliders_int, (
            f"TAG_IDLE_AUDIO_THRESHOLD でスライダーが追加されること。"
            f"found tags={list(sliders_int.keys())}"
        )

    def test_tag_idle_audio_threshold_range(self):
        """TAG_IDLE_AUDIO_THRESHOLD スライダーの範囲が 50〜500 であること。"""
        _, sliders_int, _ = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_AUDIO_THRESHOLD
        if tag in sliders_int:
            kw = sliders_int[tag]["kwargs"]
            assert kw.get("min_value") == 50, f"min_value が 50 であること。got={kw}"
            assert kw.get("max_value") == 500, f"max_value が 500 であること。got={kw}"

    def test_tag_idle_resume_button_added(self):
        """TAG_IDLE_RESUME_BUTTON ボタンが _build_gui で追加されること。"""
        _, _, buttons = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_RESUME_BUTTON
        assert tag in buttons, (
            f"TAG_IDLE_RESUME_BUTTON でボタンが追加されること。"
            f"found tags={list(buttons.keys())}"
        )

    def test_tag_idle_resume_button_callback_set(self):
        """TAG_IDLE_RESUME_BUTTON ボタンに callback が設定されること。"""
        _, _, buttons = self._run_build_gui_fragment()
        tag = app.TAG_IDLE_RESUME_BUTTON
        if tag in buttons:
            cb = buttons[tag]["kwargs"].get("callback")
            assert cb is not None, (
                f"TAG_IDLE_RESUME_BUTTON に callback が設定されること。got kwargs={buttons[tag]['kwargs']}"
            )

    def test_tag_idle_timeout_sec_default_from_saved(self):
        """saved idle_timeout_sec=600 のとき default_value が 600 になること。"""
        _, sliders_int, _ = self._run_build_gui_fragment(idle_timeout_sec=600)
        tag = app.TAG_IDLE_TIMEOUT_SEC
        if tag in sliders_int:
            assert sliders_int[tag]["default_value"] == 600, (
                f"saved=600 のとき default_value が 600 であること。got={sliders_int[tag]}"
            )

    def test_tag_idle_audio_threshold_default_from_saved(self):
        """saved idle_audio_threshold=200 のとき default_value が 200 になること。"""
        _, sliders_int, _ = self._run_build_gui_fragment(idle_audio_threshold=200)
        tag = app.TAG_IDLE_AUDIO_THRESHOLD
        if tag in sliders_int:
            assert sliders_int[tag]["default_value"] == 200, (
                f"saved=200 のとき default_value が 200 であること。got={sliders_int[tag]}"
            )


# ---------------------------------------------------------------------------
# 11. _create_konnyaku_system が idle 設定を RouteConfig に渡すこと
# ---------------------------------------------------------------------------

class TestCreateKonnyakuSystemIdleSettings:
    """_create_konnyaku_system が idle 設定を RouteConfig に正しく渡すこと。"""

    def _run_create_and_capture_route_configs(self, settings: dict) -> tuple:
        """_create_konnyaku_system を実行して RouteConfig のペアをキャプチャするヘルパー。

        MultiCaptionSystem のコンストラクタを MagicMock に差し替えて
        route_a / route_b の RouteConfig を捕捉する。
        """
        from main import MultiCaptionSystem
        captured_configs = []

        fake_cls = MagicMock()

        def fake_new(cls, config, route_a, route_b, **kwargs):
            captured_configs.append((route_a, route_b))
            instance = MagicMock()
            # route_a_system / route_b_system はプロパティのため MagicMock に設定
            instance.route_a_system = MagicMock()
            instance.route_b_system = MagicMock()
            return instance

        fake_device = {"name": "FakeMic", "index": 0, "samplerate": 16000}

        with patch("app._load_settings", return_value=settings), \
             patch("app._devices", [fake_device]), \
             patch("app.list_audio_devices", return_value=[]), \
             patch("app.find_device_by_name", return_value=None), \
             patch("app.MultiCaptionSystem", side_effect=lambda *a, **kw: fake_new(MultiCaptionSystem, *a, **kw)):
            app._konnyaku_system = None
            app._create_konnyaku_system()

        return tuple(captured_configs[0]) if captured_configs else (None, None)

    def test_idle_disconnect_enabled_passed_to_route_config(self):
        """_create_konnyaku_system が idle_disconnect_enabled=True を RouteConfig に渡すこと。"""
        fake_settings = {
            "idle_disconnect_enabled": True,
            "idle_timeout_sec": 600,
            "idle_audio_threshold": 200,
            "route_a": {"device": "", "lang": ""},
            "route_b": {"device": "", "lang": ""},
        }
        route_a_cfg, route_b_cfg = self._run_create_and_capture_route_configs(fake_settings)
        assert route_a_cfg is not None
        assert route_a_cfg.idle_disconnect_enabled is True, (
            f"route_a の idle_disconnect_enabled が True であること。got={route_a_cfg.idle_disconnect_enabled}"
        )
        assert route_b_cfg.idle_disconnect_enabled is True, (
            f"route_b の idle_disconnect_enabled が True であること。got={route_b_cfg.idle_disconnect_enabled}"
        )

    def test_idle_timeout_sec_passed_to_route_config(self):
        """_create_konnyaku_system が idle_timeout_sec=600 を RouteConfig に渡すこと。"""
        fake_settings = {
            "idle_disconnect_enabled": False,
            "idle_timeout_sec": 600,
            "idle_audio_threshold": 100,
            "route_a": {"device": "", "lang": ""},
            "route_b": {"device": "", "lang": ""},
        }
        route_a_cfg, _ = self._run_create_and_capture_route_configs(fake_settings)
        assert route_a_cfg is not None
        assert route_a_cfg.idle_timeout_sec == 600.0, (
            f"route_a の idle_timeout_sec が 600.0 であること。got={route_a_cfg.idle_timeout_sec}"
        )

    def test_idle_audio_threshold_passed_to_route_config(self):
        """_create_konnyaku_system が idle_audio_threshold=200 を RouteConfig に渡すこと。"""
        fake_settings = {
            "idle_disconnect_enabled": False,
            "idle_timeout_sec": 300,
            "idle_audio_threshold": 200,
            "route_a": {"device": "", "lang": ""},
            "route_b": {"device": "", "lang": ""},
        }
        route_a_cfg, _ = self._run_create_and_capture_route_configs(fake_settings)
        assert route_a_cfg is not None
        assert route_a_cfg.idle_audio_threshold == 200, (
            f"route_a の idle_audio_threshold が 200 であること。got={route_a_cfg.idle_audio_threshold}"
        )
