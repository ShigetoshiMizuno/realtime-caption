"""
tests/test_w_cost_2_ui.py

W-COST-2 UI ウィジェット配置テスト（issue #81）

テスト対象:
1. チェックボックスのタグ定数が app モジュールに存在すること
2. コールバック関数が app モジュールに存在すること
3. チェック切替で _save_settings が呼ばれること
4. 稼働中切替時に TAG_STATUS_STATE へ通知メッセージが設定されること
5. _build_gui が route_a / route_b の saved 値を default_value に反映すること
"""

import sys
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
# 1. TAG 定数の存在確認
# ---------------------------------------------------------------------------

class TestSourceTranscriptTagConstants:
    """TAG_ROUTE_A/B_SOURCE_TRANSCRIPT_ENABLE が app モジュールに定義されていること。"""

    def test_tag_route_a_source_transcript_enable_exists(self):
        """TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE"), (
            "TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE が app モジュールに定義されていること"
        )

    def test_tag_route_b_source_transcript_enable_exists(self):
        """TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE"), (
            "TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE が app モジュールに定義されていること"
        )

    def test_tag_route_a_value(self):
        """TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE の値が文字列であること。"""
        assert isinstance(app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE, str)
        assert len(app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE) > 0

    def test_tag_route_b_value(self):
        """TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE の値が文字列であること。"""
        assert isinstance(app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE, str)
        assert len(app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE) > 0

    def test_tags_are_distinct(self):
        """系統 A / B のタグが互いに異なること。"""
        assert (
            app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE
            != app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE
        )


# ---------------------------------------------------------------------------
# 2. コールバック関数の存在確認
# ---------------------------------------------------------------------------

