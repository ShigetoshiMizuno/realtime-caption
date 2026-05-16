"""
tests/test_ga_migration.py

OpenAI Realtime API GA 移行（2026-05-12）対応テスト

背景:
  - 2026-05-12 に Realtime API Beta が削除され GA へ移行
  - GA 版では session.update のペイロードが変更された
  - 実機検証（2026-05-16）により以下が確定:
    - input_transcript.delta を受信するには audio.input.transcription + noise_reduction を
      セットで指定する必要がある
    - noise_reduction なしでは input_transcript が発行されない
    - turn_detection は GA 仕様外（Unknown parameter エラー）→ 送信しない

Fix 1: session.update ペイロードの検証
  - request_source_transcript=True のとき audio.input.transcription + noise_reduction を送ること
  - request_source_transcript=False のとき audio.input は送らないこと
  - vad_enabled に関わらず audio.input.turn_detection は送らないこと
  - audio.output.language は常に送ること

Fix 2: deprecation コメントの検証（属性は残るが効果なし）
  - request_source_transcript 属性は存在すること（後方互換）
  - vad_enabled 属性は存在すること（後方互換）
  - vad_enabled は session.update には反映されない（turn_detection は GA 仕様外）
"""

import asyncio
import json
import socket
import threading
import time

import pytest

from realtime_translator import RealtimeTranslator


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
    vad_enabled: bool = False,
    target_language_code: str = "ja",
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
            api_key="sk-test-fake-ga-migration-0000",
            target_language_code=target_language_code,
            on_error=lambda msg: None,
            reconnect_max_attempts=0,
            request_source_transcript=request_source_transcript,
            request_audio_output=request_audio_output,
            vad_enabled=vad_enabled,
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
# Fix 1: GA 版 session.update ペイロード検証
# ---------------------------------------------------------------------------

