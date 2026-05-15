"""
tests/test_w_cost_3_vad.py

W-COST-3: VAD 設定による無音区間 input トークン削減テスト（issue #81）

テスト対象:
1. RealtimeTranslator: vad_enabled=True/False で session.update の
   audio.input.turn_detection の有無と値を検証
2. RealtimeTranslator: request_audio_output x vad_enabled の 2x2 マトリクステスト
3. CaptionSystem: vad_enabled が RealtimeTranslator に正しく伝播する
4. RouteConfig / MultiCaptionSystem: VAD フィールドが CaptionSystem に伝播する
5. app.py: vad_enabled の settings load/save

NOTE: API 受入確認（TBD-3-1）は実機テストのため本ファイルでは除外する。
"""

import asyncio
import json
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from realtime_translator import RealtimeTranslator
from main import CaptionSystem, MultiCaptionSystem, RouteConfig
import app


# ---------------------------------------------------------------------------
# ヘルパー: モック WebSocket サーバー
# ---------------------------------------------------------------------------

def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _start_mock_server_in_thread(handler, port=None):
    """モックWSサーバーを別スレッドで起動し、(loop, stop_event, thread, port) を返す。"""
    if port is None:
        port = _get_free_port()
    import websockets

    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()
    ready_event = threading.Event()

    async def _serve():
        async with websockets.serve(handler, "localhost", port):
            ready_event.set()
            await stop_event.wait()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    ready_event.wait(timeout=5.0)
    return loop, stop_event, t, port


def _stop_mock_server(loop, stop_event):
    loop.call_soon_threadsafe(stop_event.set)
    time.sleep(0.1)


def _capture_session_update(
    vad_enabled: bool,
    request_source_transcript: bool = True,
    request_audio_output: bool = False,
    vad_threshold: float = 0.5,
    vad_prefix_padding_ms: int = 300,
    vad_silence_duration_ms: int = 500,
) -> dict:
    """モックサーバーに接続して session.update ペイロードをキャプチャして返す。"""
    captured = {}
    received_event = threading.Event()

    async def mock_handler(websocket):
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=5)
            captured["payload"] = json.loads(raw)
            received_event.set()
            try:
                await websocket.wait_closed()
            except Exception:
                pass
        except asyncio.TimeoutError:
            received_event.set()

    server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

    try:
        translator = RealtimeTranslator(
            api_key="sk-test-fake-vad001",
            target_language_code="ja",
            on_error=lambda msg: None,
            reconnect_max_attempts=0,
            request_source_transcript=request_source_transcript,
            request_audio_output=request_audio_output,
            vad_enabled=vad_enabled,
            vad_threshold=vad_threshold,
            vad_prefix_padding_ms=vad_prefix_padding_ms,
            vad_silence_duration_ms=vad_silence_duration_ms,
        )
        translator._ws_url = f"ws://localhost:{port}"

        client_loop = asyncio.new_event_loop()
        translator.connect(client_loop)
        received_event.wait(timeout=5.0)
        translator.disconnect()
        client_loop.call_soon_threadsafe(client_loop.stop)
    finally:
        _stop_mock_server(server_loop, stop_event)

    return captured.get("payload", {})


