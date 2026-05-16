"""
tests/test_w_cost_2_source_transcript.py

W-COST-2: 原文表示 OFF 時の Whisper コスト削減テスト（issue #81）

テスト対象:
1. RealtimeTranslator: request_source_transcript=False/True で
   session.update ペイロードの audio.input.transcription の有無を検証
2. CaptionSystem: request_source_transcript 属性が RealtimeTranslator に正しく伝播する
3. RouteConfig / MultiCaptionSystem: 設定値が CaptionSystem に正しく伝播する
4. app.py: source_transcript_enabled の settings load/save が正しく動作する
"""

import asyncio
import json
import socket
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

from realtime_translator import RealtimeTranslator
from main import CaptionSystem, MultiCaptionSystem, RouteConfig, RouteState
import app


# ---------------------------------------------------------------------------
# ヘルパー: モック WebSocket サーバー
# ---------------------------------------------------------------------------

def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _start_mock_server_in_thread(handler, port=None):
    if port is None:
        port = _get_free_port()
    import websockets

    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()

    async def _serve():
        async with websockets.serve(handler, "localhost", port):
            await stop_event.wait()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.2)
    return loop, stop_event, t, port


def _stop_mock_server(loop, stop_event):
    loop.call_soon_threadsafe(stop_event.set)
    time.sleep(0.1)


# ---------------------------------------------------------------------------
# 1. RealtimeTranslator: session.update ペイロードの条件分岐
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorSourceTranscriptFlag:
    """request_source_transcript フラグによる session.update ペイロードの変化を検証する。"""

    def _capture_session_update(self, request_source_transcript: bool) -> dict:
        """モックサーバーに接続して session.update ペイロードをキャプチャして返す。"""
        captured = {}
        received_event = threading.Event()

        async def mock_handler(websocket):
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=5)
                captured["payload"] = json.loads(raw)
                received_event.set()
                # セッションを維持（クライアントが切断するまで）
                try:
                    await websocket.wait_closed()
                except Exception:
                    pass
            except asyncio.TimeoutError:
                received_event.set()

        server_loop, stop_event, _, port = _start_mock_server_in_thread(mock_handler)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-w-cost-2-0000000000",
                target_language_code="ja",
                on_error=lambda msg: None,
                reconnect_max_attempts=0,
                request_source_transcript=request_source_transcript,
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

    def test_session_update_no_audio_input_when_true_ga(self):
        """GA 版 (2026-05-12 以降): request_source_transcript=True でも
        session.update に audio.input は含まれないこと。

        GA 版では transcript イベントは自動発行されるため audio.input.transcription の
        明示指定は不要（仕様外として無視または拒否される）。
        request_source_transcript は Beta 時代の互換性のため属性として残置されるが効果なし。
        """
        payload = self._capture_session_update(request_source_transcript=True)

        assert payload.get("type") == "session.update", f"expected session.update, got: {payload}"
        audio = payload.get("session", {}).get("audio", {})
        assert "input" not in audio, (
            f"GA 版では request_source_transcript=True でも audio.input は送らないこと。audio={audio}"
        )

    def test_session_update_excludes_transcription_when_false(self):
        """request_source_transcript=False のとき session.update の
        audio.input.transcription が除外されること（GA 版では audio.input 自体なし）。"""
        payload = self._capture_session_update(request_source_transcript=False)

        assert payload.get("type") == "session.update", f"expected session.update, got: {payload}"
        audio = payload.get("session", {}).get("audio", {})
        # GA 版: audio.input 自体が存在しないこと
        assert "input" not in audio, (
            f"GA 版では request_source_transcript=False でも audio.input は除外されること。"
            f"audio={audio}"
        )

    def test_default_is_true(self):
        """request_source_transcript のデフォルトは True（後方互換）。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-w-cost-2-default",
            target_language_code="ja",
        )
        assert translator._request_source_transcript is True

    def test_false_is_stored(self):
        """request_source_transcript=False が内部属性に格納されること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-w-cost-2-stored",
            target_language_code="ja",
            request_source_transcript=False,
        )
        assert translator._request_source_transcript is False


