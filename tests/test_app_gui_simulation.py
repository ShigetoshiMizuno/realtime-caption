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
import threading
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
        # Issue #43: 系統 ON/OFF トグル（デフォルト両方有効）
        app.TAG_ROUTE_A_ENABLE: True,
        app.TAG_ROUTE_B_ENABLE: True,
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
        """停止ボタン押下時に _konnyaku_system.shutdown() が別スレッドで呼ばれること。"""
        mock_dpg = _make_dpg_mock()
        mock_instance = MagicMock()
        shutdown_called = threading.Event()

        def fake_shutdown():
            shutdown_called.set()

        mock_instance.shutdown.side_effect = fake_shutdown

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_konnyaku_running", True),
            patch.object(app, "_konnyaku_system", mock_instance),
        ):
            app._on_konnyaku_start_stop_click()
            # バックグラウンドスレッドの完了を待つ（最大3秒）
            shutdown_called.wait(timeout=3.0)

        mock_instance.shutdown.assert_called_once()
        assert app._konnyaku_system is None
        assert app._konnyaku_running is False

    def test_stop_shows_stopping_label_immediately(self):
        """停止ボタン押下直後にボタンラベルが「停止中...」になり disabled になること。"""
        configure_item_calls: list[dict] = []
        mock_dpg = _make_dpg_mock()
        mock_dpg.configure_item.side_effect = lambda tag, **kwargs: configure_item_calls.append(
            {"tag": tag, **kwargs}
        )

        shutdown_start = threading.Event()
        shutdown_done = threading.Event()
        mock_instance = MagicMock()

        def fake_shutdown():
            # shutdown 開始を通知してから解放シグナルを待つ
            shutdown_start.set()
            shutdown_done.wait(timeout=3.0)

        mock_instance.shutdown.side_effect = fake_shutdown

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_konnyaku_running", True),
            patch.object(app, "_konnyaku_system", mock_instance),
        ):
            app._on_konnyaku_start_stop_click()
            # shutdown が始まるまで待つ（GUI が停止中...に更新された後）
            shutdown_started = shutdown_start.wait(timeout=3.0)
            # 最初の configure_item 呼び出しで「停止中...」+ enabled=False になっていること
            stopping_calls = [
                c for c in configure_item_calls
                if c.get("tag") == app.TAG_KONNYAKU_START_BTN and c.get("label") == "停止中..."
            ]
            # shutdown スレッドを解放（patch スコープ内で実行されるよう）
            shutdown_done.set()
            # スレッド完了を待つ（patch スコープ内で finally が動くよう）
            import time as _time
            _time.sleep(0.2)

        assert shutdown_started, "shutdown が3秒以内に開始しなかった"
        assert len(stopping_calls) >= 1, (
            f"「停止中...」ラベルへの configure_item が呼ばれていない: {configure_item_calls}"
        )
        assert stopping_calls[0].get("enabled") is False, (
            f"「停止中...」時に enabled=False になっていない: {stopping_calls[0]}"
        )

    def test_stop_restores_button_after_shutdown_complete(self):
        """シャットダウン完了後にボタンラベルが「開始」に戻り enabled になること。"""
        configure_item_calls: list[dict] = []
        mock_dpg = _make_dpg_mock()
        mock_dpg.configure_item.side_effect = lambda tag, **kwargs: configure_item_calls.append(
            {"tag": tag, **kwargs}
        )

        mock_instance = MagicMock()
        mock_instance.shutdown.side_effect = lambda: None  # 即完了

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_konnyaku_running", True),
            patch.object(app, "_konnyaku_system", mock_instance),
        ):
            app._on_konnyaku_start_stop_click()
            # バックグラウンドスレッドが完了するまで patch スコープ内でループ待機
            import time as _time
            deadline = _time.monotonic() + 3.0
            while _time.monotonic() < deadline:
                restored = [
                    c for c in configure_item_calls
                    if c.get("tag") == app.TAG_KONNYAKU_START_BTN
                    and c.get("enabled") is True
                ]
                if restored:
                    break
                _time.sleep(0.05)

            # shutdown 完了後に「開始」+ enabled=True に戻ること（スコープ内で検証）
            restored_calls = [
                c for c in configure_item_calls
                if c.get("tag") == app.TAG_KONNYAKU_START_BTN
                and c.get("enabled") is True
            ]

        assert len(restored_calls) >= 1, (
            f"shutdown 後にボタンが enabled=True に戻っていない: {configure_item_calls}"
        )

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


# ---------------------------------------------------------------------------
# CLI 自動操作モード (--auto-konnyaku) テスト
# ---------------------------------------------------------------------------

