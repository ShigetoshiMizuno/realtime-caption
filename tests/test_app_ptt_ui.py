"""
tests/test_app_ptt_ui.py

app.py の PTT UI 結線テスト (PR4)。

テスト対象:
- PTT チェックボックス ON で manager.start() が呼ばれること (F-5)
- PTT チェックボックス OFF で _cleanup_ptt_manager() が呼ばれること (F-5)
- ホットキー入力変更で change_hotkey() が呼ばれること (F-5)
- 設定変更が _save_settings() を呼んで永続化されること (F-5)
- TBD-4: 系統B チェック OFF 時に PTT モードも自動 OFF
- TBD-3: PTT 押下中のデバイスコンボ disable 状態遷移
- W-3: _build_ptt_settings_dict が _save_settings() から呼ばれること
- F-6: 視覚フィードバック関数が存在し、正しいシグネチャを持つこと

docs/spec/ptt-mode-design.md F-5 / F-6 / TBD-3 / TBD-4 参照
"""

import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

import app


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

class FakeKeyboardBackend:
    """keyboard モジュールの代替。"""

    def __init__(self):
        self._press_handlers: dict[str, list] = {}
        self._release_handlers: dict[str, list] = {}
        self.unhook_all_called = 0

    def on_press_key(self, key: str, callback, suppress: bool = False):
        self._press_handlers.setdefault(key, []).append(callback)
        return (key, "press", callback)

    def on_release_key(self, key: str, callback, suppress: bool = False):
        self._release_handlers.setdefault(key, []).append(callback)
        return (key, "release", callback)

    def unhook_all(self):
        self.unhook_all_called += 1
        self._press_handlers.clear()
        self._release_handlers.clear()

    def simulate_press(self, key: str):
        event = MagicMock()
        event.name = key
        for cb in self._press_handlers.get(key, []):
            cb(event)

    def simulate_release(self, key: str):
        event = MagicMock()
        event.name = key
        for cb in self._release_handlers.get(key, []):
            cb(event)


class FakeTimer:
    """threading.Timer 互換の同期タイマー。start() で即座にコールバックを呼ぶ。"""

    def __init__(self, interval, func):
        self._func = func
        self._cancelled = False

    def start(self):
        if not self._cancelled:
            self._func()

    def cancel(self):
        self._cancelled = True


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_app_ptt_state():
    """各テスト前後に app モジュールの PTT グローバル状態をリセットする。"""
    old_manager = app._ptt_manager
    if old_manager is not None:
        try:
            old_manager.stop()
        except Exception:
            pass

    old_ptt_enabled = app._ptt_enabled
    old_ptt_hotkey = app._ptt_hotkey
    old_running = app._konnyaku_running
    old_system = app._konnyaku_system
    old_dpg_ready = app._dpg_ready

    yield

    new_manager = app._ptt_manager
    if new_manager is not None and new_manager is not old_manager:
        try:
            new_manager.stop()
        except Exception:
            pass

    app._ptt_manager = old_manager
    app._ptt_enabled = old_ptt_enabled
    app._ptt_hotkey = old_ptt_hotkey
    app._konnyaku_running = old_running
    app._konnyaku_system = old_system
    app._dpg_ready = old_dpg_ready


# ---------------------------------------------------------------------------
# F-5: PTT チェックボックス ON → manager.start() が呼ばれること
# ---------------------------------------------------------------------------