# ---------------------------------------------------------------------------
# 2. CaptionSystem: request_source_transcript が RealtimeTranslator に伝播
# ---------------------------------------------------------------------------

class TestCaptionSystemSourceTranscriptPropagation:
    """CaptionSystem の _request_source_transcript が RealtimeTranslator に正しく渡される。"""

    def _make_caption_system(self, request_source_transcript: bool) -> CaptionSystem:
        config = {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-caption-w-cost-2"},
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
            request_source_transcript=request_source_transcript,
        )
        return cs

    def test_caption_system_stores_request_source_transcript_true(self):
        """CaptionSystem が request_source_transcript=True を保持すること。"""
        cs = self._make_caption_system(True)
        assert cs._request_source_transcript is True

    def test_caption_system_stores_request_source_transcript_false(self):
        """CaptionSystem が request_source_transcript=False を保持すること。"""
        cs = self._make_caption_system(False)
        assert cs._request_source_transcript is False

    def test_caption_system_default_is_true(self):
        """CaptionSystem の request_source_transcript デフォルトは True（後方互換）。"""
        config = {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-caption-default"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
            },
            "output": {"log_dir": "."},
            "stt": {"model": "tiny"},
        }
        device_info = {"name": "FakeMic", "index": 0, "samplerate": 16000}
        cs = CaptionSystem(config=config, device_info=device_info, model_name="tiny")
        assert cs._request_source_transcript is True

    def test_create_realtime_translator_passes_flag_false(self):
        """_create_realtime_translator が _request_source_transcript=False を
        RealtimeTranslator に渡すこと。

        _create_realtime_translator 内で from realtime_translator import RealtimeTranslator を
        local import しているため、realtime_translator.RealtimeTranslator をモックする。
        """
        cs = self._make_caption_system(False)

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor") as MockCM:
            MockRT.return_value = MagicMock()
            MockCM.return_value = MagicMock()
            cs._create_realtime_translator()

        # RealtimeTranslator に request_source_transcript=False が渡されること
        assert MockRT.called, "RealtimeTranslator が呼び出されること"
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_source_transcript") is False, (
            f"RealtimeTranslator に request_source_transcript=False が渡されること。"
            f"kwargs={kwargs}"
        )

    def test_create_realtime_translator_passes_flag_true(self):
        """_create_realtime_translator が _request_source_transcript=True を
        RealtimeTranslator に渡すこと。"""
        cs = self._make_caption_system(True)

        with patch("realtime_translator.RealtimeTranslator") as MockRT, \
             patch("cost_monitor.CostMonitor") as MockCM:
            MockRT.return_value = MagicMock()
            MockCM.return_value = MagicMock()
            cs._create_realtime_translator()

        assert MockRT.called
        _, kwargs = MockRT.call_args
        assert kwargs.get("request_source_transcript") is True, (
            f"request_source_transcript=True が渡されること。kwargs={kwargs}"
        )


# ---------------------------------------------------------------------------
# 3. RouteConfig / MultiCaptionSystem: 設定値が CaptionSystem に伝播
# ---------------------------------------------------------------------------

class TestRouteConfigSourceTranscript:
    """RouteConfig に request_source_transcript フィールドが追加されていることを検証。"""

    def test_route_config_has_source_transcript_field(self):
        """RouteConfig に request_source_transcript フィールドがあること。"""
        rc = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            request_source_transcript=False,
        )
        assert rc.request_source_transcript is False

    def test_route_config_default_source_transcript_is_true(self):
        """RouteConfig の request_source_transcript デフォルトは True。"""
        rc = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMic", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
        )
        assert rc.request_source_transcript is True


