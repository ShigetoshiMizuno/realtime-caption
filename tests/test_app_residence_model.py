"""
tests/test_app_residence_model.py

PR-4: app.py 常駐モデル化テスト。

対象機能:
  - main() で MultiCaptionSystem を即生成（常駐モデル）
  - 開始/停止ボタンで start_all() / stop_all() を呼ぶ（再生成しない）
  - 系統チェック OFF/ON で stop_route() / start_route() を呼ぶ (B-14)
  - 入力デバイス変更で stop() + デバイス更新 + start() を呼ぶ (B-15)

設計方針:
  - dpg / MultiCaptionSystem をモックして実機不要
  - _konnyaku_system の同一性検証でインスタンス再利用を確認
"""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app  # noqa: E402


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------


def _make_dpg_mock(widget_values: dict | None = None) -> MagicMock:
    widget_values = widget_values or {}
    mock = MagicMock()
    mock.does_item_exist.return_value = True
    mock.get_value.side_effect = lambda tag: widget_values.get(tag, "")
    mock.get_item_configuration.return_value = {"items": []}
    return mock


def _fake_devices():
    return [
        {"index": 0, "name": "Test Loopback Device", "isLoopback": True, "hostApi": 0},
        {"index": 1, "name": "Test Microphone", "isLoopback": False, "hostApi": 0},
    ]


def _fake_config():
    return {
        "openai": {"api_key": "sk-test-fake-00000000000000000000000000000000"},
        "stt": {"model": "tiny"},
        "translation": {"translation_model": "openai-realtime"},
    }


# ---------------------------------------------------------------------------
# 常駐モデル生成テスト
# ---------------------------------------------------------------------------


class TestResidenceModelCreation:
    """main() で MultiCaptionSystem が即生成されること（常駐モデル）。"""

    def test_create_konnyaku_system_function_exists(self):
        """_create_konnyaku_system 関数が app に定義されていること。"""
        assert hasattr(app, "_create_konnyaku_system"), (
            "_create_konnyaku_system が app に定義されていない"
        )

    def test_create_konnyaku_system_sets_global(self):
        """_create_konnyaku_system() を呼ぶと _konnyaku_system が非 None になること。"""
        fake_devices = _fake_devices()
        mock_mcs = MagicMock()
        mock_instance = MagicMock()
        mock_mcs.return_value = mock_instance

        with (
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            app._create_konnyaku_system()
            assert app._konnyaku_system is not None, (
                "_create_konnyaku_system() 後に _konnyaku_system が None のまま"
            )

    def test_create_konnyaku_system_is_idempotent(self):
        """_konnyaku_system が既に存在する場合は再生成しないこと。"""
        existing_instance = MagicMock()
        mock_mcs = MagicMock()

        with (
            patch.object(app, "_devices", _fake_devices()),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_konnyaku_system", existing_instance),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            app._create_konnyaku_system()
            # MultiCaptionSystem は新規生成されないこと
            mock_mcs.assert_not_called()
            # 既存インスタンスが保持されること
            assert app._konnyaku_system is existing_instance

    def test_main_calls_create_konnyaku_system(self):
        """main() が起動時に _create_konnyaku_system() を呼ぶこと。"""
        mock_dpg = MagicMock()
        mock_dpg.is_dearpygui_running.return_value = False

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "load_config", return_value=_fake_config()),
            patch.object(app, "list_audio_devices", return_value=_fake_devices()),
            patch.object(app, "_start_rpc_server", MagicMock()),
            patch.object(app, "_build_gui", MagicMock()),
            patch.object(app, "_save_settings", MagicMock()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_create_konnyaku_system") as mock_create,
            patch("sys.argv", ["app.py"]),
        ):
            app.main()
            mock_create.assert_called_once()

    def test_main_terminates_konnyaku_system_on_exit(self):
        """main() 終了時に _konnyaku_system.terminate() が呼ばれること。"""
        mock_dpg = MagicMock()
        mock_dpg.is_dearpygui_running.return_value = False

        mock_instance = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "load_config", return_value=_fake_config()),
            patch.object(app, "list_audio_devices", return_value=_fake_devices()),
            patch.object(app, "_start_rpc_server", MagicMock()),
            patch.object(app, "_build_gui", MagicMock()),
            patch.object(app, "_save_settings", MagicMock()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_create_konnyaku_system", MagicMock()),
            patch("sys.argv", ["app.py"]),
        ):
            app.main()
            mock_instance.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# 開始/停止ボタンが start_all/stop_all を呼ぶテスト