class TestPttEnabledChange:
    def test_ptt_enabled_true_calls_manager_start(self):
        """PTT チェックボックス ON 時に _ptt_manager.start() が呼ばれること。"""
        mock_manager = MagicMock()
        mock_manager.running = False
        app._ptt_manager = mock_manager
        app._ptt_enabled = False

        app._on_ptt_enabled_change(enabled=True)

        mock_manager.start.assert_called_once()

    def test_ptt_enabled_true_updates_global_flag(self):
        """PTT チェックボックス ON で _ptt_enabled が True になること。"""
        mock_manager = MagicMock()
        mock_manager.running = False
        app._ptt_manager = mock_manager
        app._ptt_enabled = False

        app._on_ptt_enabled_change(enabled=True)

        assert app._ptt_enabled is True

    def test_ptt_enabled_false_calls_cleanup(self):
        """PTT チェックボックス OFF 時に _cleanup_ptt_manager() が呼ばれること。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_enabled = True

        with patch.object(app, '_cleanup_ptt_manager') as mock_cleanup:
            app._on_ptt_enabled_change(enabled=False)
            mock_cleanup.assert_called_once()

    def test_ptt_enabled_false_updates_global_flag(self):
        """PTT チェックボックス OFF で _ptt_enabled が False になること。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_enabled = True

        app._on_ptt_enabled_change(enabled=False)

        assert app._ptt_enabled is False

    def test_ptt_enabled_calls_save_settings(self):
        """PTT チェックボックス変更時に _save_settings() が呼ばれること。"""
        mock_manager = MagicMock()
        mock_manager.running = False
        app._ptt_manager = mock_manager
        app._dpg_ready = False  # dpg 未初期化なので _save_settings 内は no-op だが呼ばれること確認

        with patch.object(app, '_save_settings') as mock_save:
            app._on_ptt_enabled_change(enabled=True)
            mock_save.assert_called_once()

    def test_ptt_enabled_noop_when_manager_is_none_and_enable_false(self):
        """_ptt_manager が None で OFF に変更しても例外が起きないこと。"""
        app._ptt_manager = None
        app._ptt_enabled = False

        # 例外なく完了すればOK
        app._on_ptt_enabled_change(enabled=False)

    def test_ptt_enabled_true_already_running_noop(self):
        """manager.running=True の状態で ON にしても start() を重ねて呼ばないこと（冪等性）。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_enabled = True

        app._on_ptt_enabled_change(enabled=True)

        mock_manager.start.assert_not_called()


# ---------------------------------------------------------------------------
# F-5: ホットキー入力変更 → change_hotkey() が呼ばれること
# ---------------------------------------------------------------------------

class TestPttHotkeyChange:
    def test_hotkey_change_calls_change_hotkey(self):
        """ホットキー入力変更時に _ptt_manager.change_hotkey() が呼ばれること。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_enabled = True

        app._on_ptt_hotkey_change(new_hotkey="f9")

        mock_manager.change_hotkey.assert_called_once_with("f9")

    def test_hotkey_change_updates_global(self):
        """ホットキー変更で _ptt_hotkey グローバルが更新されること。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_hotkey = "f8"

        app._on_ptt_hotkey_change(new_hotkey="f10")

        assert app._ptt_hotkey == "f10"

    def test_hotkey_change_calls_save_settings(self):
        """ホットキー変更時に _save_settings() が呼ばれること。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager

        with patch.object(app, '_save_settings') as mock_save:
            app._on_ptt_hotkey_change(new_hotkey="f9")
            mock_save.assert_called_once()

    def test_hotkey_change_noop_when_manager_is_none(self):
        """_ptt_manager が None でも例外が起きないこと。"""
        app._ptt_manager = None
        app._ptt_hotkey = "f8"

        # 例外なく完了すればOK
        app._on_ptt_hotkey_change(new_hotkey="f9")
        assert app._ptt_hotkey == "f9"

    def test_hotkey_change_noop_manager_not_running(self):
        """manager.running=False のとき change_hotkey() を呼ばないこと。"""
        mock_manager = MagicMock()
        mock_manager.running = False
        app._ptt_manager = mock_manager

        app._on_ptt_hotkey_change(new_hotkey="f9")

        mock_manager.change_hotkey.assert_not_called()
        assert app._ptt_hotkey == "f9"


# ---------------------------------------------------------------------------
# TBD-4: 系統B チェック OFF 時に PTT モードも自動 OFF
# ---------------------------------------------------------------------------