class TestMultiCaptionSystemSourceTranscriptPropagation:
    """MultiCaptionSystem が RouteConfig.request_source_transcript を CaptionSystem に渡す。"""

    def _make_config(self):
        return {
            "translation": {"translation_model": "openai-realtime"},
            "openai": {"api_key": "sk-test-fake-multi-w-cost-2"},
            "openai_realtime": {
                "target_language_code": "ja",
                "model": "gpt-realtime-translate",
            },
            "output": {"log_dir": "."},
            "stt": {"model": "tiny"},
        }

    def test_multi_caption_system_propagates_false_to_route_a(self):
        """route_a.request_source_transcript=False が route_a CaptionSystem に伝播すること。"""
        import pyaudio

        route_a = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMicA", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            request_source_transcript=False,
        )

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=route_a,
                route_b=None,
            )

        assert mcs.route_a_system is not None
        assert mcs.route_a_system._request_source_transcript is False

    def test_multi_caption_system_propagates_true_to_route_b(self):
        """route_b.request_source_transcript=True が route_b CaptionSystem に伝播すること。"""
        route_b = RouteConfig(
            route_id="b",
            input_device_info={"name": "FakeMicB", "index": 1},
            target_language_code="en",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            request_source_transcript=True,
        )

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=None,
                route_b=route_b,
            )

        assert mcs.route_b_system is not None
        assert mcs.route_b_system._request_source_transcript is True

    def test_multi_caption_system_default_propagation(self):
        """RouteConfig デフォルト（True）が CaptionSystem に伝播すること。"""
        route_a = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMicA", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
            # request_source_transcript 省略 -> デフォルト True
        )

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=route_a,
                route_b=None,
            )

        assert mcs.route_a_system._request_source_transcript is True


# ---------------------------------------------------------------------------
# 4. app.py: settings load/save に source_transcript_enabled が含まれる
# ---------------------------------------------------------------------------

class TestAppSettingsSourceTranscript:
    """app.py の settings.json に source_transcript_enabled が正しく保存・読み込みされる。"""

    def setup_method(self):
        """各テスト前に app モジュールのグローバル状態をリセット。"""
        self._old_system = app._konnyaku_system
        self._old_running = app._konnyaku_running

    def teardown_method(self):
        """各テスト後に元のグローバル状態を復元。"""
        app._konnyaku_system = self._old_system
        app._konnyaku_running = self._old_running

    def test_save_settings_includes_source_transcript_route_a(self):
        """_save_settings が route_a.source_transcript_enabled を保存すること。"""
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
                app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE: False,
            }.get(tag, "")

            app._save_settings()

        route_a = saved_data.get("route_a", {})
        assert "source_transcript_enabled" in route_a, (
            f"route_a に source_transcript_enabled が保存されること。route_a={route_a}"
        )

    def test_save_settings_includes_source_transcript_route_b(self):
        """_save_settings が route_b.source_transcript_enabled を保存すること。"""
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
                app.TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE: False,
            }.get(tag, "")

            app._save_settings()

        route_b = saved_data.get("route_b", {})
        assert "source_transcript_enabled" in route_b, (
            f"route_b に source_transcript_enabled が保存されること。route_b={route_b}"
        )

    def test_create_konnyaku_system_passes_source_transcript_from_saved(self):
        """_create_konnyaku_system が saved settings の source_transcript_enabled を
        RouteConfig に反映すること。"""
        fake_devices = [
            {"name": "Mic1", "index": 0, "samplerate": 16000},
        ]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": False,  # OFF に設定
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,   # ON に設定
            },
        }

        app._konnyaku_system = None
        app._konnyaku_running = False

        created_route_a_kwargs = {}
        created_route_b_kwargs = {}
        OriginalMultiCaptionSystem = MultiCaptionSystem

        def capturing_mcs(config, route_a, route_b, **kwargs):
            if route_a is not None:
                created_route_a_kwargs["request_source_transcript"] = route_a.request_source_transcript
            if route_b is not None:
                created_route_b_kwargs["request_source_transcript"] = route_b.request_source_transcript
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

        assert created_route_a_kwargs.get("request_source_transcript") is False, (
            f"route_a の request_source_transcript が False であること。got={created_route_a_kwargs}"
        )
        assert created_route_b_kwargs.get("request_source_transcript") is True, (
            f"route_b の request_source_transcript が True であること。got={created_route_b_kwargs}"
        )

    def test_create_konnyaku_system_defaults_source_transcript_true(self):
        """saved settings に source_transcript_enabled がない場合、デフォルト True が使われること。"""
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
                created_route_a_kwargs["request_source_transcript"] = route_a.request_source_transcript
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

        assert created_route_a_kwargs.get("request_source_transcript") is True, (
            f"デフォルト True が使われること。got={created_route_a_kwargs}"
        )