# ---------------------------------------------------------------------------


class TestStartStopUsesStartAllStopAll:
    """開始/停止ボタンで start_all() / stop_all() が呼ばれること（常駐モデル）。"""

    def test_start_button_calls_start_route_on_existing_system(self):
        """開始ボタン押下時、既に存在する _konnyaku_system.start_route() が呼ばれること。

        PR-4 設計: 両系統 ON のとき start_route('a') と start_route('b') が呼ばれる。
        """
        fake_devices = _fake_devices()
        mock_instance = MagicMock()
        mock_instance.route_a_system = MagicMock()
        mock_instance.route_b_system = MagicMock()
        widget_values = {
            app.TAG_ROUTE_A_ENABLE: True,
            app.TAG_ROUTE_B_ENABLE: True,
            app.TAG_ROUTE_A_DEVICE_COMBO: "Test Loopback Device [Loopback]",
            app.TAG_ROUTE_B_DEVICE_COMBO: "Test Microphone",
        }
        mock_dpg = _make_dpg_mock(widget_values)

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_konnyaku_running", False),
        ):
            app._on_konnyaku_start_stop_click()
            calls = [c.args[0] for c in mock_instance.start_route.call_args_list]
            assert "a" in calls, f"start_route('a') が呼ばれていない: {calls}"
            assert "b" in calls, f"start_route('b') が呼ばれていない: {calls}"

    def test_start_button_does_not_create_new_instance_if_system_exists(self):
        """既に _konnyaku_system が存在するとき、新しい MultiCaptionSystem を生成しないこと。"""
        fake_devices = _fake_devices()
        existing_instance = MagicMock()
        widget_values = {
            app.TAG_ROUTE_A_ENABLE: True,
            app.TAG_ROUTE_B_ENABLE: True,
            app.TAG_ROUTE_A_DEVICE_COMBO: "Test Loopback Device [Loopback]",
            app.TAG_ROUTE_B_DEVICE_COMBO: "Test Microphone",
        }
        mock_dpg = _make_dpg_mock(widget_values)

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", existing_instance),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem") as mock_mcs,
        ):
            app._on_konnyaku_start_stop_click()
            # MultiCaptionSystem は新規生成されないこと
            mock_mcs.assert_not_called()
            # 既存インスタンスが保持されること
            assert app._konnyaku_system is existing_instance

    def test_stop_button_calls_stop_all_not_shutdown(self):
        """停止ボタン押下時に stop_all() が呼ばれ、_konnyaku_system が None にならないこと。"""
        mock_dpg = _make_dpg_mock()
        mock_instance = MagicMock()
        stop_all_called = threading.Event()

        def fake_stop_all():
            stop_all_called.set()

        mock_instance.stop_all.side_effect = fake_stop_all

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_konnyaku_running", True),
            patch.object(app, "_konnyaku_system", mock_instance),
        ):
            app._on_konnyaku_start_stop_click()
            # バックグラウンドスレッドの完了を待つ
            stop_all_called.wait(timeout=3.0)
            import time as _time
            _time.sleep(0.1)

            mock_instance.stop_all.assert_called_once()
            # 常駐モデル: _konnyaku_system は None にならない（with スコープ内で検証）
            assert app._konnyaku_system is not None, (
                "停止後に _konnyaku_system が None になっている（常駐モデルでは保持するべき）"
            )

    def test_start_stop_cycle_reuses_same_instance(self):
        """開始/停止を繰り返しても _konnyaku_system は同じインスタンスであること。"""
        fake_devices = _fake_devices()
        mock_instance = MagicMock()
        widget_values = {
            app.TAG_ROUTE_A_ENABLE: True,
            app.TAG_ROUTE_B_ENABLE: True,
            app.TAG_ROUTE_A_DEVICE_COMBO: "Test Loopback Device [Loopback]",
            app.TAG_ROUTE_B_DEVICE_COMBO: "Test Microphone",
        }
        mock_dpg = _make_dpg_mock(widget_values)

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_konnyaku_running", False),
        ):
            app._on_konnyaku_start_stop_click()  # 開始
            instance_after_start = app._konnyaku_system

        assert instance_after_start is mock_instance, (
            "開始後に _konnyaku_system が別のインスタンスに変わっている"
        )


# ---------------------------------------------------------------------------
# B-14: 系統チェック ON/OFF で start_route / stop_route を呼ぶテスト
# ---------------------------------------------------------------------------


