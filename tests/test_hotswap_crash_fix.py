"""
tests/test_hotswap_crash_fix.py

ホットスワップクラッシュバグ修正の検証テスト。

Bug 1: RUNNING中に set_output_device() を直接呼ぶと _restart_route_for_change との
       二重操作でレースコンディションが発生しクラッシュする。

Bug 2: _on_route_a/b_output_device_change で RUNNING 中に _restart_route_for_change を
       呼ぶ前に _output_device_index が更新されないため、再起動後も古いデバイスが使われる。

修正内容:
- CaptionSystem に update_output_config(device_index) を追加（ストリーム再起動なし）
- RUNNING中は set_output_device の代わりに update_output_config を使う
- _on_route_a/b_output_device_change: RUNNING中は restart 前に update_output_config を呼ぶ
- _on_route_a/b_output_enable_change: RUNNING中は set_output_device の代わりに
  update_output_config を使い、その後 restart に任せる
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# CaptionSystem.update_output_config テスト
# ---------------------------------------------------------------------------

class TestUpdateOutputConfig:
    """CaptionSystem.update_output_config() の動作を検証する。"""

    def _make_caption_system(self, output_device_index=None):
        from main import CaptionSystem
        config = {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-hotswap-0000"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
                "connect_timeout": 10,
                "reconnect_max_attempts": 5,
                "reconnect_backoff_base": 1.5,
                "max_session_minutes": 60,
            },
            "output": {"log_dir": "."},
        }
        device_info = {"index": 0, "name": "FakeInputDevice"}
        return CaptionSystem(
            config=config,
            device_info=device_info,
            model_name="gpt-realtime-translate",
            output_device_index=output_device_index,
        )

    def test_update_output_config_sets_device_index(self):
        """update_output_config(5) で _output_device_index が 5 になること。"""
        cs = self._make_caption_system(output_device_index=None)
        assert cs._output_device_index is None

        cs.update_output_config(5)

        assert cs._output_device_index == 5, (
            f"update_output_config(5) 後に _output_device_index が 5 であるべき。"
            f"実際: {cs._output_device_index}"
        )

    def test_update_output_config_sets_audio_output_mode_true_when_device_given(self):
        """update_output_config(3) で _audio_output_mode が True になること。"""
        cs = self._make_caption_system(output_device_index=None)
        assert cs._audio_output_mode is False

        cs.update_output_config(3)

        assert cs._audio_output_mode is True, (
            f"update_output_config(3) 後に _audio_output_mode が True であるべき。"
            f"実際: {cs._audio_output_mode}"
        )

    def test_update_output_config_sets_none_disables_output(self):
        """update_output_config(None) で _output_device_index=None, _audio_output_mode=False になること。"""
        cs = self._make_caption_system(output_device_index=2)
        assert cs._output_device_index == 2
        assert cs._audio_output_mode is True

        cs.update_output_config(None)

        assert cs._output_device_index is None, (
            f"update_output_config(None) 後に _output_device_index が None であるべき。"
            f"実際: {cs._output_device_index}"
        )
        assert cs._audio_output_mode is False, (
            f"update_output_config(None) 後に _audio_output_mode が False であるべき。"
            f"実際: {cs._audio_output_mode}"
        )

    def test_update_output_config_does_not_touch_audio_stream(self):
        """update_output_config は既存の _audio_stream を変更しないこと。

        ストリームの再起動は _restart_route_for_change に任せるため、
        update_output_config はストリームに触れてはならない。
        """
        cs = self._make_caption_system(output_device_index=2)
        fake_stream = MagicMock()
        cs._audio_stream = fake_stream

        cs.update_output_config(5)

        # stop() が呼ばれていないこと
        fake_stream.stop.assert_not_called()
        # _audio_stream が差し替えられていないこと
        assert cs._audio_stream is fake_stream, (
            "update_output_config が _audio_stream を変更してはならない"
        )

    def test_update_output_config_none_does_not_stop_stream(self):
        """update_output_config(None) でも既存ストリームを停止しないこと。"""
        cs = self._make_caption_system(output_device_index=2)
        fake_stream = MagicMock()
        cs._audio_stream = fake_stream

        cs.update_output_config(None)

        fake_stream.stop.assert_not_called()
        assert cs._audio_stream is fake_stream, (
            "update_output_config(None) が _audio_stream を変更してはならない"
        )


# ---------------------------------------------------------------------------
# app.py: RUNNING中に set_output_device が呼ばれないことを確認するテスト
# (Bug 1 修正: RUNNING中は update_output_config を使い、
#  ストリーム操作は _restart_route_for_change に任せる)
# ---------------------------------------------------------------------------

import app
from main import RouteState


class FakeRouteSystem:
    """CaptionSystem の最小スタブ。update_output_config と set_output_device を持つ。"""

    def __init__(self, state: RouteState = RouteState.IDLE):
        self._state = state
        self.set_output_device = MagicMock()
        self.update_output_config = MagicMock()

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


@pytest.fixture(autouse=True)
def reset_app_state():
    """各テスト前後に app モジュールのグローバル状態をリセットする。"""
    old_system = app._konnyaku_system
    old_running = app._konnyaku_running
    yield
    app._konnyaku_system = old_system
    app._konnyaku_running = old_running


# ---------------------------------------------------------------------------
# Bug 1 修正: _on_route_a_output_enable_change (RUNNING中)
# ---------------------------------------------------------------------------

class TestRouteAOutputEnableChangeNoDirectSetOutputDeviceWhenRunning:
    """RUNNING中は _on_route_a_output_enable_change が set_output_device を呼ばないこと。

    Bug 1 修正の核心:
    RUNNING中に直接 set_output_device を呼ぶとレースコンディションが発生する。
    代わりに update_output_config でインデックスのみ更新し、
    ストリーム操作は _restart_route_for_change に任せる。
    """

    def test_off_when_running_uses_update_output_config_not_set_output_device(self):
        """RUNNING中に音声出力 OFF にしたとき、set_output_device ではなく
        update_output_config(None) が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        # update_output_config(None) が呼ばれること
        fake_system.route_a_system.update_output_config.assert_called_once_with(None)
        # set_output_device は呼ばれないこと（RUNNING中は触れない）
        fake_system.route_a_system.set_output_device.assert_not_called()

    def test_on_when_running_uses_update_output_config_not_set_output_device(self):
        """RUNNING中に音声出力 ON にしたとき、set_output_device ではなく
        update_output_config(matched_index) が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.list_audio_devices", return_value=[{"name": "Speaker", "index": 3}]), \
             patch("app.find_device_by_name", return_value={"name": "Speaker", "index": 3}), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "Speaker"

            app._on_route_a_output_enable_change(sender=None, app_data=True, user_data=None)

        # update_output_config(3) が呼ばれること
        fake_system.route_a_system.update_output_config.assert_called_once_with(3)
        # set_output_device は呼ばれないこと
        fake_system.route_a_system.set_output_device.assert_not_called()

    def test_off_when_idle_uses_set_output_device_not_update_config(self):
        """IDLE中に音声出力 OFF にしたとき、set_output_device(None) が呼ばれること
        （停止中は直接操作が正しい）。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.IDLE)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        fake_system.route_a_system.set_output_device.assert_called_once_with(None)
        fake_system.route_a_system.update_output_config.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 1 修正: _on_route_b_output_enable_change (RUNNING中)
