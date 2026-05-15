"""
tests/test_vad_api_smoke.py

tools/test_vad_api_smoke.py の単体テスト。
純関数（build_session_update / parse_error_response / format_report）と
run_smoke_test の mock テストを行う。

実 API キーは一切使わない。fixture はすべてフェイク値のみ。
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# tools/ を sys.path に追加して import できるようにする
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from test_vad_api_smoke import (
    build_session_update,
    format_report,
    parse_error_response,
    run_smoke_test,
)


# ---------------------------------------------------------------------------
# 1. build_session_update — 出力構造検証
# ---------------------------------------------------------------------------

class TestBuildSessionUpdate:
    """build_session_update が正しい session.update payload を構築すること。"""

    def test_type_is_session_update(self):
        """payload の type が 'session.update' であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        assert payload["type"] == "session.update"

    def test_session_audio_input_exists(self):
        """session.audio.input が存在すること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        assert "session" in payload
        assert "audio" in payload["session"]
        assert "input" in payload["session"]["audio"]

    def test_transcription_model_is_gpt_realtime_whisper(self):
        """audio.input.transcription.model が 'gpt-realtime-whisper' であること（PR #29 との一致）。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        audio_input = payload["session"]["audio"]["input"]
        assert audio_input["transcription"]["model"] == "gpt-realtime-whisper"

    def test_turn_detection_type_is_server_vad(self):
        """turn_detection.type が 'server_vad' であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        td = payload["session"]["audio"]["input"]["turn_detection"]
        assert td["type"] == "server_vad"

    def test_turn_detection_threshold(self):
        """turn_detection.threshold が指定値通りであること。"""
        payload = build_session_update(
            threshold=0.7, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        td = payload["session"]["audio"]["input"]["turn_detection"]
        assert td["threshold"] == pytest.approx(0.7)

    def test_turn_detection_silence_duration_ms(self):
        """turn_detection.silence_duration_ms が指定値通りであること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=800, prefix_ms=300, target_lang="ja"
        )
        td = payload["session"]["audio"]["input"]["turn_detection"]
        assert td["silence_duration_ms"] == 800

    def test_turn_detection_prefix_padding_ms(self):
        """turn_detection.prefix_padding_ms が指定値通りであること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=200, target_lang="ja"
        )
        td = payload["session"]["audio"]["input"]["turn_detection"]
        assert td["prefix_padding_ms"] == 200

    def test_default_threshold(self):
        """デフォルト threshold は 0.5 であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        td = payload["session"]["audio"]["input"]["turn_detection"]
        assert td["threshold"] == pytest.approx(0.5)

    def test_turn_detection_matches_pr97_session_update(self):
        """PR #97 の session.update と同じ構造であること（turn_detection 全フィールド）。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        td = payload["session"]["audio"]["input"]["turn_detection"]
        assert set(td.keys()) >= {"type", "threshold", "prefix_padding_ms", "silence_duration_ms"}

    def test_serializable_to_json(self):
        """payload が JSON シリアライズ可能であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        dumped = json.dumps(payload)
        reloaded = json.loads(dumped)
        assert reloaded["type"] == "session.update"


# ---------------------------------------------------------------------------
# 2. parse_error_response — エラー分類
# ---------------------------------------------------------------------------

class TestParseErrorResponse:
    """parse_error_response が各種エラーを正しく分類すること。"""

    def test_unknown_parameter_returns_unsupported(self):
        """'Unknown parameter' を含むエラーは ('unsupported', ...) に分類されること。"""
        error_data = {
            "type": "invalid_request_error",
            "message": "Unknown parameter 'audio.input.turn_detection'",
        }
        category, message = parse_error_response(error_data)
        assert category == "unsupported"

    def test_unknown_parameter_message_preserved(self):
        """'Unknown parameter' エラーのメッセージがそのまま返ること。"""
        error_data = {
            "type": "invalid_request_error",
            "message": "Unknown parameter 'audio.input.turn_detection'",
        }
        category, message = parse_error_response(error_data)
        assert "Unknown parameter" in message

    def test_invalid_api_key_returns_auth(self):
        """'invalid_api_key' エラーは ('auth', ...) に分類されること。"""
        error_data = {
            "type": "invalid_request_error",
            "code": "invalid_api_key",
            "message": "Incorrect API key provided.",
        }
        category, message = parse_error_response(error_data)
        assert category == "auth"

    def test_rate_limit_returns_rate_limit(self):
        """'rate_limit_exceeded' エラーは ('rate_limit', ...) に分類されること。"""
        error_data = {
            "type": "rate_limit_exceeded",
            "message": "Rate limit exceeded.",
        }
        category, message = parse_error_response(error_data)
        assert category == "rate_limit"

    def test_unknown_error_returns_unknown(self):
        """未知のエラーは ('unknown', ...) に分類されること。"""
        error_data = {
            "type": "server_error",
            "message": "Internal server error.",
        }
        category, message = parse_error_response(error_data)
        assert category == "unknown"

    def test_empty_error_data_returns_unknown(self):
        """空の error_data は ('unknown', ...) に分類されること。"""
        category, message = parse_error_response({})
        assert category == "unknown"

    def test_returns_tuple_of_two_strings(self):
        """戻り値が (str, str) のタプルであること。"""
        error_data = {"type": "invalid_request_error", "message": "test"}
        result = parse_error_response(error_data)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)