class TestRouteEnableCallbackB14:
    """系統チェック ON/OFF が start_route / stop_route を呼ぶことのテスト (B-14)。"""

    def test_on_route_a_enable_change_function_exists(self):
        """_on_route_a_enable_change 関数が app に定義されていること。"""
        assert hasattr(app, "_on_route_a_enable_change"), (
            "_on_route_a_enable_change が app に定義されていない"
        )

    def test_on_route_b_enable_change_function_exists(self):
        """_on_route_b_enable_change 関数が app に定義されていること。"""
        assert hasattr(app, "_on_route_b_enable_change"), (
            "_on_route_b_enable_change が app に定義されていない"
        )

    def test_route_a_enable_off_calls_stop_route_a(self):
        """系統1 チェック OFF で stop_route('a') が呼ばれること（B-14）。"""
        mock_instance = MagicMock()

        with patch.object(app, "_konnyaku_system", mock_instance):
            app._on_route_a_enable_change(sender=None, app_data=False, user_data=None)

        mock_instance.stop_route.assert_called_once_with("a")
        mock_instance.start_route.assert_not_called()

    def test_route_a_enable_on_calls_start_route_a(self):
        """系統1 チェック ON で start_route('a') が呼ばれること（B-14）。"""
        mock_instance = MagicMock()

        with patch.object(app, "_konnyaku_system", mock_instance):
            app._on_route_a_enable_change(sender=None, app_data=True, user_data=None)

        mock_instance.start_route.assert_called_once_with("a")
        mock_instance.stop_route.assert_not_called()

    def test_route_b_enable_off_calls_stop_route_b(self):
        """系統2 チェック OFF で stop_route('b') が呼ばれること（B-14）。"""
        mock_instance = MagicMock()

        with patch.object(app, "_konnyaku_system", mock_instance):
            app._on_route_b_enable_change(sender=None, app_data=False, user_data=None)

        mock_instance.stop_route.assert_called_once_with("b")
        mock_instance.start_route.assert_not_called()

    def test_route_b_enable_on_calls_start_route_b(self):
        """系統2 チェック ON で start_route('b') が呼ばれること（B-14）。"""
        mock_instance = MagicMock()

        with patch.object(app, "_konnyaku_system", mock_instance):
            app._on_route_b_enable_change(sender=None, app_data=True, user_data=None)

        mock_instance.start_route.assert_called_once_with("b")
        mock_instance.stop_route.assert_not_called()

    def test_route_a_enable_change_no_system_does_not_crash(self):
        """_konnyaku_system が None の場合、チェック変更が例外なく終了すること。"""
        with patch.object(app, "_konnyaku_system", None):
            # 例外が出なければ OK
            app._on_route_a_enable_change(sender=None, app_data=False, user_data=None)

    def test_route_b_enable_change_no_system_does_not_crash(self):
        """_konnyaku_system が None の場合、チェック変更が例外なく終了すること。"""
        with patch.object(app, "_konnyaku_system", None):
            app._on_route_b_enable_change(sender=None, app_data=True, user_data=None)

    def test_build_gui_route_a_enable_checkbox_uses_new_callback(self):
        """_build_gui 内の TAG_ROUTE_A_ENABLE checkbox が _on_route_a_enable_change callback を使っていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        # TAG_ROUTE_A_ENABLE を含む add_checkbox ブロックに _on_route_a_enable_change が存在すること
        lines = source.splitlines()
        found = False
        for idx, line in enumerate(lines):
            if "TAG_ROUTE_A_ENABLE" in line:
                for j in range(max(0, idx - 5), min(len(lines), idx + 10)):
                    if "_on_route_a_enable_change" in lines[j]:
                        found = True
                        break
        assert found, (
            "TAG_ROUTE_A_ENABLE の add_checkbox に _on_route_a_enable_change callback が設定されていない"
        )

    def test_build_gui_route_b_enable_checkbox_uses_new_callback(self):
        """_build_gui 内の TAG_ROUTE_B_ENABLE checkbox が _on_route_b_enable_change callback を使っていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        lines = source.splitlines()
        found = False
        for idx, line in enumerate(lines):
            if "TAG_ROUTE_B_ENABLE" in line:
                for j in range(max(0, idx - 5), min(len(lines), idx + 10)):
                    if "_on_route_b_enable_change" in lines[j]:
                        found = True
                        break
        assert found, (
            "TAG_ROUTE_B_ENABLE の add_checkbox に _on_route_b_enable_change callback が設定されていない"
        )


# ---------------------------------------------------------------------------
# B-15: 入力デバイス変更で stop + 更新 + start を呼ぶテスト
# ---------------------------------------------------------------------------