# ---------------------------------------------------------------------------
# 1. RealtimeTranslator: コンストラクタのデフォルト値と属性
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorVadDefaults:
    """RealtimeTranslator の VAD パラメータデフォルト値と属性を検証する。"""

    def test_vad_enabled_default_is_false(self):
        """vad_enabled のデフォルトは False（後方互換・安全側）。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-vad-default",
            target_language_code="ja",
        )
        assert translator._vad_enabled is False

    def test_vad_threshold_default(self):
        """vad_threshold のデフォルトは 0.5。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-vad-threshold",
            target_language_code="ja",
        )
        assert translator._vad_threshold == 0.5

    def test_vad_prefix_padding_ms_default(self):
        """vad_prefix_padding_ms のデフォルトは 300。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-vad-prefix",
            target_language_code="ja",
        )
        assert translator._vad_prefix_padding_ms == 300

    def test_vad_silence_duration_ms_default(self):
        """vad_silence_duration_ms のデフォルトは 500。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-vad-silence",
            target_language_code="ja",
        )
        assert translator._vad_silence_duration_ms == 500

    def test_vad_enabled_true_is_stored(self):
        """vad_enabled=True が内部属性に格納されること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-vad-stored",
            target_language_code="ja",
            vad_enabled=True,
        )
        assert translator._vad_enabled is True

    def test_vad_params_custom_values_stored(self):
        """カスタム VAD パラメータが内部属性に格納されること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-vad-custom",
            target_language_code="ja",
            vad_enabled=True,
            vad_threshold=0.7,
            vad_prefix_padding_ms=200,
            vad_silence_duration_ms=800,
        )
        assert translator._vad_threshold == 0.7
        assert translator._vad_prefix_padding_ms == 200
        assert translator._vad_silence_duration_ms == 800


# ---------------------------------------------------------------------------
# 2. RealtimeTranslator: session.update の turn_detection 有無
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorVadSessionUpdate:
    """vad_enabled フラグによる session.update ペイロードの変化を検証する。"""

    def test_session_update_excludes_turn_detection_when_vad_disabled(self):
        """vad_enabled=False（デフォルト）のとき session.update に turn_detection が含まれないこと。"""
        payload = _capture_session_update(vad_enabled=False)

        assert payload.get("type") == "session.update", f"expected session.update, got: {payload}"
        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        assert "turn_detection" not in audio_input, (
            f"vad_enabled=False のとき turn_detection は含まれないこと。audio.input={audio_input}"
        )

    def test_session_update_includes_turn_detection_when_vad_enabled(self):
        """vad_enabled=True のとき session.update の audio.input に turn_detection が含まれること。"""
        payload = _capture_session_update(vad_enabled=True)

        assert payload.get("type") == "session.update", f"expected session.update, got: {payload}"
        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        assert "turn_detection" in audio_input, (
            f"vad_enabled=True のとき turn_detection が存在すること。audio.input={audio_input}"
        )

    def test_session_update_turn_detection_type_is_server_vad(self):
        """turn_detection.type が 'server_vad' であること。"""
        payload = _capture_session_update(vad_enabled=True)

        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        td = audio_input.get("turn_detection", {})
        assert td.get("type") == "server_vad", (
            f"turn_detection.type は 'server_vad' であること。got={td}"
        )

    def test_session_update_turn_detection_threshold(self):
        """turn_detection.threshold が指定値通りに送信されること。"""
        payload = _capture_session_update(vad_enabled=True, vad_threshold=0.7)

        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        td = audio_input.get("turn_detection", {})
        assert td.get("threshold") == pytest.approx(0.7), (
            f"turn_detection.threshold は 0.7 であること。got={td}"
        )

    def test_session_update_turn_detection_prefix_padding_ms(self):
        """turn_detection.prefix_padding_ms が指定値通りに送信されること。"""
        payload = _capture_session_update(vad_enabled=True, vad_prefix_padding_ms=200)

        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        td = audio_input.get("turn_detection", {})
        assert td.get("prefix_padding_ms") == 200, (
            f"turn_detection.prefix_padding_ms は 200 であること。got={td}"
        )

    def test_session_update_turn_detection_silence_duration_ms(self):
        """turn_detection.silence_duration_ms が指定値通りに送信されること。"""
        payload = _capture_session_update(vad_enabled=True, vad_silence_duration_ms=800)

        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        td = audio_input.get("turn_detection", {})
        assert td.get("silence_duration_ms") == 800, (
            f"turn_detection.silence_duration_ms は 800 であること。got={td}"
        )

    def test_session_update_default_vad_params_in_turn_detection(self):
        """デフォルトパラメータ（threshold=0.5, prefix_padding_ms=300, silence_duration_ms=500）
        が turn_detection に含まれること。"""
        payload = _capture_session_update(vad_enabled=True)

        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        td = audio_input.get("turn_detection", {})
        assert td.get("threshold") == pytest.approx(0.5), f"threshold default は 0.5: {td}"
        assert td.get("prefix_padding_ms") == 300, f"prefix_padding_ms default は 300: {td}"
        assert td.get("silence_duration_ms") == 500, f"silence_duration_ms default は 500: {td}"

    def test_transcription_still_present_when_vad_enabled(self):
        """vad_enabled=True でも audio.input.transcription は含まれること。"""
        payload = _capture_session_update(vad_enabled=True, request_source_transcript=True)

        audio_input = payload.get("session", {}).get("audio", {}).get("input", {})
        assert "transcription" in audio_input, (
            f"vad_enabled=True でも transcription は含まれること。audio.input={audio_input}"
        )
        assert audio_input["transcription"].get("model") == "gpt-realtime-whisper", (
            f"transcription.model は gpt-realtime-whisper であること。got={audio_input['transcription']}"
        )

    def test_no_turn_detection_when_source_transcript_disabled(self):
        """request_source_transcript=False のとき VAD 設定に関係なく audio.input 自体が除外されること。

        W-COST-2 の既存挙動維持（audio.input キーなし = turn_detection も含まれない）。
        """
        payload = _capture_session_update(
            vad_enabled=True,
            request_source_transcript=False,
        )

        audio = payload.get("session", {}).get("audio", {})
        # request_source_transcript=False のとき audio.input キー自体が存在しない
        assert "input" not in audio, (
            f"request_source_transcript=False のとき audio.input は除外されること。audio={audio}"
        )