class TestGASessionUpdatePayload:
    """GA 版（2026-05-12 以降）の session.update ペイロードを検証する。

    実機検証（2026-05-16）により確定した仕様:
    - request_source_transcript=True のとき audio.input.transcription + noise_reduction をセット送信
    - request_source_transcript=False のとき audio.input は送らない
    - turn_detection は GA 仕様外（Unknown parameter エラー）→ vad_enabled に関わらず送らない
    - audio.output.language は常に送る
    """

    def test_session_update_type_is_session_update(self):
        """送信されるメッセージの type が 'session.update' であること。"""
        payload = _capture_session_update()
        assert payload.get("type") == "session.update", (
            f"type は 'session.update' であること。got={payload}"
        )

    def test_ga_session_update_has_audio_output_language(self):
        """GA 版 session.update は audio.output.language を含むこと。"""
        payload = _capture_session_update(
            request_audio_output=False,
            target_language_code="ja",
        )
        # GA 版: output.language は常に送る（transcript の言語指定に必要）
        audio = payload.get("session", {}).get("audio", {})
        assert "output" in audio, (
            f"GA 版では audio.output が存在すること。audio={audio}"
        )
        assert audio["output"].get("language") == "ja", (
            f"audio.output.language は target_language_code と一致すること。audio={audio}"
        )

    def test_ga_session_update_audio_input_when_source_transcript_true(self):
        """request_source_transcript=True のとき audio.input が含まれること。

        実機検証（2026-05-16）: input_transcript.delta を受信するには
        audio.input.transcription + noise_reduction をセットで指定する必要がある。
        """
        payload = _capture_session_update(
            request_source_transcript=True,
        )
        audio = payload.get("session", {}).get("audio", {})
        assert "input" in audio, (
            f"request_source_transcript=True のとき audio.input が含まれること。audio={audio}"
        )
        assert "transcription" in audio["input"], (
            f"audio.input.transcription が含まれること。audio={audio}"
        )
        assert "noise_reduction" in audio["input"], (
            f"audio.input.noise_reduction が含まれること（noise_reduction なしでは "
            f"input_transcript が発行されない: 実機検証 2026-05-16 確認）。audio={audio}"
        )

    def test_ga_session_update_no_audio_input_when_source_transcript_false(self):
        """request_source_transcript=False のとき audio.input は含まないこと。"""
        payload = _capture_session_update(
            request_source_transcript=False,
        )
        audio = payload.get("session", {}).get("audio", {})
        assert "input" not in audio, (
            f"request_source_transcript=False のとき audio.input は送らないこと。audio={audio}"
        )

    def test_ga_session_update_no_turn_detection_when_vad_enabled_true(self):
        """vad_enabled=True でも audio.input.turn_detection は含まないこと（GA 版）。

        GA 版では turn_detection は仕様外（Unknown parameter エラー）で拒否されるため送らない。
        audio.input は request_source_transcript=True（デフォルト）なので存在する。
        """
        payload = _capture_session_update(
            vad_enabled=True,
            request_source_transcript=True,
        )
        audio = payload.get("session", {}).get("audio", {})
        # audio.input は存在するが turn_detection は含まないこと
        audio_input = audio.get("input", {})
        assert "turn_detection" not in audio_input, (
            f"GA 版では vad_enabled=True でも audio.input.turn_detection は送らないこと。"
            f"audio_input={audio_input}"
        )

    def test_ga_session_update_no_turn_detection_when_vad_enabled_false(self):
        """vad_enabled=False のとき turn_detection は含まないこと（GA 版）。"""
        payload = _capture_session_update(
            vad_enabled=False,
            request_source_transcript=True,
        )
        audio = payload.get("session", {}).get("audio", {})
        audio_input = audio.get("input", {})
        assert "turn_detection" not in audio_input, (
            f"GA 版では vad_enabled=False のとき turn_detection は含まれないこと。"
            f"audio_input={audio_input}"
        )

    def test_ga_session_update_audio_keys_when_source_transcript_true(self):
        """request_source_transcript=True のとき audio セクションは output と input を持つこと。

        実機検証（2026-05-16）: audio.input.transcription + noise_reduction が必要。
        """
        payload = _capture_session_update(
            request_source_transcript=True,
            vad_enabled=True,
        )
        audio = payload.get("session", {}).get("audio", {})
        audio_keys = set(audio.keys())
        assert "output" in audio_keys, (
            f"audio に 'output' が存在すること。audio_keys={audio_keys}"
        )
        assert "input" in audio_keys, (
            f"request_source_transcript=True のとき audio に 'input' が存在すること。"
            f"audio_keys={audio_keys}"
        )

    def test_ga_session_update_language_code_passed_correctly_en(self):
        """target_language_code='en' が audio.output.language に反映されること。"""
        payload = _capture_session_update(
            target_language_code="en",
        )
        audio = payload.get("session", {}).get("audio", {})
        assert audio.get("output", {}).get("language") == "en", (
            f"audio.output.language は 'en' であること。audio={audio}"
        )

    def test_ga_session_update_transcription_model_correct(self):
        """audio.input.transcription.model が 'gpt-realtime-whisper' であること。"""
        payload = _capture_session_update(
            request_source_transcript=True,
        )
        audio = payload.get("session", {}).get("audio", {})
        audio_input = audio.get("input", {})
        assert audio_input.get("transcription", {}).get("model") == "gpt-realtime-whisper", (
            f"audio.input.transcription.model は 'gpt-realtime-whisper' であること。"
            f"audio_input={audio_input}"
        )

    def test_ga_session_update_noise_reduction_type_correct(self):
        """audio.input.noise_reduction.type が 'near_field' であること。"""
        payload = _capture_session_update(
            request_source_transcript=True,
        )
        audio = payload.get("session", {}).get("audio", {})
        audio_input = audio.get("input", {})
        assert audio_input.get("noise_reduction", {}).get("type") == "near_field", (
            f"audio.input.noise_reduction.type は 'near_field' であること。"
            f"audio_input={audio_input}"
        )


# ---------------------------------------------------------------------------
# Fix 2: request_source_transcript / vad_enabled 属性の後方互換性
# ---------------------------------------------------------------------------