class TestAutoKonnyakuRunner:
    """_auto_konnyaku_runner が正しい順序で各コールバックを呼ぶことを検証する。"""

    def test_auto_konnyaku_runner_executes_correct_sequence(self):
        """
        _auto_konnyaku_runner が正しい順序で各コールバックを呼ぶこと:
        1. _on_konnyaku_preset_click
        2. _on_konnyaku_start_stop_click (開始)
        3. _on_konnyaku_start_stop_click (停止)
        4. dpg.stop_dearpygui
        time.sleep をモックしてスキップし、順序のみ検証する。
        """
        call_order: list[str] = []

        def fake_preset():
            call_order.append("preset")

        def fake_start_stop():
            call_order.append("start_stop")

        def fake_stop_dpg():
            call_order.append("stop_dearpygui")

        mock_dpg = MagicMock()
        mock_dpg.stop_dearpygui.side_effect = fake_stop_dpg

        with (
            patch.object(app, "_on_konnyaku_preset_click", fake_preset),
            patch.object(app, "_on_konnyaku_start_stop_click", fake_start_stop),
            patch.object(app, "dpg", mock_dpg),
            patch("app.time") as mock_time,
        ):
            # time モジュールの sleep をノーオプに
            mock_time.sleep = MagicMock()
            # _auto_konnyaku_runner を直接呼ぶ（スレッド経由でなく同期的に）
            app._auto_konnyaku_runner(duration=0)

        # 呼び出し順序の検証
        assert call_order == ["preset", "start_stop", "start_stop", "stop_dearpygui"], (
            f"呼び出し順序が期待と異なる: {call_order}"
        )

    def test_auto_konnyaku_runner_sleeps_correct_durations(self):
        """
        _auto_konnyaku_runner が正しい sleep 引数で time.sleep を呼ぶこと:
        - 5秒 (モデルロード待機)
        - 1秒 (GUI 反映待機)
        - N秒 (duration)
        shutdown 完了待ちはループ（time.sleep(0.2) × 複数回）に変更されたため
        固定値 3 は含まれないこと。
        """
        sleep_args: list[float] = []

        mock_dpg = MagicMock()

        with (
            patch.object(app, "_on_konnyaku_preset_click", MagicMock()),
            patch.object(app, "_on_konnyaku_start_stop_click", MagicMock()),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "dpg", mock_dpg),
            patch("app.time") as mock_time,
        ):
            def record_sleep(n):
                sleep_args.append(n)
            mock_time.sleep = record_sleep
            mock_time.monotonic = __import__("time").monotonic
            app._auto_konnyaku_runner(duration=10)

        # 5, 1, 10 が含まれていること（shutdown 完了待ちは _konnyaku_running=False で即抜ける）
        assert sleep_args[:3] == [5, 1, 10], (
            f"sleep の呼び出しシーケンスが期待と異なる: {sleep_args}"
        )
        # 固定値 3 の sleep が含まれていないこと（ループ待機に変更されたため）
        assert 3 not in sleep_args, (
            f"固定 sleep(3) が残っている（ループ待機に変更されたはず）: {sleep_args}"
        )

    def test_auto_konnyaku_runner_waits_for_konnyaku_running_false(self):
        """
        _auto_konnyaku_runner の停止ボタン押下後、_konnyaku_running が False になるまで
        ループ待機すること（固定 sleep(3) ではなく）。
        """
        # _konnyaku_running が途中で True → False に変わるシナリオ
        running_flag = [True]
        call_count = [0]

        def fake_start_stop():
            call_count[0] += 1
            # 2回目の呼び出し（停止ボタン）で _konnyaku_running を False に設定
            if call_count[0] >= 2:
                running_flag[0] = False

        mock_dpg = MagicMock()

        with (
            patch.object(app, "_on_konnyaku_preset_click", MagicMock()),
            patch.object(app, "_on_konnyaku_start_stop_click", fake_start_stop),
            patch.object(app, "dpg", mock_dpg),
            patch("app.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            mock_time.monotonic = __import__("time").monotonic
            # _konnyaku_running を running_flag[0] で管理
            with patch.object(app, "_konnyaku_running", new_callable=lambda: type(
                "_Prop", (), {
                    "__get__": lambda self, obj, cls: running_flag[0],
                    "__set__": lambda self, obj, val: running_flag.__setitem__(0, val),
                }
            )()):
                app._auto_konnyaku_runner(duration=0)

        # dpg.stop_dearpygui が呼ばれること（ループ抜け後）
        mock_dpg.stop_dearpygui.assert_called_once()

    def test_auto_konnyaku_runner_calls_stop_dearpygui_on_exception(self):
        """
        _auto_konnyaku_runner 内で例外が発生した場合でも dpg.stop_dearpygui が呼ばれること。
        """
        mock_dpg = MagicMock()

        with (
            patch.object(app, "_on_konnyaku_preset_click", side_effect=RuntimeError("boom")),
            patch.object(app, "dpg", mock_dpg),
            patch("app.time") as mock_time,
        ):
            mock_time.sleep = MagicMock()
            app._auto_konnyaku_runner(duration=0)

        mock_dpg.stop_dearpygui.assert_called()


class TestAutoKonnyakuArgparse:
    """main() の --auto-konnyaku 引数処理を検証する。"""

    def test_main_with_auto_konnyaku_arg_starts_runner_thread(self):
        """
        main(--auto-konnyaku=N) で _auto_konnyaku_runner を target にしたスレッドが
        起動されること。

        Thread.__init__ をモニタリングして捕捉し、Thread.start はモックして実際には
        スレッドを起動しない（dpg クラッシュを防ぐため）。
        """
        started_threads: list[threading.Thread] = []

        real_thread_init = threading.Thread.__init__

        def capturing_thread_init(self_t, *args, **kwargs):
            real_thread_init(self_t, *args, **kwargs)
            # AutoKonnyakuRunner スレッドだけ捕捉
            if getattr(self_t, "name", "") == "AutoKonnyakuRunner":
                started_threads.append(self_t)
                # start() をノーオペレーションに差し替えてスレッドを実際には起動しない
                self_t.start = MagicMock()

        mock_dpg = MagicMock()
        # is_dearpygui_running() を False にして即終了させる
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
            patch("sys.argv", ["app.py", "--auto-konnyaku=5"]),
            patch.object(threading.Thread, "__init__", capturing_thread_init),
        ):
            app.main()

        assert len(started_threads) == 1, (
            f"AutoKonnyakuRunner スレッドが起動されていない: {started_threads}"
        )
        assert started_threads[0].daemon is True, (
            "AutoKonnyakuRunner スレッドが daemon=True でない"
        )

    def test_main_without_auto_konnyaku_no_runner_thread(self):
        """
        --auto-konnyaku 引数なしで main() を呼ぶと AutoKonnyakuRunner スレッドが起動しないこと。
        """
        started_threads: list[threading.Thread] = []

        real_thread_init = threading.Thread.__init__

        def capturing_thread_init(self_t, *args, **kwargs):
            real_thread_init(self_t, *args, **kwargs)
            if getattr(self_t, "name", "") == "AutoKonnyakuRunner":
                started_threads.append(self_t)

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
            patch("sys.argv", ["app.py"]),
            patch.object(threading.Thread, "__init__", capturing_thread_init),
        ):
            app.main()

        assert len(started_threads) == 0, (
            f"引数なし時に AutoKonnyakuRunner スレッドが起動してしまった: {started_threads}"
        )