# ---------------------------------------------------------------------------
# 3. RealtimeTranslator: request_audio_output x vad_enabled の 2x2 マトリクス
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorVadMatrixTest:
    """request_audio_output x vad_enabled の 4 ケースで session.update の構造を検証する。"""

    @pytest.mark.parametrize("request_audio_output,vad_enabled,expect_output,expect_td", [
        (False, False, False, False),
        (False, True,  False, True),
        (True,  False, True,  False),
        (True,  True,  True,  True),
    ])
    def test_2x2_matrix(self, request_audio_output, vad_enabled, expect_output, expect_td):
        """request_audio_output x vad_enabled の 2x2 組み合わせで session.update の構造を確認する。"""
        payload = _capture_session_update(
            vad_enabled=vad_enabled,
            request_audio_output=request_audio_output,
        )

        audio = payload.get("session", {}).get("audio", {})
        audio_input = audio.get("input", {})

        # audio.output の有無
        if expect_output:
            assert "output" in audio, (
                f"request_audio_output=True のとき audio.output が存在すること。audio={audio}"
            )
        else:
            assert "output" not in audio, (
                f"request_audio_output=False のとき audio.output は除外されること。audio={audio}"
            )

        # audio.input.turn_detection の有無
        if expect_td:
            assert "turn_detection" in audio_input, (
                f"vad_enabled=True のとき turn_detection が存在すること。"
                f"params: request_audio_output={request_audio_output}, vad_enabled={vad_enabled}. "
                f"audio.input={audio_input}"
            )
        else:
            assert "turn_detection" not in audio_input, (
                f"vad_enabled=False のとき turn_detection は含まれないこと。"
                f"params: request_audio_output={request_audio_output}, vad_enabled={vad_enabled}. "
                f"audio.input={audio_input}"
            )


# ---------------------------------------------------------------------------
# 4. CaptionSystem: vad_enabled が RealtimeTranslator に伝播
# ---------------------------------------------------------------------------