# ---------------------------------------------------------------------------

class TestRouteBOutputEnableChangeNoDirectSetOutputDeviceWhenRunning:
    """RUNNING中は _on_route_b_output_enable_change が set_output_device を呼ばないこと。"""

    def test_off_when_running_uses_update_output_config_not_set_output_device(self):
        """RUNNING中に系統B 音声出力 OFF にしたとき、update_output_config(None) が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_b_output_enable_change(sender=None, app_data=False, user_data=None)

        fake_system.route_b_system.update_output_config.assert_called_once_with(None)
        fake_system.route_b_system.set_output_device.assert_not_called()

    def test_on_when_running_uses_update_output_config_not_set_output_device(self):
        """RUNNING中に系統B 音声出力 ON にしたとき、update_output_config(matched_index) が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.list_audio_devices", return_value=[{"name": "Speaker", "index": 7}]), \
             patch("app.find_device_by_name", return_value={"name": "Speaker", "index": 7}), \
             patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "Speaker"

            app._on_route_b_output_enable_change(sender=None, app_data=True, user_data=None)

        fake_system.route_b_system.update_output_config.assert_called_once_with(7)
        fake_system.route_b_system.set_output_device.assert_not_called()


# ---------------------------------------------------------------------------
# Bug 2 修正: _on_route_a/b_output_device_change (RUNNING中のデバイス更新)
# ---------------------------------------------------------------------------