# ---------------------------------------------------------------------------
# Issue #43: 系統別 ON/OFF トグル — GUI テスト
# ---------------------------------------------------------------------------

def _make_widget_values_with_enable(
    route_a_device_label: str,
    route_b_device_label: str,
    route_a_enabled: bool = True,
    route_b_enabled: bool = True,
) -> dict:
    """ON/OFF チェックボックスを含むウィジェット値辞書を組み立てる。"""
    base = _make_widget_values(route_a_device_label, route_b_device_label)
    base[app.TAG_ROUTE_A_ENABLE] = route_a_enabled
    base[app.TAG_ROUTE_B_ENABLE] = route_b_enabled
    return base


class TestRouteToggle:
    """系統別 ON/OFF トグルの GUI 動作テスト（Issue #43）。"""

    def test_start_with_both_routes_disabled_shows_error(self):
        """両系統 OFF で開始ボタン押下時、ステータスにエラー表示が出ること。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values_with_enable(
            route_a_label, route_b_label,
            route_a_enabled=False, route_b_enabled=False,
        )
        set_value_calls: dict[str, str] = {}
        mock_dpg = _make_dpg_mock(widget_values)
        mock_dpg.set_value.side_effect = lambda tag, val: set_value_calls.update({tag: val})
        mock_mcs = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            app._on_konnyaku_start_stop_click()

        # MultiCaptionSystem は生成されないこと
        mock_mcs.assert_not_called()
        # ステータスにエラーが表示されること
        assert app.TAG_STATUS_STATE in set_value_calls, (
            "両系統 OFF 時に TAG_STATUS_STATE に set_value が呼ばれていない"
        )

    def test_start_with_only_route_a_enabled_passes_route_b_none(self):
        """route_a のみ ON のとき MultiCaptionSystem に route_b=None が渡されること。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values_with_enable(
            route_a_label, route_b_label,
            route_a_enabled=True, route_b_enabled=False,
        )
        mock_dpg = _make_dpg_mock(widget_values)
        mock_mcs = MagicMock()
        mock_mcs.return_value = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            app._on_konnyaku_start_stop_click()

        mock_mcs.assert_called_once()
        call_kwargs = mock_mcs.call_args.kwargs
        assert call_kwargs["route_a"] is not None, "route_a が None になっている"
        assert call_kwargs["route_b"] is None, "route_b が None でない"

    def test_start_with_only_route_b_enabled_passes_route_a_none(self):
        """route_b のみ ON のとき MultiCaptionSystem に route_a=None が渡されること。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values_with_enable(
            route_a_label, route_b_label,
            route_a_enabled=False, route_b_enabled=True,
        )
        mock_dpg = _make_dpg_mock(widget_values)
        mock_mcs = MagicMock()
        mock_mcs.return_value = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            app._on_konnyaku_start_stop_click()

        mock_mcs.assert_called_once()
        call_kwargs = mock_mcs.call_args.kwargs
        assert call_kwargs["route_a"] is None, "route_a が None でない"
        assert call_kwargs["route_b"] is not None, "route_b が None になっている"

    def test_start_with_both_enabled_passes_both_configs(self):
        """両系統 ON のとき MultiCaptionSystem に route_a・route_b 両方が渡されること（既存動作維持）。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values_with_enable(
            route_a_label, route_b_label,
            route_a_enabled=True, route_b_enabled=True,
        )
        mock_dpg = _make_dpg_mock(widget_values)
        mock_mcs = MagicMock()
        mock_mcs.return_value = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", mock_mcs),
        ):
            app._on_konnyaku_start_stop_click()

        mock_mcs.assert_called_once()
        call_kwargs = mock_mcs.call_args.kwargs
        assert call_kwargs["route_a"] is not None, "route_a が None になっている（両方 ON のはず）"
        assert call_kwargs["route_b"] is not None, "route_b が None になっている（両方 ON のはず）"