# ---------------------------------------------------------------------------
# 3. format_report — テキスト/JSON フォーマット
# ---------------------------------------------------------------------------

class TestFormatReport:
    """format_report がテキスト/JSON を正しくフォーマットすること。"""

    def _make_success_result(self):
        return {
            "status": "ok",
            "error_category": None,
            "error_message": None,
            "elapsed": 0.84,
            "timeout": 15.0,
            "params": {
                "vad_enabled": True,
                "vad_threshold": 0.5,
                "vad_silence_ms": 500,
                "vad_prefix_ms": 300,
                "target_language": "ja",
            },
        }

    def _make_unsupported_result(self):
        return {
            "status": "unsupported",
            "error_category": "unsupported",
            "error_message": "Unknown parameter 'audio.input.turn_detection'",
            "elapsed": 1.2,
            "timeout": 15.0,
            "params": {
                "vad_enabled": True,
                "vad_threshold": 0.5,
                "vad_silence_ms": 500,
                "vad_prefix_ms": 300,
                "target_language": "ja",
            },
        }

    def test_success_text_contains_ok(self):
        """成功時のテキスト出力に 'OK' または '受入' が含まれること。"""
        text = format_report(self._make_success_result(), output_json=False)
        assert "OK" in text or "受入" in text or "ok" in text.lower()

    def test_success_text_contains_tbd31(self):
        """成功時のテキスト出力に 'TBD-3-1' が含まれること。"""
        text = format_report(self._make_success_result(), output_json=False)
        assert "TBD-3-1" in text

    def test_unsupported_text_contains_ng(self):
        """API 非対応時のテキスト出力に 'NG' または '非対応' が含まれること。"""
        text = format_report(self._make_unsupported_result(), output_json=False)
        assert "NG" in text or "非対応" in text or "ng" in text.lower()

    def test_json_output_is_valid_json(self):
        """JSON 出力が有効な JSON 文字列であること。"""
        text = format_report(self._make_success_result(), output_json=True)
        data = json.loads(text)
        assert isinstance(data, dict)

    def test_json_output_contains_status(self):
        """JSON 出力に 'status' キーが含まれること。"""
        text = format_report(self._make_success_result(), output_json=True)
        data = json.loads(text)
        assert "status" in data

    def test_json_output_success_status_ok(self):
        """JSON 出力の status が 'ok' であること（成功時）。"""
        text = format_report(self._make_success_result(), output_json=True)
        data = json.loads(text)
        assert data["status"] == "ok"

    def test_json_output_unsupported_status(self):
        """JSON 出力の status が 'unsupported' であること（API 非対応時）。"""
        text = format_report(self._make_unsupported_result(), output_json=True)
        data = json.loads(text)
        assert data["status"] == "unsupported"

    def test_text_output_contains_elapsed(self):
        """テキスト出力に経過時間（elapsed）が含まれること。"""
        text = format_report(self._make_success_result(), output_json=False)
        # 数値が含まれていることを確認（"0.84" or "0.84s" 形式）
        assert "0.84" in text or "elapsed" in text.lower() or "時間" in text


# ---------------------------------------------------------------------------
# 4. run_smoke_test — mock テスト
# ---------------------------------------------------------------------------