class TestRouteAOutputDeviceChangeUpdatesConfigBeforeRestart:
    """RUNNING中に出力デバイスを変更したとき、restart 前に update_output_config が
    呼ばれていること（Bug 2 修正）。

    Bug 2 の症状: RUNNING中に restart だけ呼ぶと、start() が古い _output_device_index を
    使ってしまい、実際には新しいデバイスに切り替わらない。
    """

    def test_device_change_when_running_calls_update_output_config_before_restart(self):
        """RUNNING中の出力デバイス変更で、update_output_config が restart より先に
        呼ばれること（restart 完了を待たずに確認）。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        # update_output_config が呼ばれたタイムスタンプを記録する
        call_log = []
        def track_update(idx):
            call_log.append(("update_output_config", idx, time.monotonic()))
        def track_stop(route_id):
            call_log.append(("stop_route", route_id, time.monotonic()))

        fake_system.route_a_system.update_output_config.side_effect = track_update
        fake_system.stop_route.side_effect = track_stop

        with patch("app.list_audio_devices", return_value=[{"name": "NewDevice", "index": 5}]), \
             patch("app.find_device_by_name", return_value={"name": "NewDevice", "index": 5}), \
             patch("app.dpg"):

            app._on_route_a_output_device_change(sender=None, app_data="NewDevice", user_data=None)

        # restart 完了を待つ（最大3秒）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        # update_output_config が呼ばれたこと
        fake_system.route_a_system.update_output_config.assert_called_once_with(5)

        # update_output_config が stop_route より先に呼ばれていること
        update_entries = [e for e in call_log if e[0] == "update_output_config"]
        stop_entries = [e for e in call_log if e[0] == "stop_route"]
        assert len(update_entries) == 1, f"update_output_config は1回呼ばれるべき: {call_log}"
        assert len(stop_entries) == 1, f"stop_route は1回呼ばれるべき: {call_log}"
        assert update_entries[0][2] <= stop_entries[0][2], (
            f"update_output_config が stop_route より先に呼ばれるべき。"
            f"update_ts={update_entries[0][2]:.6f}, stop_ts={stop_entries[0][2]:.6f}"
        )

    def test_device_change_when_running_does_not_call_set_output_device(self):
        """RUNNING中の出力デバイス変更で set_output_device が呼ばれないこと。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.list_audio_devices", return_value=[{"name": "NewDevice", "index": 5}]), \
             patch("app.find_device_by_name", return_value={"name": "NewDevice", "index": 5}), \
             patch("app.dpg"):

            app._on_route_a_output_device_change(sender=None, app_data="NewDevice", user_data=None)

        # restart 完了を待つ
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        # RUNNING中は set_output_device ではなく update_output_config を使うべき
        fake_system.route_a_system.set_output_device.assert_not_called()


class TestRouteBOutputDeviceChangeUpdatesConfigBeforeRestart:
    """系統B の RUNNING中出力デバイス変更でも同様の検証。"""

    def test_device_change_when_running_calls_update_output_config(self):
        """系統B RUNNING中の出力デバイス変更で update_output_config が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.list_audio_devices", return_value=[{"name": "HDMI", "index": 9}]), \
             patch("app.find_device_by_name", return_value={"name": "HDMI", "index": 9}), \
             patch("app.dpg"):

            app._on_route_b_output_device_change(sender=None, app_data="HDMI", user_data=None)

        # restart 完了を待つ
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.route_b_system.update_output_config.assert_called_once_with(9)
        fake_system.route_b_system.set_output_device.assert_not_called()