class TestCaptionSystemVadPropagation:
    """CaptionSystem の VAD パラメータが RealtimeTranslator に正しく渡される。"""

    def _make_caption_system(
        self,
        vad_enabled: bool = False,
        vad_threshold: float = 0.5,
        vad_prefix_padding_ms: int = 300,
        vad_silence_duration_ms: int = 500,
    ) -> CaptionSystem:
        config = {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-caption-vad001"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
            },
            "output": {"log_dir": "."},
            "stt": {"model": "tiny"},
        }
        device_info = {"name": "FakeMic", "index": 0, "samplerate": 16000}
        cs = CaptionSystem(
            config=config,
            device_info=device_info,
            model_name="tiny",
            vad_enabled=vad_enabled,
            vad_threshold=vad_threshold,
            vad_prefix_padding_ms=vad_prefix_padding_ms,
            vad_silence_duration_ms=vad_silence_duration_ms,
        )
        return cs

    def test_caption_system_stores_vad_enabled_false(self):
        """CaptionSystem が vad_enabled=False を保持すること。"""
        cs = self._make_caption_system(vad_enabled=False)
        assert cs._vad_enabled is False

    def test_caption_system_stores_vad_enabled_true(self):
        """CaptionSystem が vad_enabled=True を保持すること。"""
        cs = self._make_caption_system(vad_enabled=True)
        assert cs._vad_enabled is True

    def test_caption_system_default_vad_enabled_is_false(self):
        """CaptionSystem の vad_enabled デフォルトは False（後方互換）。"""
        config = {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-caption-vad-default"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
            },
            "output": {"log_dir": "."},
            "stt": {"model": "tiny"},
        }
        device_info = {"name": "FakeMic", "index": 0, "samplerate": 16000}
        cs = CaptionSystem(config=config, device_info=device_info, model_name="tiny")
        assert cs._vad_enabled is False

    def test_create_realtime_translator_passes_vad_enabled_false(self):
        """_create_realtime_translator が _vad_enabled=False を RealtimeTranslator に渡すこと。"""
        cs = self._make_caption_system(vad_enabled=False)

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor") as MockCM:
            MockRT.return_value = MagicMock()
            MockCM.return_value = MagicMock()
            cs._create_realtime_translator()

        assert MockRT.called, "RealtimeTranslator が呼び出されること"
        _, kwargs = MockRT.call_args
        assert kwargs.get("vad_enabled") is False, (
            f"RealtimeTranslator に vad_enabled=False が渡されること。kwargs={kwargs}"
        )

    def test_create_realtime_translator_passes_vad_enabled_true(self):
        """_create_realtime_translator が _vad_enabled=True を RealtimeTranslator に渡すこと。"""
        cs = self._make_caption_system(vad_enabled=True)

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor") as MockCM:
            MockRT.return_value = MagicMock()
            MockCM.return_value = MagicMock()
            cs._create_realtime_translator()

        assert MockRT.called
        _, kwargs = MockRT.call_args
        assert kwargs.get("vad_enabled") is True, (
            f"vad_enabled=True が渡されること。kwargs={kwargs}"
        )

    def test_create_realtime_translator_passes_vad_params(self):
        """_create_realtime_translator が VAD パラメータを RealtimeTranslator に渡すこと。"""
        cs = self._make_caption_system(
            vad_enabled=True,
            vad_threshold=0.7,
            vad_prefix_padding_ms=200,
            vad_silence_duration_ms=800,
        )

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor") as MockCM:
            MockRT.return_value = MagicMock()
            MockCM.return_value = MagicMock()
            cs._create_realtime_translator()

        assert MockRT.called
        _, kwargs = MockRT.call_args
        assert kwargs.get("vad_threshold") == pytest.approx(0.7), f"vad_threshold: {kwargs}"
        assert kwargs.get("vad_prefix_padding_ms") == 200, f"vad_prefix_padding_ms: {kwargs}"
        assert kwargs.get("vad_silence_duration_ms") == 800, f"vad_silence_duration_ms: {kwargs}"


# ---------------------------------------------------------------------------
# 5. RouteConfig: VAD フィールドの確認
# ---------------------------------------------------------------------------