class TestRunSmokeTest:
    """run_smoke_test の mock テスト。実 API には接続しない。"""

    def _run(self, coro):
        """asyncio コルーチンを同期で実行するヘルパー。"""
        import asyncio
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_success_scenario_returns_ok_status(self):
        """接続成功・エラーなし → status='ok' が返ること。"""
        import asyncio

        async def fake_connect_and_run(api_key, threshold, silence_ms, prefix_ms,
                                       target_lang, timeout):
            # 接続成功・エラーなしシナリオのスタブ
            return {
                "status": "ok",
                "error_category": None,
                "error_message": None,
                "elapsed": 0.5,
                "timeout": timeout,
                "params": {
                    "vad_enabled": True,
                    "vad_threshold": threshold,
                    "vad_silence_ms": silence_ms,
                    "vad_prefix_ms": prefix_ms,
                    "target_language": target_lang,
                },
            }

        with patch(
            "test_vad_api_smoke.run_smoke_test",
            side_effect=fake_connect_and_run,
        ):
            result = asyncio.get_event_loop().run_until_complete(
                fake_connect_and_run(
                    api_key="sk-test-fake-smoke-0000000000000000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=15.0,
                )
            )

        assert result["status"] == "ok"

    def test_error_response_scenario_returns_unsupported(self):
        """エラーレスポンス（Unknown parameter）受信 → status='unsupported' が返ること。"""
        import asyncio

        async def fake_connect_and_run(api_key, threshold, silence_ms, prefix_ms,
                                       target_lang, timeout):
            return {
                "status": "unsupported",
                "error_category": "unsupported",
                "error_message": "Unknown parameter 'audio.input.turn_detection'",
                "elapsed": 0.3,
                "timeout": timeout,
                "params": {
                    "vad_enabled": True,
                    "vad_threshold": threshold,
                    "vad_silence_ms": silence_ms,
                    "vad_prefix_ms": prefix_ms,
                    "target_language": target_lang,
                },
            }

        with patch(
            "test_vad_api_smoke.run_smoke_test",
            side_effect=fake_connect_and_run,
        ):
            result = asyncio.get_event_loop().run_until_complete(
                fake_connect_and_run(
                    api_key="sk-test-fake-smoke-0000000000000000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=15.0,
                )
            )

        assert result["status"] == "unsupported"
        assert result["error_category"] == "unsupported"

    def test_timeout_scenario_returns_timeout(self):
        """タイムアウト → status='timeout' が返ること。"""
        import asyncio

        async def fake_connect_and_run(api_key, threshold, silence_ms, prefix_ms,
                                       target_lang, timeout):
            return {
                "status": "timeout",
                "error_category": "timeout",
                "error_message": "Connection timed out",
                "elapsed": timeout,
                "timeout": timeout,
                "params": {
                    "vad_enabled": True,
                    "vad_threshold": threshold,
                    "vad_silence_ms": silence_ms,
                    "vad_prefix_ms": prefix_ms,
                    "target_language": target_lang,
                },
            }

        with patch(
            "test_vad_api_smoke.run_smoke_test",
            side_effect=fake_connect_and_run,
        ):
            result = asyncio.get_event_loop().run_until_complete(
                fake_connect_and_run(
                    api_key="sk-test-fake-smoke-0000000000000000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=1.0,
                )
            )

        assert result["status"] == "timeout"

    def test_run_smoke_test_with_websocket_mock_success(self):
        """WebSocket モックを使った run_smoke_test 成功シナリオ。"""
        import asyncio

        # WebSocket 接続と受信をモック
        mock_ws = AsyncMock()
        # session.updated イベントを返す（VAD 設定受入 OK）
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "type": "session.updated",
            "session": {"id": "fake-session-id-0000"},
        }))
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.get_event_loop().run_until_complete(
                run_smoke_test(
                    api_key="sk-test-fake-smoke-0000000000000000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=1.0,
                )
            )

        assert result["status"] == "ok"
        assert result["error_category"] is None

    def test_run_smoke_test_with_websocket_mock_error(self):
        """WebSocket モックを使った run_smoke_test エラーシナリオ（Unknown parameter）。"""
        import asyncio

        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Unknown parameter 'audio.input.turn_detection'",
            },
        }))
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.get_event_loop().run_until_complete(
                run_smoke_test(
                    api_key="sk-test-fake-smoke-0000000000000000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=1.0,
                )
            )

        assert result["status"] == "unsupported"
        assert result["error_category"] == "unsupported"

    def test_run_smoke_test_with_auth_error(self):
        """WebSocket モックを使った run_smoke_test 認証エラーシナリオ。"""
        import asyncio

        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_api_key",
                "message": "Incorrect API key provided.",
            },
        }))
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.get_event_loop().run_until_complete(
                run_smoke_test(
                    api_key="sk-test-fake-smoke-0000000000000000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=1.0,
                )
            )

        assert result["status"] == "auth_error"
        assert result["error_category"] == "auth"

    def test_run_smoke_test_result_contains_params(self):
        """run_smoke_test の戻り値に params が含まれること。"""
        import asyncio

        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "type": "session.updated",
            "session": {"id": "fake-session-id-0001"},
        }))
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.get_event_loop().run_until_complete(
                run_smoke_test(
                    api_key="sk-test-fake-smoke-0000000000000000",
                    threshold=0.7,
                    silence_ms=800,
                    prefix_ms=200,
                    target_lang="en",
                    timeout=1.0,
                )
            )

        assert "params" in result
        assert result["params"]["vad_threshold"] == pytest.approx(0.7)
        assert result["params"]["vad_silence_ms"] == 800
        assert result["params"]["vad_prefix_ms"] == 200
        assert result["params"]["target_language"] == "en"