class TestSourceTranscriptCallbacksExist:
    """コールバック関数が app モジュールに定義されていること。"""

    def test_on_route_a_source_transcript_change_exists(self):
        """_on_route_a_source_transcript_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_a_source_transcript_change"), (
            "_on_route_a_source_transcript_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_a_source_transcript_change)

    def test_on_route_b_source_transcript_change_exists(self):
        """_on_route_b_source_transcript_change が app モジュールに存在すること。"""
        assert hasattr(app, "_on_route_b_source_transcript_change"), (
            "_on_route_b_source_transcript_change が app モジュールに定義されていること"
        )
        assert callable(app._on_route_b_source_transcript_change)

    def test_callback_a_accepts_sender_app_data(self):
        """_on_route_a_source_transcript_change が (sender, app_data) シグネチャを持つこと。"""
        import inspect
        sig = inspect.signature(app._on_route_a_source_transcript_change)
        params = list(sig.parameters.keys())
        assert "sender" in params, f"sender パラメータが必要。got={params}"
        assert "app_data" in params, f"app_data パラメータが必要。got={params}"

    def test_callback_b_accepts_sender_app_data(self):
        """_on_route_b_source_transcript_change が (sender, app_data) シグネチャを持つこと。"""
        import inspect
        sig = inspect.signature(app._on_route_b_source_transcript_change)
        params = list(sig.parameters.keys())
        assert "sender" in params, f"sender パラメータが必要。got={params}"
        assert "app_data" in params, f"app_data パラメータが必要。got={params}"


# ---------------------------------------------------------------------------
# 3. チェック切替で _save_settings が呼ばれること
# ---------------------------------------------------------------------------

class TestSourceTranscriptCallbackSaveSettings:
    """チェックボックス変更コールバックが _save_settings() を呼ぶこと。"""

    def test_route_a_callback_calls_save_settings(self):
        """_on_route_a_source_transcript_change 呼び出しで _save_settings が呼ばれること。"""
        app._dpg_ready = False  # dpg 未初期化状態でもコールバック自体は呼ばれる

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_a_source_transcript_change(sender=None, app_data=True)
            mock_save.assert_called_once()

    def test_route_b_callback_calls_save_settings(self):
        """_on_route_b_source_transcript_change 呼び出しで _save_settings が呼ばれること。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_b_source_transcript_change(sender=None, app_data=True)
            mock_save.assert_called_once()

    def test_route_a_callback_calls_save_settings_when_unchecked(self):
        """app_data=False（チェック OFF）でも _save_settings が呼ばれること。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_a_source_transcript_change(sender=None, app_data=False)
            mock_save.assert_called_once()

    def test_route_b_callback_calls_save_settings_when_unchecked(self):
        """app_data=False（チェック OFF）でも _save_settings が呼ばれること。"""
        app._dpg_ready = False

        with patch.object(app, "_save_settings") as mock_save:
            app._on_route_b_source_transcript_change(sender=None, app_data=False)
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# 4. 稼働中切替時のステータス通知
# ---------------------------------------------------------------------------

class TestSourceTranscriptRunningNotification:
    """稼働中にチェックを変更したとき TAG_STATUS_STATE に通知メッセージが設定されること。

    W-COST-2 リファクタリング後: 稼働中切替は即時再起動（「切替中...」メッセージ）。
    「次回起動時に反映されます」は廃止。
    """

    def _make_mock_dpg(self, item_exists=True):
        mock_dpg = MagicMock()
        mock_dpg.does_item_exist.return_value = item_exists
        return mock_dpg

    def _make_running_system(self, route_a_state=None, route_b_state=None):
        """稼働中の FakeMultiCaptionSystem を作成するヘルパー。"""
        from main import RouteState
        from unittest.mock import MagicMock

        class _FakeRoute:
            def __init__(self, state):
                self._state = state
                self.set_output_device = MagicMock()

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

    def test_route_a_shows_status_message_when_running(self):
        """稼働中（_konnyaku_running=True, route_a=RUNNING）に系統 A チェックを変更すると
        TAG_STATUS_STATE に「切替中」メッセージが設定されること（W-COST-2 即時再起動）。"""
        from main import RouteState
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system(route_a_state=RouteState.RUNNING)
        mock_dpg = self._make_mock_dpg()

        with patch("app.dpg", mock_dpg):
            app._on_route_a_source_transcript_change(sender=None, app_data=False)

        # TAG_STATUS_STATE に set_value が呼ばれたことを確認
        set_value_calls = [
            c for c in mock_dpg.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_STATUS_STATE
        ]
        assert len(set_value_calls) >= 1, (
            "稼働中切替時に TAG_STATUS_STATE へ set_value が呼ばれること。"
            f"set_value calls={mock_dpg.set_value.call_args_list}"
        )
        message = set_value_calls[0].args[1]
        assert "切替中" in message, (
            f"メッセージに「切替中」が含まれること。got={message!r}"
        )

    def test_route_b_shows_status_message_when_running(self):
        """稼働中（_konnyaku_running=True, route_b=RUNNING）に系統 B チェックを変更すると
        TAG_STATUS_STATE に「切替中」メッセージが設定されること（W-COST-2 即時再起動）。"""
        from main import RouteState
        app._konnyaku_running = True
        app._konnyaku_system = self._make_running_system(route_b_state=RouteState.RUNNING)
        mock_dpg = self._make_mock_dpg()

        with patch("app.dpg", mock_dpg):
            app._on_route_b_source_transcript_change(sender=None, app_data=True)

        set_value_calls = [
            c for c in mock_dpg.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_STATUS_STATE
        ]
        assert len(set_value_calls) >= 1, (
            "稼働中切替時に TAG_STATUS_STATE へ set_value が呼ばれること。"
            f"set_value calls={mock_dpg.set_value.call_args_list}"
        )
        message = set_value_calls[0].args[1]
        assert "切替中" in message, (
            f"メッセージに「切替中」が含まれること。got={message!r}"
        )

    def test_route_a_no_status_message_when_not_running(self):
        """停止中（_konnyaku_running=False）にチェックを変更しても
        TAG_STATUS_STATE への set_value は呼ばれないこと（または空文字設定）。"""
        app._konnyaku_running = False
        mock_dpg = self._make_mock_dpg()

        with patch("app.dpg", mock_dpg):
            app._on_route_a_source_transcript_change(sender=None, app_data=False)

        # 稼働中でないときに「次回起動時」メッセージを出していないこと
        for c in mock_dpg.set_value.call_args_list:
            if c.args and c.args[0] == app.TAG_STATUS_STATE:
                msg = c.args[1] if len(c.args) > 1 else ""
                assert "次回起動時" not in msg, (
                    f"停止中は「次回起動時」メッセージを出さないこと。got={msg!r}"
                )

    def test_route_b_no_status_message_when_not_running(self):
        """停止中（_konnyaku_running=False）にチェックを変更しても
        TAG_STATUS_STATE に「次回起動時」メッセージは出ないこと。"""
        app._konnyaku_running = False
        mock_dpg = self._make_mock_dpg()

        with patch("app.dpg", mock_dpg):
            app._on_route_b_source_transcript_change(sender=None, app_data=False)

        for c in mock_dpg.set_value.call_args_list:
            if c.args and c.args[0] == app.TAG_STATUS_STATE:
                msg = c.args[1] if len(c.args) > 1 else ""
                assert "次回起動時" not in msg, (
                    f"停止中は「次回起動時」メッセージを出さないこと。got={msg!r}"
                )


# ---------------------------------------------------------------------------
# 5. _build_gui が saved 設定値を default_value に反映すること
# ---------------------------------------------------------------------------

class TestSourceTranscriptGuiDefaultValue:
    """_build_gui が route_a / route_b の saved source_transcript_enabled を
    チェックボックスの default_value に反映すること。"""

    def _run_build_gui_fragment(self, route_a_saved_value: bool, route_b_saved_value: bool):
        """_build_gui 内のチェックボックス追加を捕捉するためのヘルパー。

        add_checkbox の calls を返す。
        """
        added_checkboxes: dict[str, dict] = {}

        def fake_add_checkbox(tag=None, label="", default_value=True, **kwargs):
            added_checkboxes[tag] = {"default_value": default_value, "label": label}

        fake_settings = {
            "route_a": {
                "source_transcript_enabled": route_a_saved_value,
                "enabled": True,
                "output_enabled": False,
            },
            "route_b": {
                "source_transcript_enabled": route_b_saved_value,
                "enabled": True,
                "output_enabled": True,
            },
        }

        mock_dpg = MagicMock()
        mock_dpg.add_checkbox.side_effect = fake_add_checkbox
        # group / child_window / text 等は no-op
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
                # dpg の一部呼び出しがモックで失敗しても捕捉済みの add_checkbox データを使う
                pass

        return added_checkboxes

    def test_route_a_default_value_true_when_saved_true(self):
        """saved source_transcript_enabled=True のとき
        route_a チェックボックスの default_value が True であること。"""
        checkboxes = self._run_build_gui_fragment(
            route_a_saved_value=True,
            route_b_saved_value=False,
        )
        tag = app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE
        assert tag in checkboxes, (
            f"TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )
        assert checkboxes[tag]["default_value"] is True, (
            f"saved=True のとき default_value が True であること。"
            f"got={checkboxes[tag]}"
        )

    def test_route_a_default_value_false_when_saved_false(self):
        """saved source_transcript_enabled=False のとき
        route_a チェックボックスの default_value が False であること。"""
        checkboxes = self._run_build_gui_fragment(
            route_a_saved_value=False,
            route_b_saved_value=True,
        )
        tag = app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE
        assert tag in checkboxes, (
            f"TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )
        assert checkboxes[tag]["default_value"] is False, (
            f"saved=False のとき default_value が False であること。"
            f"got={checkboxes[tag]}"
        )

    def test_route_b_default_value_true_when_saved_true(self):
        """saved source_transcript_enabled=True のとき
        route_b チェックボックスの default_value が True であること。"""
        checkboxes = self._run_build_gui_fragment(
            route_a_saved_value=False,
            route_b_saved_value=True,
        )
        tag = app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE
        assert tag in checkboxes, (
            f"TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )
        assert checkboxes[tag]["default_value"] is True, (
            f"saved=True のとき default_value が True であること。"
            f"got={checkboxes[tag]}"
        )

    def test_route_b_default_value_false_when_saved_false(self):
        """saved source_transcript_enabled=False のとき
        route_b チェックボックスの default_value が False であること。"""
        checkboxes = self._run_build_gui_fragment(
            route_a_saved_value=True,
            route_b_saved_value=False,
        )
        tag = app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE
        assert tag in checkboxes, (
            f"TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(checkboxes.keys())}"
        )
        assert checkboxes[tag]["default_value"] is False, (
            f"saved=False のとき default_value が False であること。"
            f"got={checkboxes[tag]}"
        )

    def test_route_a_default_value_true_when_no_saved(self):
        """saved に source_transcript_enabled がない場合、
        route_a チェックボックスのデフォルトが True（後方互換）であること。"""
        added_checkboxes: dict[str, dict] = {}

        def fake_add_checkbox(tag=None, label="", default_value=True, **kwargs):
            added_checkboxes[tag] = {"default_value": default_value}

        fake_settings = {
            "route_a": {"enabled": True, "output_enabled": False},
            "route_b": {"enabled": True, "output_enabled": True},
        }

        mock_dpg = MagicMock()
        mock_dpg.add_checkbox.side_effect = fake_add_checkbox
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

        tag = app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE
        assert tag in added_checkboxes, (
            f"saved なしでも TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE でチェックボックスが追加されること。"
            f"found tags={list(added_checkboxes.keys())}"
        )
        assert added_checkboxes[tag]["default_value"] is True, (
            f"saved なしのデフォルトは True（後方互換）であること。"
            f"got={added_checkboxes[tag]}"
        )

    def test_checkbox_label_contains_cost_hint(self):
        """チェックボックスのラベルに Whisper コスト示唆テキストが含まれること。"""
        added_checkboxes: dict[str, dict] = {}

        def fake_add_checkbox(tag=None, label="", default_value=True, **kwargs):
            added_checkboxes[tag] = {"default_value": default_value, "label": label}

        fake_settings = {
            "route_a": {"enabled": True},
            "route_b": {"enabled": True},
        }

        mock_dpg = MagicMock()
        mock_dpg.add_checkbox.side_effect = fake_add_checkbox
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

        tag_a = app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE
        if tag_a in added_checkboxes:
            label_a = added_checkboxes[tag_a].get("label", "")
            # Whisper コスト示唆が含まれること（「課金」「Whisper」「コスト」いずれか）
            assert any(kw in label_a for kw in ("課金", "Whisper", "コスト")), (
                f"TAG_ROUTE_A チェックボックスのラベルに Whisper コスト示唆が含まれること。"
                f"got label={label_a!r}"
            )