class TestRouteConfigVad:
    """RouteConfig に VAD フィールドが追加されていることを検証。"""

    def test_route_config_has_vad_enabled_field(self):
        """RouteConfig に vad_enabled フィールドがあること。"""
        rc = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            vad_enabled=True,
        )
        assert rc.vad_enabled is True

    def test_route_config_vad_enabled_default_is_false(self):
        """RouteConfig の vad_enabled デフォルトは False（後方互換・安全側）。"""
        rc = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
        )
        assert rc.vad_enabled is False

    def test_route_config_has_vad_silence_duration_ms_field(self):
        """RouteConfig に vad_silence_duration_ms フィールドがあること。"""
        rc = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            vad_silence_duration_ms=800,
        )
        assert rc.vad_silence_duration_ms == 800

    def test_route_config_has_vad_threshold_field(self):
        """RouteConfig に vad_threshold フィールドがあること。"""
        rc = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            vad_threshold=0.7,
        )
        assert rc.vad_threshold == pytest.approx(0.7)

    def test_route_config_has_vad_prefix_padding_ms_field(self):
        """RouteConfig に vad_prefix_padding_ms フィールドがあること。"""
        rc = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            vad_prefix_padding_ms=200,
        )
        assert rc.vad_prefix_padding_ms == 200


# ---------------------------------------------------------------------------
# 6. MultiCaptionSystem: VAD フィールドが CaptionSystem に伝播
# ---------------------------------------------------------------------------

class TestMultiCaptionSystemVadPropagation:
    """MultiCaptionSystem が RouteConfig.vad_enabled を CaptionSystem に渡す。"""

    def _make_config(self):
        return {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-multi-vad001"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
            },
            "output": {"log_dir": "."},
            "stt": {"model": "tiny"},
        }

    def test_multi_caption_system_propagates_vad_enabled_true_to_route_a(self):
        """route_a.vad_enabled=True が route_a CaptionSystem に伝播すること。"""
        route_a = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMicA", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            vad_enabled=True,
        )

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=route_a,
                route_b=None,
            )

        assert mcs.route_a_system is not None
        assert mcs.route_a_system._vad_enabled is True

    def test_multi_caption_system_propagates_vad_enabled_false_to_route_b(self):
        """route_b.vad_enabled=False が route_b CaptionSystem に伝播すること。"""
        route_b = RouteConfig(
            route_id="b",
            input_device_info={"name": "FakeMicB", "index": 1},
            target_language_code="en",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            vad_enabled=False,
        )

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=None,
                route_b=route_b,
            )

        assert mcs.route_b_system is not None
        assert mcs.route_b_system._vad_enabled is False

    def test_multi_caption_system_default_vad_propagation(self):
        """RouteConfig デフォルト（vad_enabled=False）が CaptionSystem に伝播すること。"""
        route_a = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMicA", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            # vad_enabled 省略 -> デフォルト False
        )

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=route_a,
                route_b=None,
            )

        assert mcs.route_a_system._vad_enabled is False


# ---------------------------------------------------------------------------
# 7. app.py: vad_enabled の settings save/load
# ---------------------------------------------------------------------------

