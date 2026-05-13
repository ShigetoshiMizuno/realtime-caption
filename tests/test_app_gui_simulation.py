"""
test_app_gui_simulation.py

翻訳こんにゃくモード「開始」ボタンのコールバックを
Dear PyGui を実起動せずに pytest で検証するシミュレーションテスト。

設計方針:
- dpg の get_value / set_value / does_item_exist / configure_item を辞書モックに差し替える
- MultiCaptionSystem をモックして実際の音声/WebSocket を起動しない
- app モジュールのグローバル変数を patch.object で差し替える
- CI 環境でも動作すること（外部デバイス・API 不要）
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app  # noqa: E402


# ---------------------------------------------------------------------------
# ヘルパー: dpg をモックするコンテキスト
# ---------------------------------------------------------------------------

def _make_dpg_mock(widget_values: dict | None = None) -> MagicMock:
    """dpg モックを組み立てるファクトリ。widget_values から get_value の返値を制御する。"""
    widget_values = widget_values or {}
    mock = MagicMock()
    mock.does_item_exist.return_value = True
    mock.get_value.side_effect = lambda tag: widget_values.get(tag, "")
    mock.get_item_configuration.return_value = {"items": []}
    return mock


def _fake_devices():
    """テスト用フェイクデバイスリスト。"""
    return [
        {
            "index": 0,
            "name": "Test Loopback Device",
            "isLoopback": True,
            "hostApi": 0,
        },
        {
            "index": 1,
            "name": "Test Microphone",
            "isLoopback": False,
            "hostApi": 0,
        },
    ]


def _fake_config():
    """テスト用フェイク config。"""
    return {
        "openai": {"api_key": "sk-test-fake-00000000000000000000000000000000"},
        "stt": {"model": "tiny"},
        "translation": {"translation_model": "openai-realtime"},
    }


def _device_label_for(d: dict) -> str:
    """app._device_label と同等のラベル生成（テスト用）。"""
    name = d["name"]
    suffix = " [Loopback]" if d.get("isLoopback") else ""
    return f"{name}{suffix}"


def _make_widget_values(route_a_device_label: str, route_b_device_label: str) -> dict:
    """標準的なウィジェット値辞書を組み立てる。"""
    from constants import get_language_display_names, get_language_codes
    lang_names = get_language_display_names()
    lang_codes = get_language_codes()
    ja_name = lang_names[lang_codes.index("ja")] if "ja" in lang_codes else lang_names[0]
    en_name = lang_names[lang_codes.index("en")] if "en" in lang_codes else lang_names[-1]

    return {
        app.TAG_ROUTE_A_DEVICE_COMBO: route_a_device_label,
        app.TAG_ROUTE_B_DEVICE_COMBO: route_b_device_label,
        app.TAG_ROUTE_A_LANG_COMBO: ja_name,
        app.TAG_ROUTE_B_LANG_COMBO: en_name,
        app.TAG_ROUTE_A_OUTPUT_ENABLE: False,
        app.TAG_ROUTE_B_OUTPUT_ENABLE: False,
        app.TAG_ROUTE_A_OUTPUT_DEVICE_COMBO: "(なし)",
        app.TAG_ROUTE_B_OUTPUT_DEVICE_COMBO: "(なし)",
        app.TAG_ROUTE_A_OUTPUT_VOLUME: 1.0,
        app.TAG_ROUTE_B_OUTPUT_VOLUME: 1.0,
    }


# ---------------------------------------------------------------------------
# テストクラス
# ---------------------------------------------------------------------------

class TestKonnyakuStartFlow:
    """翻訳こんにゃくモード開始ボタンの挙動を GUI 起動なしで検証する。"""

    def test_start_creates_multi_caption_system_with_correct_route_configs(self):
        """
        GUI のウィジェット値（デバイス選択・言語）が RouteConfig として
        正しく MultiCaptionSystem に渡されること。
        """
        from constants import get_language_display_names, get_language_codes

        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])  # "Test Loopback Device [Loopback]"
        route_b_label = _device_label_for(fake_devices[1])  # "Test Microphone"

        widget_values = _make_widget_values(route_a_label, route_b_label)
        mock_dpg = _make_dpg_mock(widget_values)

        lang_names = get_language_display_names()
        lang_codes = get_language_codes()
        ja_code = "ja" if "ja" in lang_codes else lang_codes[0]
        en_code = "en" if "en" in lang_codes else lang_codes[-1]

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem") as mock_mcs,
        ):
            mock_mcs.return_value = MagicMock()
            app._on_konnyaku_start_stop_click()

        mock_mcs.assert_called_once()
        call_kwargs = mock_mcs.call_args.kwargs
        route_a = call_kwargs["route_a"]
        route_b = call_kwargs["route_b"]

        assert route_a.route_id == "a"
        assert route_a.target_language_code == ja_code
        assert route_a.input_device_info["index"] == 0

        assert route_b.route_id == "b"
        assert route_b.target_language_code == en_code
        assert route_b.input_device_info["index"] == 1

    def test_start_invokes_system_start(self):
        """`_konnyaku_system.start()` がボタン押下後に必ず呼ばれること。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)
        mock_dpg = _make_dpg_mock(widget_values)
        mock_instance = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", return_value=mock_instance),
        ):
            app._on_konnyaku_start_stop_click()

        mock_instance.start.assert_called_once()

    def test_start_sets_konnyaku_running_true(self):
        """起動成功後に _konnyaku_running が True になること。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)
        mock_dpg = _make_dpg_mock(widget_values)
        mock_instance = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None) as _ks_patch,
            patch.object(app, "_konnyaku_running", False) as _kr_patch,
            patch.object(app, "MultiCaptionSystem", return_value=mock_instance),
        ):
            app._on_konnyaku_start_stop_click()
            assert app._konnyaku_running is True

    def test_start_does_not_swallow_exception(self):
        """
        `MultiCaptionSystem.__init__` が例外を投げた場合、
        GUI ステータスバーにエラーメッセージが表示されること（クラッシュ防止）。

        Task A: _on_konnyaku_start_stop_click を try/except で囲む実装が必要。
        """
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)

        set_value_calls: dict[str, str] = {}

        mock_dpg = _make_dpg_mock(widget_values)
        mock_dpg.set_value.side_effect = lambda tag, val: set_value_calls.update({tag: val})

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", side_effect=RuntimeError("boom")),
        ):
            # 例外が外に漏れてはならない
            app._on_konnyaku_start_stop_click()

        # TAG_STATUS_STATE にエラーメッセージが設定されていること
        assert app.TAG_STATUS_STATE in set_value_calls, (
            "エラー発生時に TAG_STATUS_STATE に set_value が呼ばれていない"
        )
        assert "boom" in set_value_calls[app.TAG_STATUS_STATE] or \
               "RuntimeError" in set_value_calls[app.TAG_STATUS_STATE] or \
               "失敗" in set_value_calls[app.TAG_STATUS_STATE], (
            f"エラーメッセージにエラー情報が含まれない: {set_value_calls[app.TAG_STATUS_STATE]!r}"
        )

    def test_start_resets_state_on_failure(self):
        """起動失敗時に _konnyaku_running が False のまま、_konnyaku_system が None になること。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)
        mock_dpg = _make_dpg_mock(widget_values)

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", side_effect=RuntimeError("init failed")),
        ):
            app._on_konnyaku_start_stop_click()
            assert app._konnyaku_running is False
            assert app._konnyaku_system is None

    def test_existing_system_running_rejects_konnyaku_start(self):
        """既存単独モード稼働中はこんにゃく起動が拒否されること。"""
        mock_dpg = _make_dpg_mock()
        mock_system = MagicMock()
        mock_mcs = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_system", mock_system),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            app._on_konnyaku_start_stop_click()

        # MultiCaptionSystem は生成されないこと
        mock_mcs.assert_not_called()
        # 警告メッセージが出ること
        mock_dpg.set_value.assert_called()

    def test_device_not_found_no_crash(self):
        """選択デバイス名が _devices に存在しない場合、早期 return して例外を投げないこと。"""
        widget_values = {
            app.TAG_ROUTE_A_DEVICE_COMBO: "存在しないデバイス",
            app.TAG_ROUTE_B_DEVICE_COMBO: "これも存在しない",
        }
        mock_dpg = _make_dpg_mock(widget_values)
        mock_mcs = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", _fake_devices()),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            # 例外が外に漏れてはいけない
            app._on_konnyaku_start_stop_click()

        # MultiCaptionSystem は生成されないこと
        mock_mcs.assert_not_called()

    def test_stop_shuts_down_system(self):
        """停止ボタン押下時に _konnyaku_system.shutdown() が呼ばれること。"""
        mock_dpg = _make_dpg_mock()
        mock_instance = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_konnyaku_running", True),
            patch.object(app, "_konnyaku_system", mock_instance),
        ):
            app._on_konnyaku_start_stop_click()

        mock_instance.shutdown.assert_called_once()
        assert app._konnyaku_system is None
        assert app._konnyaku_running is False

    def test_thread_error_callback_called_on_thread_crash(self):
        """
        MultiCaptionSystem が on_thread_error コールバックを受け取れること。

        Task B: MultiCaptionSystem.__init__ に on_thread_error 引数が追加されていること、
        かつ app 側からコールバックを渡していること。
        """
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)
        mock_dpg = _make_dpg_mock(widget_values)
        mock_instance = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", return_value=mock_instance) as mock_mcs_cls,
        ):
            app._on_konnyaku_start_stop_click()

        # MultiCaptionSystem が on_thread_error キーワード引数付きで呼ばれること
        call_kwargs = mock_mcs_cls.call_args.kwargs
        assert "on_thread_error" in call_kwargs, (
            "MultiCaptionSystem の呼び出しに on_thread_error が含まれていない"
        )
        assert callable(call_kwargs["on_thread_error"]), (
            "on_thread_error が callable でない"
        )

    def test_thread_error_handler_updates_status_bar(self):
        """
        on_thread_error ハンドラーを直接呼び出すと GUI ステータスバーが更新されること。

        Task B: _konnyaku_thread_error_handler の動作を確認する。
        """
        set_value_calls: dict[str, str] = {}
        mock_dpg = _make_dpg_mock()
        mock_dpg.set_value.side_effect = lambda tag, val: set_value_calls.update({tag: val})

        with patch.object(app, "dpg", mock_dpg):
            # ハンドラーが存在していること
            assert hasattr(app, "_konnyaku_thread_error_handler"), (
                "_konnyaku_thread_error_handler が app に定義されていない"
            )
            app._konnyaku_thread_error_handler(
                "a", RuntimeError("thread crashed"), "Traceback..."
            )

        assert app.TAG_STATUS_STATE in set_value_calls, (
            "スレッドエラー時に TAG_STATUS_STATE に set_value が呼ばれていない"
        )


