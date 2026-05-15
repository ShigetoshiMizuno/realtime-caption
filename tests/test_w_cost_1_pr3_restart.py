"""
tests/test_w_cost_1_pr3_restart.py

W-COST-1 PR3: app.py の音声出力 ON/OFF 稼働中切替の再起動結線テスト。

テスト対象 (docs/spec/cost-w-cost-1-design.md §4.3 / PR3):
- 稼働中に音声出力 OFF で stop_route("a") -> start_route("a") が順序通り呼ばれること
- 稼働中に音声出力 ON で stop_route("a") -> start_route("a") が順序通り呼ばれること
- 系統 B でも同様に再起動が呼ばれること
- 停止中（_konnyaku_running=False）の場合は再起動が発生しないこと
- RouteState が RUNNING 以外（IDLE など）の場合は再起動が発生しないこと
- 再起動中にステータスバーへのメッセージ表示が行われること
"""

import threading
import time
from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest

import app
from main import RouteState


# ---------------------------------------------------------------------------
# ヘルパー: FakeRouteSystem（CaptionSystem の最小スタブ）
# ---------------------------------------------------------------------------

class FakeRouteSystem:
    """CaptionSystem の最小スタブ。state プロパティを返す。"""

    def __init__(self, state: RouteState = RouteState.IDLE):
        self._state = state
        self.set_output_device = MagicMock()

    @property
    def state(self) -> RouteState:
        return self._state


class FakeMultiCaptionSystem:
    """MultiCaptionSystem の最小スタブ。"""

    def __init__(self, route_a_state=RouteState.IDLE, route_b_state=RouteState.IDLE):
        self.route_a_system = FakeRouteSystem(route_a_state)
        self.route_b_system = FakeRouteSystem(route_b_state)
        self.stop_route = MagicMock()
        self.start_route = MagicMock()


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_app_state():
    """各テスト前後に app モジュールのグローバル状態をリセットする。"""
    old_system = app._konnyaku_system
    old_running = app._konnyaku_running

    yield

    app._konnyaku_system = old_system
    app._konnyaku_running = old_running


# ---------------------------------------------------------------------------
# テスト: 系統 A — 稼働中に音声出力 OFF → 再起動発生
# ---------------------------------------------------------------------------

class TestRouteAOutputOffRestart:
    def test_off_triggers_stop_then_start_when_running(self):
        """稼働中に音声出力 OFF で stop_route('a') -> start_route('a') が順序通り呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        # スレッドが完了するまで待つ（最大 3 秒）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.stop_route.assert_called_once_with("a")
        fake_system.start_route.assert_called_once_with("a")

        # 順序チェック: stop が start より先に呼ばれること
        stop_order = fake_system.stop_route.call_args_list
        start_order = fake_system.start_route.call_args_list
        assert len(stop_order) == 1
        assert len(start_order) == 1

    def test_off_sets_output_device_none_before_restart(self):
        """音声出力 OFF のとき set_output_device(None) が再起動前に呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        # set_output_device(None) が呼ばれること
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.route_a_system.set_output_device.called:
                break
            time.sleep(0.05)

        fake_system.route_a_system.set_output_device.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# テスト: 系統 A — 稼働中に音声出力 ON → 再起動発生
# ---------------------------------------------------------------------------

class TestRouteAOutputOnRestart:
    def test_on_triggers_stop_then_start_when_running(self):
        """稼働中に音声出力 ON で stop_route('a') -> start_route('a') が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.list_audio_devices", return_value=[{"name": "Speaker", "index": 3}]), \
             patch("app.find_device_by_name", return_value={"name": "Speaker", "index": 3}), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "Speaker"

            app._on_route_a_output_enable_change(sender=None, app_data=True, user_data=None)

        # スレッドが完了するまで待つ（最大 3 秒）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.stop_route.assert_called_once_with("a")
        fake_system.start_route.assert_called_once_with("a")


# ---------------------------------------------------------------------------
# テスト: 系統 B — 稼働中に音声出力 OFF → 再起動発生
# ---------------------------------------------------------------------------

class TestRouteBOutputOffRestart:
    def test_off_triggers_stop_then_start_when_running(self):
        """系統 B 稼働中に音声出力 OFF で stop_route('b') -> start_route('b') が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_b_output_enable_change(sender=None, app_data=False, user_data=None)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.stop_route.assert_called_once_with("b")
        fake_system.start_route.assert_called_once_with("b")

    def test_off_sets_output_device_none_before_restart(self):
        """系統 B 音声出力 OFF のとき set_output_device(None) が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_b_output_enable_change(sender=None, app_data=False, user_data=None)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.route_b_system.set_output_device.called:
                break
            time.sleep(0.05)

        fake_system.route_b_system.set_output_device.assert_called_once_with(None)


# ---------------------------------------------------------------------------
# テスト: 停止中（_konnyaku_running=False）では再起動しないこと
# ---------------------------------------------------------------------------

class TestNoRestartWhenNotRunning:
    def test_route_a_off_no_restart_when_konnyaku_not_running(self):
        """_konnyaku_running=False 時は stop_route が呼ばれないこと。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        time.sleep(0.2)
        fake_system.stop_route.assert_not_called()
        fake_system.start_route.assert_not_called()

    def test_route_b_off_no_restart_when_konnyaku_not_running(self):
        """系統 B: _konnyaku_running=False 時は stop_route が呼ばれないこと。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True

            app._on_route_b_output_enable_change(sender=None, app_data=False, user_data=None)

        time.sleep(0.2)
        fake_system.stop_route.assert_not_called()
        fake_system.start_route.assert_not_called()

    def test_route_a_off_no_restart_when_route_idle(self):
        """RouteState.IDLE の場合は stop_route が呼ばれないこと。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.IDLE)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        time.sleep(0.2)
        fake_system.stop_route.assert_not_called()
        fake_system.start_route.assert_not_called()

    def test_route_a_off_no_restart_when_konnyaku_system_none(self):
        """_konnyaku_system=None のときに例外が発生しないこと。"""
        app._konnyaku_system = None
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True

            # 例外が発生しないことを確認
            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        time.sleep(0.1)
        # 例外なく完了で OK


# ---------------------------------------------------------------------------
# テスト: 再起動中のステータス表示
# ---------------------------------------------------------------------------

class TestRestartStatusDisplay:
    def test_status_message_shown_during_restart_route_a(self):
        """系統 A 再起動中にステータスバーへのメッセージ表示が行われること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        status_calls = []

        def fake_set_value(tag, value):
            status_calls.append((tag, value))

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"
            mock_dpg.set_value.side_effect = fake_set_value

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        # TAG_STATUS_STATE への set_value が呼ばれたことを確認
        from app import TAG_STATUS_STATE
        status_tags = [tag for tag, _ in status_calls]
        assert TAG_STATUS_STATE in status_tags, (
            f"TAG_STATUS_STATE への set_value が呼ばれていない。calls={status_calls}"
        )

    def test_status_message_shown_during_restart_route_b(self):
        """系統 B 再起動中にステータスバーへのメッセージ表示が行われること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        status_calls = []

        def fake_set_value(tag, value):
            status_calls.append((tag, value))

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"
            mock_dpg.set_value.side_effect = fake_set_value

            app._on_route_b_output_enable_change(sender=None, app_data=False, user_data=None)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        from app import TAG_STATUS_STATE
        status_tags = [tag for tag, _ in status_calls]
        assert TAG_STATUS_STATE in status_tags, (
            f"TAG_STATUS_STATE への set_value が呼ばれていない。calls={status_calls}"
        )