class TestGAMigrationBackwardCompat:
    """GA 移行後も request_source_transcript / vad_enabled 属性は残ること（後方互換）。

    属性は存在するが session.update には反映されない（効果なし）。
    """

    def test_request_source_transcript_attribute_exists_true(self):
        """request_source_transcript=True が属性として格納されること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-ga-compat-0001",
            target_language_code="ja",
            request_source_transcript=True,
        )
        assert hasattr(translator, "_request_source_transcript"), (
            "_request_source_transcript 属性が存在すること"
        )
        assert translator._request_source_transcript is True

    def test_request_source_transcript_attribute_exists_false(self):
        """request_source_transcript=False が属性として格納されること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-ga-compat-0002",
            target_language_code="ja",
            request_source_transcript=False,
        )
        assert translator._request_source_transcript is False

    def test_vad_enabled_attribute_exists_true(self):
        """vad_enabled=True が属性として格納されること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-ga-compat-0003",
            target_language_code="ja",
            vad_enabled=True,
        )
        assert hasattr(translator, "_vad_enabled"), (
            "_vad_enabled 属性が存在すること"
        )
        assert translator._vad_enabled is True

    def test_vad_enabled_attribute_exists_false(self):
        """vad_enabled=False が属性として格納されること（デフォルト）。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-ga-compat-0004",
            target_language_code="ja",
        )
        assert translator._vad_enabled is False

    def test_vad_threshold_attribute_exists(self):
        """vad_threshold 属性が格納されること（後方互換）。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-ga-compat-0005",
            target_language_code="ja",
            vad_enabled=True,
            vad_threshold=0.7,
        )
        assert hasattr(translator, "_vad_threshold"), "_vad_threshold 属性が存在すること"
        assert translator._vad_threshold == pytest.approx(0.7)

    def test_vad_prefix_padding_ms_attribute_exists(self):
        """vad_prefix_padding_ms 属性が格納されること（後方互換）。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-ga-compat-0006",
            target_language_code="ja",
            vad_enabled=True,
            vad_prefix_padding_ms=200,
        )
        assert hasattr(translator, "_vad_prefix_padding_ms"), (
            "_vad_prefix_padding_ms 属性が存在すること"
        )
        assert translator._vad_prefix_padding_ms == 200

    def test_vad_silence_duration_ms_attribute_exists(self):
        """vad_silence_duration_ms 属性が格納されること（後方互換）。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-ga-compat-0007",
            target_language_code="ja",
            vad_enabled=True,
            vad_silence_duration_ms=800,
        )
        assert hasattr(translator, "_vad_silence_duration_ms"), (
            "_vad_silence_duration_ms 属性が存在すること"
        )
        assert translator._vad_silence_duration_ms == 800

    def test_request_source_transcript_controls_audio_input(self):
        """request_source_transcript の値によって session.update の audio.input の有無が変わること。

        実機検証（2026-05-16）:
        - True のとき: audio.input.transcription + noise_reduction をセット送信
        - False のとき: audio.input は送らない
        """
        payload_true = _capture_session_update(request_source_transcript=True)
        payload_false = _capture_session_update(request_source_transcript=False)

        audio_true = payload_true.get("session", {}).get("audio", {})
        audio_false = payload_false.get("session", {}).get("audio", {})

        # True のとき: audio.input が含まれること
        assert "input" in audio_true, (
            f"request_source_transcript=True のとき audio.input が含まれること。audio={audio_true}"
        )
        # False のとき: audio.input は含まないこと
        assert "input" not in audio_false, (
            f"request_source_transcript=False のとき audio.input は含まないこと。audio={audio_false}"
        )

    def test_vad_enabled_has_no_effect_on_turn_detection(self):
        """vad_enabled の値に関わらず turn_detection は送信されないこと（GA 版）。

        GA 版では turn_detection は仕様外（Unknown parameter エラー）。
        audio.input の有無は request_source_transcript で決まる（デフォルト=True）。
        """
        payload_true = _capture_session_update(vad_enabled=True, request_source_transcript=True)
        payload_false = _capture_session_update(vad_enabled=False, request_source_transcript=True)

        audio_true = payload_true.get("session", {}).get("audio", {})
        audio_false = payload_false.get("session", {}).get("audio", {})

        # どちらも turn_detection は含まないこと
        assert "turn_detection" not in audio_true.get("input", {}), (
            f"vad_enabled=True でも turn_detection は含まないこと（GA 版）。audio={audio_true}"
        )
        assert "turn_detection" not in audio_false.get("input", {}), (
            f"vad_enabled=False でも turn_detection は含まないこと（GA 版）。audio={audio_false}"
        )