class TestRouteBEnablePttIntegration:
    def test_route_b_off_when_ptt_enabled_turns_ptt_off(self):
        """PTT ON 時に系統B チェックを OFF にすると PTT モードが OFF になること（TBD-4）。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_enabled = True

        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        with patch.object(app, '_cleanup_ptt_manager') as mock_cleanup:
            app._on_route_b_enable_change_ptt_aware(enabled=False)
            mock_cleanup.assert_called_once()

    def test_route_b_off_updates_ptt_enabled_flag(self):
        """系統B OFF で _ptt_enabled が False になること（TBD-4）。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_enabled = True

        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        app._on_route_b_enable_change_ptt_aware(enabled=False)

        assert app._ptt_enabled is False

    def test_route_b_on_when_ptt_disabled_normal_start(self):
        """PTT OFF 時に系統B チェックを ON にすると通常の start_route('b') が呼ばれること（TBD-4）。"""
        app._ptt_enabled = False
        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        app._on_route_b_enable_change_ptt_aware(enabled=True)

        mock_system.start_route.assert_called_once_with("b")

    def test_route_b_off_when_ptt_disabled_normal_stop(self):
        """PTT OFF 時に系統B チェックを OFF にすると通常の stop_route('b') が呼ばれること（TBD-4）。"""
        app._ptt_enabled = False
        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        app._on_route_b_enable_change_ptt_aware(enabled=False)

        mock_system.stop_route.assert_called_once_with("b")

    def test_route_b_off_ptt_on_saves_settings(self):
        """系統B OFF (PTT ON 状態) でも _save_settings() が呼ばれること。"""
        mock_manager = MagicMock()
        mock_manager.running = True
        app._ptt_manager = mock_manager
        app._ptt_enabled = True
        app._konnyaku_system = MagicMock()

        with patch.object(app, '_save_settings') as mock_save:
            app._on_route_b_enable_change_ptt_aware(enabled=False)
            mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# TBD-3: PTT 押下中のデバイスコンボ disable 状態遷移
# ---------------------------------------------------------------------------

class TestRouteBDeviceDisableDuringPtt:
    def test_route_b_device_disabled_during_ptt_press(self):
        """PTT 押下中 (STARTING/RUNNING) に入力デバイスコンボが disabled になること（TBD-3）。"""
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.RUNNING

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b
        app._konnyaku_system = mock_system
        app._ptt_enabled = True

        # PTT 押下中かどうかの判定
        result = app._is_ptt_pressing()

        assert result is True

    def test_route_b_device_enabled_when_ptt_idle(self):
        """PTT 待機中 (IDLE) は入力デバイスコンボが enabled であること（TBD-3）。"""
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.IDLE

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b
        app._konnyaku_system = mock_system
        app._ptt_enabled = True

        result = app._is_ptt_pressing()

        assert result is False

    def test_is_ptt_pressing_false_when_ptt_disabled(self):
        """PTT モード OFF のときは RUNNING でも _is_ptt_pressing() が False を返すこと。"""
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.RUNNING

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b
        app._konnyaku_system = mock_system
        app._ptt_enabled = False

        result = app._is_ptt_pressing()

        assert result is False

    def test_is_ptt_pressing_false_when_system_is_none(self):
        """_konnyaku_system が None のとき False を返すこと。"""
        app._konnyaku_system = None
        app._ptt_enabled = True

        result = app._is_ptt_pressing()

        assert result is False

    def test_is_ptt_pressing_starting_state(self):
        """RouteState.STARTING のときも _is_ptt_pressing() が True を返すこと（TBD-3）。"""
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.STARTING

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b
        app._konnyaku_system = mock_system
        app._ptt_enabled = True

        result = app._is_ptt_pressing()

        assert result is True


# ---------------------------------------------------------------------------
# W-3: _build_ptt_settings_dict が _save_settings() から呼ばれること
# ---------------------------------------------------------------------------

class TestSaveSettingsPttIntegration:
    def test_save_settings_uses_ptt_globals(self):
        """_save_settings() が _ptt_enabled / _ptt_hotkey を参照して保存すること（W-3）。"""
        # _save_settings の実際の実行は dpg_ready=False で skip されるため、
        # 設定辞書構築ロジック（_build_ptt_settings_dict）が正しい値を持つかを確認
        app._ptt_enabled = True
        app._ptt_hotkey = "f9"

        result = app._build_ptt_settings_dict(
            existing_data={},
            ptt_enabled=app._ptt_enabled,
            ptt_hotkey=app._ptt_hotkey,
        )

        assert result["route_b"]["ptt_enabled"] is True
        assert result["route_b"]["ptt_hotkey"] == "f9"

    def test_save_settings_ptt_false_persisted(self):
        """ptt_enabled=False の場合も route_b.ptt_enabled=False として保存されること。"""
        app._ptt_enabled = False
        app._ptt_hotkey = "f8"

        result = app._build_ptt_settings_dict(
            existing_data={"route_b": {"enabled": True}},
            ptt_enabled=app._ptt_enabled,
            ptt_hotkey=app._ptt_hotkey,
        )

        assert result["route_b"]["ptt_enabled"] is False
        assert result["route_b"]["enabled"] is True  # 既存キーが保持されること