# ---------------------------------------------------------------------------
# Issue #51: 出力デバイス・ON/OFF コールバック RT 反映テスト
# ---------------------------------------------------------------------------

class TestOutputDeviceRTReflect:
    """出力デバイスコンボと ON/OFF チェックボックスが稼働中に即反映されること（Issue #51）。"""

    def _find_callback_in_dpg_call(self, source: str, tag_name: str, callback_name: str) -> bool:
        """_build_gui ソース中で tag_name が登場する dpg.add_* ブロック内に
        callback=callback_name が含まれるか調べるヘルパー。

        tag=TAG_XXX の行を見つけ、その前後で括弧が閉じるまでの範囲を走査する。
        """
        lines = source.splitlines()
        for idx, line in enumerate(lines):
            if tag_name not in line:
                continue
            # tag= が含まれる行を起点に、前に遡って add_* ( の開始行を見つける
            start_idx = idx
            for back in range(idx, max(0, idx - 5), -1):
                if "dpg.add_" in lines[back]:
                    start_idx = back
                    break
            # start_idx から括弧が閉じるまでを走査
            depth = 0
            for j in range(start_idx, min(len(lines), start_idx + 20)):
                stripped = lines[j].strip()
                depth += stripped.count("(") - stripped.count(")")
                if callback_name in stripped:
                    return True
                if j > start_idx and depth <= 0:
                    break
        return False

    def test_route_a_output_device_combo_has_callback(self):
        """_build_gui 内の TAG_ROUTE_A_OUTPUT_DEVICE_COMBO の add_combo に callback が設定されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        found = self._find_callback_in_dpg_call(
            source, "TAG_ROUTE_A_OUTPUT_DEVICE_COMBO", "_on_route_a_output_device_change"
        )
        assert found, (
            "TAG_ROUTE_A_OUTPUT_DEVICE_COMBO の add_combo に "
            "callback=_on_route_a_output_device_change が設定されていない"
        )

    def test_route_a_output_enable_checkbox_has_callback(self):
        """_build_gui 内の TAG_ROUTE_A_OUTPUT_ENABLE の add_checkbox に callback が設定されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        found = self._find_callback_in_dpg_call(
            source, "TAG_ROUTE_A_OUTPUT_ENABLE", "_on_route_a_output_enable_change"
        )
        assert found, (
            "TAG_ROUTE_A_OUTPUT_ENABLE の add_checkbox に "
            "callback=_on_route_a_output_enable_change が設定されていない"
        )

    def test_on_route_a_output_device_change_calls_set_output_device(self):
        """_on_route_a_output_device_change が稼働中に route_a_system.set_output_device を呼ぶこと。"""
        mock_system = MagicMock()
        mock_system.route_a_system = MagicMock()
        mock_system.route_a_system.set_output_device = MagicMock()

        fake_out_devices = [
            {"index": 2, "name": "Fake Speaker", "isLoopback": False},
        ]

        with (
            patch.object(app, "_konnyaku_system", mock_system),
            patch("app.list_audio_devices", return_value=fake_out_devices),
            patch("app.find_device_by_name", return_value=fake_out_devices[0]),
        ):
            app._on_route_a_output_device_change(
                sender=None, app_data="Fake Speaker", user_data=None
            )

        mock_system.route_a_system.set_output_device.assert_called_once_with(2)

    def test_on_route_a_output_device_change_none_label_stops_stream(self):
        """_on_route_a_output_device_change に空ラベルを渡すと set_output_device(None) が呼ばれること。"""
        mock_system = MagicMock()
        mock_system.route_a_system = MagicMock()
        mock_system.route_a_system.set_output_device = MagicMock()

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_a_output_device_change(
                sender=None, app_data="(なし)", user_data=None
            )

        mock_system.route_a_system.set_output_device.assert_called_once_with(None)

    def test_on_route_b_output_device_change_calls_set_output_device(self):
        """_on_route_b_output_device_change が稼働中に route_b_system.set_output_device を呼ぶこと。"""
        mock_system = MagicMock()
        mock_system.route_b_system = MagicMock()
        mock_system.route_b_system.set_output_device = MagicMock()

        fake_out_devices = [
            {"index": 4, "name": "CABLE Input", "isLoopback": False},
        ]

        with (
            patch.object(app, "_konnyaku_system", mock_system),
            patch("app.list_audio_devices", return_value=fake_out_devices),
            patch("app.find_device_by_name", return_value=fake_out_devices[0]),
        ):
            app._on_route_b_output_device_change(
                sender=None, app_data="CABLE Input", user_data=None
            )

        mock_system.route_b_system.set_output_device.assert_called_once_with(4)

    def test_on_route_a_output_enable_change_enables_stream(self):
        """_on_route_a_output_enable_change(True) がデバイスを取得して set_output_device を呼ぶこと。"""
        mock_system = MagicMock()
        mock_system.route_a_system = MagicMock()
        mock_system.route_a_system.set_output_device = MagicMock()

        fake_out_devices = [{"index": 2, "name": "Fake Speaker", "isLoopback": False}]
        mock_dpg = _make_dpg_mock({
            app.TAG_ROUTE_A_OUTPUT_DEVICE_COMBO: "Fake Speaker",
        })

        with (
            patch.object(app, "_konnyaku_system", mock_system),
            patch.object(app, "dpg", mock_dpg),
            patch("app.list_audio_devices", return_value=fake_out_devices),
            patch("app.find_device_by_name", return_value=fake_out_devices[0]),
        ):
            app._on_route_a_output_enable_change(
                sender=None, app_data=True, user_data=None
            )

        mock_system.route_a_system.set_output_device.assert_called_once_with(2)

    def test_on_route_a_output_enable_change_disables_stream(self):
        """_on_route_a_output_enable_change(False) が set_output_device(None) を呼ぶこと。"""
        mock_system = MagicMock()
        mock_system.route_a_system = MagicMock()
        mock_system.route_a_system.set_output_device = MagicMock()

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_a_output_enable_change(
                sender=None, app_data=False, user_data=None
            )

        mock_system.route_a_system.set_output_device.assert_called_once_with(None)

    def test_on_route_b_output_enable_change_disables_stream(self):
        """_on_route_b_output_enable_change(False) が set_output_device(None) を呼ぶこと。"""
        mock_system = MagicMock()
        mock_system.route_b_system = MagicMock()
        mock_system.route_b_system.set_output_device = MagicMock()

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_b_output_enable_change(
                sender=None, app_data=False, user_data=None
            )

        mock_system.route_b_system.set_output_device.assert_called_once_with(None)

    def test_on_route_a_output_device_change_no_system_does_not_crash(self):
        """_konnyaku_system が None の場合にコールバックが例外なく終了すること。"""
        with patch.object(app, "_konnyaku_system", None):
            # 例外が出なければ OK
            app._on_route_a_output_device_change(
                sender=None, app_data="Some Device", user_data=None
            )