class TestInputDeviceChangeCallbackB15:
    """入力デバイス変更が stop + デバイス更新 + start を呼ぶことのテスト (B-15)。"""

    def test_on_route_a_device_change_function_exists(self):
        """_on_route_a_device_change 関数が app に定義されていること。"""
        assert hasattr(app, "_on_route_a_device_change"), (
            "_on_route_a_device_change が app に定義されていない"
        )

    def test_on_route_b_device_change_function_exists(self):
        """_on_route_b_device_change 関数が app に定義されていること。"""
        assert hasattr(app, "_on_route_b_device_change"), (
            "_on_route_b_device_change が app に定義されていない"
        )

    def test_route_a_device_change_restarts_route_when_running(self):
        """稼働中に系統1 入力デバイス変更で stop() + start() が呼ばれること（B-15）。"""
        from main import RouteState

        fake_devices = _fake_devices()
        new_device = fake_devices[1]  # "Test Microphone"
        new_label = "Test Microphone"

        mock_route_a = MagicMock()
        mock_route_a.state = RouteState.RUNNING
        mock_instance = MagicMock()
        mock_instance.route_a_system = mock_route_a

        with (
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_devices", fake_devices),
        ):
            app._on_route_a_device_change(
                sender=None, app_data=new_label, user_data=None
            )

        mock_route_a.stop.assert_called_once()
        mock_route_a.start.assert_called_once()

    def test_route_a_device_change_updates_device_info(self):
        """系統1 入力デバイス変更時に _device_info が新しいデバイスに更新されること（B-15）。"""
        from main import RouteState

        fake_devices = _fake_devices()
        new_device = fake_devices[1]  # "Test Microphone"
        new_label = "Test Microphone"

        mock_route_a = MagicMock()
        mock_route_a.state = RouteState.RUNNING
        mock_instance = MagicMock()
        mock_instance.route_a_system = mock_route_a

        with (
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_devices", fake_devices),
        ):
            app._on_route_a_device_change(
                sender=None, app_data=new_label, user_data=None
            )

        assert mock_route_a._device_info == new_device, (
            f"_device_info が新しいデバイスに更新されていない: {mock_route_a._device_info}"
        )

    def test_route_a_device_change_no_restart_when_idle(self):
        """IDLE 状態のとき、デバイス変更で stop/start は呼ばれないこと（デバイス更新のみ）。"""
        from main import RouteState

        fake_devices = _fake_devices()
        new_label = "Test Microphone"

        mock_route_a = MagicMock()
        mock_route_a.state = RouteState.IDLE
        mock_instance = MagicMock()
        mock_instance.route_a_system = mock_route_a

        with (
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_devices", fake_devices),
        ):
            app._on_route_a_device_change(
                sender=None, app_data=new_label, user_data=None
            )

        mock_route_a.stop.assert_not_called()
        mock_route_a.start.assert_not_called()

    def test_route_b_device_change_restarts_route_when_running(self):
        """稼働中に系統2 入力デバイス変更で stop() + start() が呼ばれること（B-15）。"""
        from main import RouteState

        fake_devices = _fake_devices()
        new_label = "Test Loopback Device [Loopback]"

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.RUNNING
        mock_instance = MagicMock()
        mock_instance.route_b_system = mock_route_b

        with (
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_devices", fake_devices),
        ):
            app._on_route_b_device_change(
                sender=None, app_data=new_label, user_data=None
            )

        mock_route_b.stop.assert_called_once()
        mock_route_b.start.assert_called_once()

    def test_route_a_device_change_no_system_does_not_crash(self):
        """_konnyaku_system が None の場合、デバイス変更が例外なく終了すること。"""
        with patch.object(app, "_konnyaku_system", None):
            app._on_route_a_device_change(
                sender=None, app_data="Test Microphone", user_data=None
            )

    def test_route_b_device_change_unknown_device_does_not_crash(self):
        """存在しないデバイス名を渡した場合、例外なく終了すること（早期 return）。"""
        mock_instance = MagicMock()
        mock_instance.route_b_system = MagicMock()

        with (
            patch.object(app, "_konnyaku_system", mock_instance),
            patch.object(app, "_devices", _fake_devices()),
        ):
            app._on_route_b_device_change(
                sender=None, app_data="存在しないデバイス", user_data=None
            )

        # stop/start は呼ばれないこと
        mock_instance.route_b_system.stop.assert_not_called()
        mock_instance.route_b_system.start.assert_not_called()