# ---------------------------------------------------------------------------
# F-6: 視覚フィードバック関数が存在し呼び出し可能であること
# ---------------------------------------------------------------------------

class TestPttVisualFeedback:
    def test_update_ptt_visual_feedback_function_exists(self):
        """_update_ptt_visual_feedback() 関数が app モジュールに存在すること（F-6）。"""
        assert hasattr(app, '_update_ptt_visual_feedback'), \
            "_update_ptt_visual_feedback が app に定義されていない"
        assert callable(app._update_ptt_visual_feedback)

    def test_update_ptt_visual_feedback_noop_when_dpg_not_ready(self):
        """dpg_ready=False のとき _update_ptt_visual_feedback() が例外を起こさないこと。"""
        app._dpg_ready = False
        # 例外なく完了すればOK
        app._update_ptt_visual_feedback()

    def test_update_ptt_visual_feedback_noop_when_system_none(self):
        """_konnyaku_system が None のとき _update_ptt_visual_feedback() が例外を起こさないこと。"""
        app._dpg_ready = False
        app._konnyaku_system = None
        # 例外なく完了すればOK
        app._update_ptt_visual_feedback()

    def test_update_ptt_visual_feedback_pressing_true_path(self):
        """PTT 押下中のとき _update_ptt_visual_feedback() が「押下中」パスを通ること。

        dpg 未初期化なので実際の dpg 呼び出しはスキップされるが、
        内部ロジックが例外を起こさないことを確認する。
        """
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.RUNNING

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b
        app._konnyaku_system = mock_system
        app._ptt_enabled = True
        app._dpg_ready = False

        # 例外なく完了すればOK
        app._update_ptt_visual_feedback()


# ---------------------------------------------------------------------------
# F-5: TAG_PTT_ENABLED / TAG_PTT_HOTKEY タグが定義されていること
# ---------------------------------------------------------------------------

class TestPttGuiTags:
    def test_tag_ptt_enabled_defined(self):
        """TAG_PTT_ENABLED タグが app モジュールに定義されていること。"""
        assert hasattr(app, 'TAG_PTT_ENABLED'), "TAG_PTT_ENABLED が app に定義されていない"

    def test_tag_ptt_hotkey_defined(self):
        """TAG_PTT_HOTKEY タグが app モジュールに定義されていること。"""
        assert hasattr(app, 'TAG_PTT_HOTKEY'), "TAG_PTT_HOTKEY が app に定義されていない"

    def test_tag_ptt_section_defined(self):
        """TAG_PTT_SECTION タグが app モジュールに定義されていること（PTT 設定グループ）。"""
        assert hasattr(app, 'TAG_PTT_SECTION'), "TAG_PTT_SECTION が app に定義されていない"


# ---------------------------------------------------------------------------
# F-6: TAG_ROUTE_B_LABEL タグが定義されていること（系統2 見出しラベル動的切替用）
# ---------------------------------------------------------------------------

class TestRouteBLabelTag:
    def test_tag_route_b_label_defined(self):
        """TAG_ROUTE_B_LABEL タグが app モジュールに定義されていること（F-6 ラベル切替用）。"""
        assert hasattr(app, 'TAG_ROUTE_B_LABEL'), "TAG_ROUTE_B_LABEL が app に定義されていない"

    def test_tag_ptt_status_label_defined(self):
        """TAG_PTT_STATUS_LABEL タグが app モジュールに定義されていること（F-6 押下中表示用）。"""
        assert hasattr(app, 'TAG_PTT_STATUS_LABEL'), "TAG_PTT_STATUS_LABEL が app に定義されていない"


# ---------------------------------------------------------------------------
# C-1: _on_ptt_press/_on_ptt_release が _gui_queue 経由で視覚更新すること
# ---------------------------------------------------------------------------

