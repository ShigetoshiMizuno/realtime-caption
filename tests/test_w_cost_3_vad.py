"""
tests/test_w_cost_3_vad.py

W-COST-3 / refactor/remove-vad-dead-code 後のテスト。

vad_* パラメータは削除済みのため、後方互換（TypeError にならない）を中心に確認する。
セッション更新ペイロードの動作確認（turn_detection を送らない等）は test_ga_migration.py で担う。

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
    request_source_transcript: bool = True,
    request_audio_output: bool = False,
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
# 1. RealtimeTranslator: vad_* を渡しても TypeError にならないこと（後方互換）
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorVadBackwardCompat:
    """refactor/remove-vad-dead-code 後の後方互換性検証。

    vad_* パラメータは削除済みだが渡しても TypeError にならないこと。
    """

    def test_vad_enabled_true_no_type_error(self):
        """vad_enabled=True を渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-default",
                target_language_code="ja",
                vad_enabled=True,
            )
        except TypeError as e:
            pytest.fail(f"vad_enabled=True で TypeError が発生してはならない: {e}")

    def test_vad_enabled_false_no_type_error(self):
        """vad_enabled=False を渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-threshold",
                target_language_code="ja",
                vad_enabled=False,
            )
        except TypeError as e:
            pytest.fail(f"vad_enabled=False で TypeError が発生してはならない: {e}")

    def test_all_vad_params_no_type_error(self):
        """全 vad_* パラメータを渡しても TypeError にならないこと。"""
        try:
            RealtimeTranslator(
                api_key="sk-test-fake-vad-custom",
                target_language_code="ja",
                vad_enabled=True,
                vad_threshold=0.7,
                vad_prefix_padding_ms=200,
                vad_silence_duration_ms=800,
            )
        except TypeError as e:
            pytest.fail(f"全 vad_* パラメータで TypeError が発生してはならない: {e}")


# ---------------------------------------------------------------------------
# 2. RealtimeTranslator: session.update の動作確認（turn_detection を送らない等）
# ---------------------------------------------------------------------------

class TestRealtimeTranslatorVadSessionUpdate:
    """GA 版 (2026-05-12 以降): session.update ペイロードの動作確認。

    turn_detection は送らない。audio.input は request_source_transcript で制御される。
    """

    def test_session_update_has_audio_input_when_source_transcript_true(self):
        """request_source_transcript=True（デフォルト）のとき session.update に audio.input が含まれること。"""
        payload = _capture_session_update()

        assert payload.get("type") == "session.update", f"expected session.update, got: {payload}"
        audio = payload.get("session", {}).get("audio", {})
        assert "input" in audio, (
            f"request_source_transcript=True（デフォルト）なので audio.input が含まれること。audio={audio}"
        )

    def test_session_update_no_turn_detection_ga(self):
        """GA 版: turn_detection は送信されないこと。"""
        payload = _capture_session_update()

        audio = payload.get("session", {}).get("audio", {})
        audio_input = audio.get("input", {})
        assert "turn_detection" not in audio_input, (
            f"GA 版では turn_detection は送信されないこと。audio_input={audio_input}"
        )

    def test_session_update_has_transcription_when_source_transcript_true(self):
        """request_source_transcript=True のとき audio.input.transcription が含まれること。

        実機検証（2026-05-16）: input_transcript.delta には transcription + noise_reduction が必要。
        """
        payload = _capture_session_update(request_source_transcript=True)

        audio = payload.get("session", {}).get("audio", {})
        audio_input = audio.get("input", {})
        assert "transcription" in audio_input, (
            f"request_source_transcript=True のとき audio.input.transcription が含まれること。"
            f"audio_input={audio_input}"
        )
        assert "noise_reduction" in audio_input, (
            f"request_source_transcript=True のとき audio.input.noise_reduction が含まれること。"
            f"audio_input={audio_input}"
        )
        assert "turn_detection" not in audio_input, (
            f"GA 版では turn_detection は送らないこと。audio_input={audio_input}"
        )

    def test_no_audio_input_when_source_transcript_disabled_ga(self):
        """request_source_transcript=False のとき audio.input は含まれないこと。"""
        payload = _capture_session_update(
            request_source_transcript=False,
        )

        audio = payload.get("session", {}).get("audio", {})
        assert "input" not in audio, (
            f"request_source_transcript=False のとき audio.input は含まれないこと。audio={audio}"
        )

    def test_session_update_has_output_language_ga(self):
        """GA 版: session.update には audio.output.language が含まれること。"""
        payload = _capture_session_update()

        audio = payload.get("session", {}).get("audio", {})
        assert "output" in audio, (
            f"GA 版では audio.output が存在すること。audio={audio}"
        )
        assert audio["output"].get("language") == "ja", (
            f"audio.output.language は target_language_code と一致すること。audio={audio}"
        )

class TestRealtimeTranslatorVadMatrixTest:
    """GA 版 (2026-05-12 以降): request_audio_output のマトリクステスト。

    実機検証（2026-05-16）:
    - audio.output.language は request_audio_output の値に関わらず常に含まれる
    - audio.input は request_source_transcript=True（デフォルト）のとき含まれる
    - audio.input.turn_detection は含まれない（GA 仕様外）
    """

    @pytest.mark.parametrize("request_audio_output", [False, True])
    def test_session_update_always_has_output_language(self, request_audio_output):
        """GA 版: request_audio_output に関わらず audio.output.language は常に含まれること。"""
        payload = _capture_session_update(
            request_audio_output=request_audio_output,
            request_source_transcript=True,
        )

        audio = payload.get("session", {}).get("audio", {})

        # GA 版: audio.output は常に存在すること（language 指定に必要）
        assert "output" in audio, (
            f"GA 版では audio.output が常に存在すること。"
            f"params: request_audio_output={request_audio_output}. "
            f"audio={audio}"
        )
        assert audio["output"].get("language") == "ja", (
            f"audio.output.language は 'ja' であること。"
            f"params: request_audio_output={request_audio_output}. "
            f"audio={audio}"
        )

        # request_source_transcript=True なので audio.input は含まれること
        assert "input" in audio, (
            f"request_source_transcript=True なので audio.input が含まれること。"
            f"params: request_audio_output={request_audio_output}. "
            f"audio={audio}"
        )

        # turn_detection は GA 仕様外なので含まれないこと
        audio_input = audio.get("input", {})
        assert "turn_detection" not in audio_input, (
            f"GA 版では turn_detection は含まれないこと（仕様外）。"
            f"params: request_audio_output={request_audio_output}. "
            f"audio_input={audio_input}"
        )

class TestCaptionSystemVadBackwardCompat:
    """CaptionSystem に vad_* を渡しても TypeError にならないこと（後方互換）。"""

    def _make_caption_system_with_vad(self, **vad_kwargs) -> CaptionSystem:
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
        return CaptionSystem(
            config=config,
            device_info=device_info,
            model_name="tiny",
            **vad_kwargs,
        )

    def test_vad_enabled_true_no_type_error(self):
        """CaptionSystem(vad_enabled=True) を渡しても TypeError にならないこと。"""
        try:
            self._make_caption_system_with_vad(vad_enabled=True)
        except TypeError as e:
            pytest.fail(f"vad_enabled=True で TypeError が発生してはならない: {e}")

    def test_vad_enabled_false_no_type_error(self):
        """CaptionSystem(vad_enabled=False) を渡しても TypeError にならないこと。"""
        try:
            self._make_caption_system_with_vad(vad_enabled=False)
        except TypeError as e:
            pytest.fail(f"vad_enabled=False で TypeError が発生してはならない: {e}")

    def test_all_vad_params_no_type_error(self):
        """全 vad_* パラメータを渡しても TypeError にならないこと。"""
        try:
            self._make_caption_system_with_vad(
                vad_enabled=True,
                vad_threshold=0.7,
                vad_prefix_padding_ms=200,
                vad_silence_duration_ms=800,
            )
        except TypeError as e:
            pytest.fail(f"全 vad_* パラメータで TypeError が発生してはならない: {e}")


# ---------------------------------------------------------------------------
# 5. RouteConfig: vad_* を渡しても TypeError にならないこと（後方互換）
# ---------------------------------------------------------------------------

class TestRouteConfigVadBackwardCompat:
    """RouteConfig に vad_* を渡しても TypeError にならないこと（後方互換）。"""

    def test_vad_enabled_no_type_error(self):
        """RouteConfig に vad_enabled=True を渡しても TypeError にならないこと。"""
        try:
            RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_enabled=True,
            )
        except TypeError as e:
            pytest.fail(f"vad_enabled=True で TypeError が発生してはならない: {e}")

    def test_all_vad_params_no_type_error(self):
        """RouteConfig に全 vad_* を渡しても TypeError にならないこと。"""
        try:
            RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMic", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_enabled=True,
                vad_threshold=0.7,
                vad_prefix_padding_ms=200,
                vad_silence_duration_ms=800,
            )
        except TypeError as e:
            pytest.fail(f"全 vad_* パラメータで TypeError が発生してはならない: {e}")


# ---------------------------------------------------------------------------
# 6. MultiCaptionSystem: vad_* を渡しても TypeError にならないこと（後方互換）
# ---------------------------------------------------------------------------

class TestMultiCaptionSystemVadBackwardCompat:
    """MultiCaptionSystem / RouteConfig に vad_* を渡しても TypeError にならないこと（後方互換）。"""

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

    def test_multi_caption_system_with_vad_no_type_error(self):
        """RouteConfig に vad_enabled=True を渡した MultiCaptionSystem が TypeError にならないこと。"""
        try:
            route_a = RouteConfig(
                route_id="a",
                input_device_info={"name": "FakeMicA", "index": 0},
                target_language_code="ja",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_enabled=True,
            )
        except TypeError as e:
            pytest.fail(f"RouteConfig(vad_enabled=True) で TypeError が発生してはならない: {e}")

        try:
            with patch("main.pyaudio.PyAudio") as MockPA:
                MockPA.return_value = MagicMock()
                mcs = MultiCaptionSystem(
                    config=self._make_config(),
                    route_a=route_a,
                    route_b=None,
                )
        except TypeError as e:
            pytest.fail(f"MultiCaptionSystem(route_a with vad_enabled) で TypeError が発生してはならない: {e}")

        assert mcs.route_a_system is not None

    def test_multi_caption_system_with_vad_false_no_type_error(self):
        """RouteConfig に vad_enabled=False を渡した MultiCaptionSystem が TypeError にならないこと。"""
        try:
            route_b = RouteConfig(
                route_id="b",
                input_device_info={"name": "FakeMicB", "index": 1},
                target_language_code="en",
                audio_output_enabled=False,
                output_device_index=None,
                output_volume=1.0,
                vad_enabled=False,
            )
        except TypeError as e:
            pytest.fail(f"RouteConfig(vad_enabled=False) で TypeError が発生してはならない: {e}")

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=None,
                route_b=route_b,
            )

        assert mcs.route_b_system is not None

    def test_multi_caption_system_no_vad_params_no_type_error(self):
        """RouteConfig デフォルト（vad_enabled 省略）でも MultiCaptionSystem が TypeError にならないこと。"""
        route_a = RouteConfig(
            route_id="a",
            input_device_info={"name": "FakeMicA", "index": 0},
            target_language_code="ja",
            audio_output_enabled=False,
            output_device_index=None,
            output_volume=1.0,
        )

        with patch("main.pyaudio.PyAudio") as MockPA:
            MockPA.return_value = MagicMock()
            mcs = MultiCaptionSystem(
                config=self._make_config(),
                route_a=route_a,
                route_b=None,
            )

        assert mcs.route_a_system is not None


# ---------------------------------------------------------------------------
# 7. app.py: vad_enabled の settings save/load
# DEPRECATED: W-COST-3 UI 廃止 (GA で動作不能) — refactor/remove-vad-ui-ga-cleanup
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="W-COST-3 UI 廃止: _save_settings から VAD 保存コードを削除 — refactor/remove-vad-ui-ga-cleanup")
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
        """_create_konnyaku_system は saved settings に vad_enabled=True があっても
        hotfix により強制 False にして RouteConfig に渡すこと。
        (hotfix/vad-force-off-and-smoke-strict: TBD-3-1 再オープン、issue #121)"""
        fake_devices = [
            {"name": "Mic1", "index": 0, "samplerate": 16000},
        ]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": True,   # ON に設定（hotfix で強制 OFF される）
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": False,  # OFF に設定（そのまま）
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

        # hotfix: saved vad_enabled=True は強制 OFF されて False になる
        assert created_route_a_kwargs.get("vad_enabled") is False, (
            f"hotfix により route_a の vad_enabled は False であること。got={created_route_a_kwargs}"
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


# ---------------------------------------------------------------------------
# 8. app.py: VAD 数値パラメータ (threshold / prefix_padding_ms / silence_duration_ms)
#    の save/load 往復確認 (PR #97 QA 仕切り直し W-2)
# DEPRECATED: W-COST-3 UI 廃止 (GA で動作不能) — refactor/remove-vad-ui-ga-cleanup
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="W-COST-3 UI 廃止: _save_settings / _create_konnyaku_system から VAD 数値パラメータ削除 — refactor/remove-vad-ui-ga-cleanup")
class TestAppSettingsVadNumericParams:
    """vad_threshold / vad_prefix_padding_ms / vad_silence_duration_ms が
    _save_settings / _create_konnyaku_system で正しく保存・読み込みされること。"""

    def setup_method(self):
        self._old_system = app._konnyaku_system
        self._old_running = app._konnyaku_running

    def teardown_method(self):
        app._konnyaku_system = self._old_system
        app._konnyaku_running = self._old_running

    def _run_save_settings(self) -> dict:
        """_save_settings を呼んで保存された dict を返すヘルパー。"""
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

        return saved_data

    def test_save_settings_includes_vad_numeric_params_route_a(self):
        """_save_settings が route_a の vad 数値パラメータ 3 つを保存すること。
        (PR #97 QA 仕切り直し W-2)"""
        saved_data = self._run_save_settings()
        route_a = saved_data.get("route_a", {})
        assert "vad_threshold" in route_a, (
            f"route_a に vad_threshold が保存されること。route_a={route_a}"
        )
        assert "vad_prefix_padding_ms" in route_a, (
            f"route_a に vad_prefix_padding_ms が保存されること。route_a={route_a}"
        )
        assert "vad_silence_duration_ms" in route_a, (
            f"route_a に vad_silence_duration_ms が保存されること。route_a={route_a}"
        )

    def test_save_settings_includes_vad_numeric_params_route_b(self):
        """_save_settings が route_b の vad 数値パラメータ 3 つを保存すること。
        (PR #97 QA 仕切り直し W-2)"""
        saved_data = self._run_save_settings()
        route_b = saved_data.get("route_b", {})
        assert "vad_threshold" in route_b, (
            f"route_b に vad_threshold が保存されること。route_b={route_b}"
        )
        assert "vad_prefix_padding_ms" in route_b, (
            f"route_b に vad_prefix_padding_ms が保存されること。route_b={route_b}"
        )
        assert "vad_silence_duration_ms" in route_b, (
            f"route_b に vad_silence_duration_ms が保存されること。route_b={route_b}"
        )

    def test_create_konnyaku_system_passes_vad_numeric_params_to_route_config(self):
        """_create_konnyaku_system が saved settings の vad 数値パラメータを
        RouteConfig に反映すること。(PR #97 QA 仕切り直し W-2)"""
        fake_devices = [{"name": "Mic1", "index": 0, "samplerate": 16000}]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": False,
                "vad_threshold": 0.7,
                "vad_prefix_padding_ms": 200,
                "vad_silence_duration_ms": 800,
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": False,
                "vad_threshold": 0.6,
                "vad_prefix_padding_ms": 150,
                "vad_silence_duration_ms": 600,
            },
        }

        app._konnyaku_system = None
        app._konnyaku_running = False
        captured_a = {}
        captured_b = {}

        def capturing_mcs(config, route_a, route_b, **kwargs):
            if route_a is not None:
                captured_a["vad_threshold"] = route_a.vad_threshold
                captured_a["vad_prefix_padding_ms"] = route_a.vad_prefix_padding_ms
                captured_a["vad_silence_duration_ms"] = route_a.vad_silence_duration_ms
            if route_b is not None:
                captured_b["vad_threshold"] = route_b.vad_threshold
                captured_b["vad_prefix_padding_ms"] = route_b.vad_prefix_padding_ms
                captured_b["vad_silence_duration_ms"] = route_b.vad_silence_duration_ms
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

        import pytest as _pytest
        assert captured_a.get("vad_threshold") == _pytest.approx(0.7), (
            f"route_a vad_threshold=0.7 が反映されること。got={captured_a}"
        )
        assert captured_a.get("vad_prefix_padding_ms") == 200, (
            f"route_a vad_prefix_padding_ms=200 が反映されること。got={captured_a}"
        )
        assert captured_a.get("vad_silence_duration_ms") == 800, (
            f"route_a vad_silence_duration_ms=800 が反映されること。got={captured_a}"
        )
        assert captured_b.get("vad_threshold") == _pytest.approx(0.6), (
            f"route_b vad_threshold=0.6 が反映されること。got={captured_b}"
        )
        assert captured_b.get("vad_prefix_padding_ms") == 150, (
            f"route_b vad_prefix_padding_ms=150 が反映されること。got={captured_b}"
        )
        assert captured_b.get("vad_silence_duration_ms") == 600, (
            f"route_b vad_silence_duration_ms=600 が反映されること。got={captured_b}"
        )

    def test_create_konnyaku_system_fallback_for_invalid_vad_numeric_params(self):
        """saved settings に vad 数値パラメータが文字列など不正値の場合、
        デフォルト値（threshold=0.5, prefix=300, silence=500）にフォールバックすること。
        (PR #97 QA 仕切り直し W-2)"""
        fake_devices = [{"name": "Mic1", "index": 0, "samplerate": 16000}]
        fake_settings = {
            "route_a": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
                "source_transcript_enabled": True,
                "vad_enabled": False,
                "vad_threshold": "invalid",     # 不正値
                "vad_prefix_padding_ms": None,  # 不正値
                "vad_silence_duration_ms": [],  # 不正値
            },
            "route_b": {
                "device": "Mic1",
                "lang": "",
                "output_enabled": False,
            },
        }

        app._konnyaku_system = None
        app._konnyaku_running = False
        captured_a = {}

        def capturing_mcs(config, route_a, route_b, **kwargs):
            if route_a is not None:
                captured_a["vad_threshold"] = route_a.vad_threshold
                captured_a["vad_prefix_padding_ms"] = route_a.vad_prefix_padding_ms
                captured_a["vad_silence_duration_ms"] = route_a.vad_silence_duration_ms
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

        import pytest as _pytest
        assert captured_a.get("vad_threshold") == _pytest.approx(0.5), (
            f"不正値の場合 vad_threshold は 0.5 にフォールバックすること。got={captured_a}"
        )
        assert captured_a.get("vad_prefix_padding_ms") == 300, (
            f"不正値の場合 vad_prefix_padding_ms は 300 にフォールバックすること。got={captured_a}"
        )
        assert captured_a.get("vad_silence_duration_ms") == 500, (
            f"不正値の場合 vad_silence_duration_ms は 500 にフォールバックすること。got={captured_a}"
        )