# ---------------------------------------------------------------------------
# Task D: モックなし MultiCaptionSystem テスト（クラッシュ再現用）
# ---------------------------------------------------------------------------

class TestKonnyakuStartIntegration:
    """
    MultiCaptionSystem を実際に呼び出すテスト（Task D）。
    実際の音声デバイス・API は不要にするため、CaptionSystem の asyncio.run 部分は
    即座に返るようにする。
    """

    def test_multi_caption_system_init_with_fake_config(self):
        """
        MultiCaptionSystem.__init__ がフェイク設定・フェイクデバイスで
        例外なく完了するかを確認する（実 WebSocket / 音声デバイス起動なし）。

        モックなしで MultiCaptionSystem を構築し、どこで例外が発生するかを観察する。
        init 成功後 .start() は呼ばず、スレッドは起動しない。
        """
        from main import MultiCaptionSystem, RouteConfig

        fake_device_a = {
            "index": 0,
            "name": "Test Loopback Device",
            "isLoopback": True,
            "hostApi": 0,
        }
        fake_device_b = {
            "index": 1,
            "name": "Test Microphone",
            "isLoopback": False,
            "hostApi": 0,
        }
        fake_cfg = {
            "openai": {"api_key": "sk-test-fake-00000000000000000000000000000000"},
            "stt": {"model": "tiny"},
            "translation": {"translation_model": "openai-realtime"},
            "openai_realtime": {"target_language_code": "ja"},
        }

        route_a = RouteConfig(
            route_id="a",
            input_device_info=fake_device_a,
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
        )
        route_b = RouteConfig(
            route_id="b",
            input_device_info=fake_device_b,
            target_language_code="en",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
        )

        # MultiCaptionSystem の __init__ だけを実行する（.start() は呼ばない）
        # ここで例外が出ればそれがクラッシュ原因候補
        system = MultiCaptionSystem(
            config=fake_cfg,
            route_a=route_a,
            route_b=route_b,
        )
        # __init__ が成功したら route_a / route_b が存在すること
        assert system.route_a_system is not None
        assert system.route_b_system is not None