class TestPttPressUsesGuiQueue:
    def test_ptt_press_uses_gui_queue(self):
        """_on_ptt_press が直接 _update_ptt_visual_feedback() を呼ばず
        _gui_queue に 'update_ptt_visual' コマンドを enqueue すること（C-1）。"""
        import queue as _queue

        mock_system = MagicMock()
        app._konnyaku_system = mock_system
        app._konnyaku_running = True

        # _gui_queue を空にしてから呼び出す
        while not app._gui_queue.empty():
            app._gui_queue.get_nowait()

        event = MagicMock()
        with patch.object(app, '_update_ptt_visual_feedback') as mock_visual:
            app._on_ptt_press(event)

            # _update_ptt_visual_feedback が直接呼ばれていないこと
            mock_visual.assert_not_called()

        # _gui_queue に 'update_ptt_visual' が入っていること
        items = []
        while not app._gui_queue.empty():
            items.append(app._gui_queue.get_nowait())
        cmds = [i.get("cmd") for i in items]
        assert "update_ptt_visual" in cmds, \
            f"_gui_queue に 'update_ptt_visual' が enqueue されていない。実際: {cmds}"

    def test_ptt_release_uses_gui_queue(self):
        """_on_ptt_release が直接 _update_ptt_visual_feedback() を呼ばず
        _gui_queue に 'update_ptt_visual' コマンドを enqueue すること（C-1）。"""
        import queue as _queue

        mock_system = MagicMock()
        app._konnyaku_system = mock_system
        app._konnyaku_running = True

        # _gui_queue を空にしてから呼び出す
        while not app._gui_queue.empty():
            app._gui_queue.get_nowait()

        event = MagicMock()
        with patch.object(app, '_update_ptt_visual_feedback') as mock_visual:
            app._on_ptt_release(event)

            # _update_ptt_visual_feedback が直接呼ばれていないこと
            mock_visual.assert_not_called()

        # _gui_queue に 'update_ptt_visual' が入っていること
        items = []
        while not app._gui_queue.empty():
            items.append(app._gui_queue.get_nowait())
        cmds = [i.get("cmd") for i in items]
        assert "update_ptt_visual" in cmds, \
            f"_gui_queue に 'update_ptt_visual' が enqueue されていない。実際: {cmds}"

    def test_gui_queue_consumes_update_ptt_visual(self):
        """_drain_queue() が 'update_ptt_visual' コマンドを消費して
        _update_ptt_visual_feedback() をメインスレッドから呼ぶこと（C-1）。"""
        app._gui_queue.put({"cmd": "update_ptt_visual"})

        with patch.object(app, '_update_ptt_visual_feedback') as mock_visual:
            app._drain_queue()
            mock_visual.assert_called_once()


# ---------------------------------------------------------------------------
# W-1: 待機中ラベルのホットキー文字列が動的に反映されること
# ---------------------------------------------------------------------------

class TestIdleLabelUsesCurrentHotkey:
    def test_idle_label_uses_current_hotkey(self):
        """ホットキーを変更すると待機中ラベルが _ptt_hotkey を反映すること（W-1）。"""
        import dearpygui.dearpygui as dpg_real

        app._ptt_enabled = True
        app._dpg_ready = False  # dpg 未初期化環境でのテスト

        # _ptt_hotkey を f8 以外に変更して _update_ptt_visual_feedback を呼ぶ
        # dpg_ready=False なので実際の dpg 操作は行われないが、
        # ラベル文字列生成ロジックをパッチして確認する

        # dpg.does_item_exist が True を返すよう、かつ dpg.set_value / configure_item を
        # モックして呼び出し引数を検証する
        app._ptt_hotkey = "f9"
        app._dpg_ready = True

        with patch.object(dpg_real, 'does_item_exist', return_value=True), \
             patch.object(dpg_real, 'set_value') as mock_set_value, \
             patch.object(dpg_real, 'configure_item'), \
             patch.object(app, '_is_ptt_pressing', return_value=False):
            app._update_ptt_visual_feedback()

        # TAG_PTT_STATUS_LABEL への set_value 呼び出しを検索
        calls_for_status = [
            c for c in mock_set_value.call_args_list
            if c.args and c.args[0] == app.TAG_PTT_STATUS_LABEL
        ]
        assert calls_for_status, "TAG_PTT_STATUS_LABEL への set_value が呼ばれていない"

        label_value = calls_for_status[0].args[1]
        assert "F9" in label_value or "f9" in label_value, \
            f"待機中ラベルに _ptt_hotkey ('f9') が含まれていない。実際: {label_value!r}"
        assert "F8" not in label_value, \
            f"待機中ラベルに古いホットキー 'F8' がハードコードされている。実際: {label_value!r}"