class TestAppSettingsVad:
    """app.py の settings.json に vad_enabled が正しく保存・読み込みされる。"""

    def setup_method(self):
        """各テスト前に app モジュールのグローバル状態をリセット。"""
        self._old_system = app._konnyaku_system
        self._old_running = app._konnyaku_running

    def teardown_method(self):
        """各テスト後に元のグローバル状態を復元。"""
        app._konnyaku_system = self._old_system
        app._konnyaku_running = self._old_running

    def test_save_settings_includes_vad_enabled_route_a(self):
        """_save_settings が route_a.vad_enabled を保存すること。"""
        saved_data = {}

        def fake_json_dump(data, f, **kwargs):
            saved_data.update(data)

        with patch("app.dpg") as mock_dpg, \
             patch("app._dpg_ready", True), \
             patch("app.json.dump", fake_json_dump), \
             patch("builtins.open", MagicMock()):
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.side_effect = lambda tag: {
                app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE: True,
                app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE: True,
            }.get(tag, "")

            app._save_settings()

        route_a = saved_data.get("route_a", {})
        assert "vad_enabled" in route_a, (
            f"route_a に vad_enabled が保存されること。route_a={route_a}"
        )

    def test_save_settings_includes_vad_enabled_route_b(self):
        """_save_settings が route_b.vad_enabled を保存すること。"""
        saved_data = {}

        def fake_json_dump(data, f, **kwargs):
            saved_data.update(data)

        with patch("app.dpg") as mock_dpg, \
             patch("app._dpg_ready", True), \
             patch("app.json.dump", fake_json_dump), \
             patch("builtins.open", MagicMock()):
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.side_effect = lambda tag: {
                app.TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE: True,
                app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE: True,
            }.get(tag, "")

            app._save_settings()

        route_b = saved_data.get("route_b", {})
        assert "vad_enabled" in route_b, (
            f"route_b に vad_enabled が保存されること。route_b={route_b}"
        )

    def test_create_konnyaku_system_passes_vad_enabled_from_saved(self):
        """_create_konnyaku_system が saved settings の vad_enabled を RouteConfig に反映すること。"""
        fake_devices = [
            {"name": "Mic1", "index": 0, "samplerate": 16000},
        ]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": True,   # ON に設定
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": False,  # OFF に設定
            },
        }

        app._konnyaku_system = None
        app._konnyaku_running = False

        created_route_a_kwargs = {}
        created_route_b_kwargs = {}

        def capturing_mcs(config, route_a, route_b, **kwargs):
            if route_a is not None:
                created_route_a_kwargs["vad_enabled"] = route_a.vad_enabled
            if route_b is not None:
                created_route_b_kwargs["vad_enabled"] = route_b.vad_enabled
            mock_mcs = MagicMock()
            mock_mcs.route_a_system = MagicMock()
            mock_mcs.route_b_system = MagicMock()
            return mock_mcs

        with patch("app._load_settings", return_value=fake_settings), \
             patch("app._devices", fake_devices), \
             patch("app.MultiCaptionSystem", side_effect=capturing_mcs), \
             patch("app.list_audio_devices", return_value=[]), \
             patch("app.find_device_by_name", return_value=None):
            app._create_konnyaku_system()

        assert created_route_a_kwargs.get("vad_enabled") is True, (
            f"route_a の vad_enabled が True であること。got={created_route_a_kwargs}"
        )
        assert created_route_b_kwargs.get("vad_enabled") is False, (
            f"route_b の vad_enabled が False であること。got={created_route_b_kwargs}"
        )

    def test_create_konnyaku_system_defaults_vad_enabled_false(self):
        """saved settings に vad_enabled がない場合、デフォルト False が使われること。"""
        fake_devices = [
            {"name": "Mic1", "index": 0, "samplerate": 16000},
        ]
        fake_settings = {
            "route_a": {"device": "Mic1", "lang": "", "output_enabled": False},
            "route_b": {"device": "Mic1", "lang": "", "output_enabled": False},
        }

        app._konnyaku_system = None
        app._konnyaku_running = False

        created_route_a_kwargs = {}

        def capturing_mcs(config, route_a, route_b, **kwargs):
            if route_a is not None:
                created_route_a_kwargs["vad_enabled"] = route_a.vad_enabled
            mock_mcs = MagicMock()
            mock_mcs.route_a_system = MagicMock()
            mock_mcs.route_b_system = MagicMock()
            return mock_mcs

        with patch("app._load_settings", return_value=fake_settings), \
             patch("app._devices", fake_devices), \
             patch("app.MultiCaptionSystem", side_effect=capturing_mcs), \
             patch("app.list_audio_devices", return_value=[]), \
             patch("app.find_device_by_name", return_value=None):
            app._create_konnyaku_system()

        assert created_route_a_kwargs.get("vad_enabled") is False, (
            f"デフォルト False が使われること。got={created_route_a_kwargs}"
        )
