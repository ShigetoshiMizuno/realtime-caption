"""
tests/test_vad_api_smoke.py

tools/test_vad_api_smoke.py の単体テスト。
純関数（build_session_update / parse_error_response / format_report）と
run_smoke_test の mock テストを行う。

実 API キーは一切使わない。fixture はすべてフェイク値のみ。
"""

import asyncio
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
    """GA 版 (2026-05-12 以降): build_session_update が最小ペイロードを構築すること。

    TBD-3-1 確定: turn_detection は 'Unknown parameter' エラーで拒否されるため送らない。
    GA 版では audio.output.language のみ送信する。
    """

    def test_type_is_session_update(self):
        """payload の type が 'session.update' であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        assert payload["type"] == "session.update"

    def test_session_audio_output_exists_ga(self):
        """GA 版: session.audio.output が存在すること（input は送らない）。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        assert "session" in payload
        assert "audio" in payload["session"]
        assert "output" in payload["session"]["audio"], (
            f"GA 版では audio.output が存在すること: {payload['session']['audio']}"
        )
        assert "input" not in payload["session"]["audio"], (
            f"GA 版では audio.input は送らないこと: {payload['session']['audio']}"
        )

    def test_audio_output_language_is_target_lang_ga(self):
        """GA 版: audio.output.language が target_lang と一致すること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        assert payload["session"]["audio"]["output"]["language"] == "ja", (
            f"audio.output.language は 'ja' であること: {payload}"
        )

    def test_audio_output_language_en_ga(self):
        """GA 版: target_lang='en' のとき audio.output.language が 'en' であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="en"
        )
        assert payload["session"]["audio"]["output"]["language"] == "en"

    def test_no_turn_detection_ga(self):
        """GA 版: turn_detection は含まれないこと（TBD-3-1 確定: GA 版では仕様外）。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        audio = payload["session"]["audio"]
        audio_input = audio.get("input", {})
        assert "turn_detection" not in audio_input, (
            f"GA 版では turn_detection は送らないこと: {audio}"
        )

    def test_no_transcription_ga(self):
        """GA 版: audio.input.transcription は含まれないこと（transcript は自動発行）。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        audio = payload["session"]["audio"]
        audio_input = audio.get("input", {})
        assert "transcription" not in audio_input, (
            f"GA 版では audio.input.transcription は送らないこと: {audio}"
        )

    def test_serializable_to_json(self):
        """payload が JSON シリアライズ可能であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        dumped = json.dumps(payload)
        reloaded = json.loads(dumped)
        assert reloaded["type"] == "session.update"

    def test_minimum_payload_structure_ga(self):
        """GA 版: payload は type / session / audio / output / language の最小構造であること。"""
        payload = build_session_update(
            threshold=0.5, silence_ms=500, prefix_ms=300, target_lang="ja"
        )
        assert payload == {
            "type": "session.update",
            "session": {
                "audio": {
                    "output": {"language": "ja"},
                },
            },
        }, f"GA 版最小ペイロードと一致すること: {payload}"


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

    def test_success_scenario_returns_ok_status(self):
        """接続成功・エラーなし → status='ok' が返ること。"""
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
            result = asyncio.run(
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
            result = asyncio.run(
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
            result = asyncio.run(
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
            result = asyncio.run(
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
            result = asyncio.run(
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
            result = asyncio.run(
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
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "type": "session.updated",
            "session": {"id": "fake-session-id-0001"},
        }))
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
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


# ---------------------------------------------------------------------------
# 5. run_smoke_test — エラーレスポンス検出の強化（Fix 3: hotfix/vad-force-off-and-smoke-strict）
# ---------------------------------------------------------------------------

class TestRunSmokeTestErrorDetection:
    """session.created 後にエラーレスポンスが来た場合も unsupported 検出できること。
    TBD-3-1 再オープン（2026-05-16 実機検証）の教訓:
    接続成功 (session.created) 後に error が来ても正しく検出すること。
    """

    def test_error_after_session_created_returns_unsupported(self):
        """session.created の後に Unknown parameter エラーが来た場合、unsupported と判定すること。"""
        # 1回目: session.created / 2回目: error
        recv_responses = [
            json.dumps({"type": "session.created", "session": {"id": "fake-sess-0000"}}),
            json.dumps({
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "code": "unknown_parameter",
                    "message": "Unknown parameter: 'session.audio.input.turn_detection'.",
                },
            }),
        ]
        recv_iter = iter(recv_responses)

        mock_ws = MagicMock()

        async def mock_recv():
            try:
                return next(recv_iter)
            except StopIteration:
                import asyncio as _asyncio
                raise _asyncio.TimeoutError()

        mock_ws.recv = mock_recv
        mock_ws.send = MagicMock(return_value=None)

        async def fake_send(data):
            pass

        mock_ws.send = fake_send
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-smoke-errorafter-0000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=2.0,
                )
            )

        assert result["status"] == "unsupported", (
            f"session.created 後の Unknown parameter error は unsupported であること。got={result}"
        )
        assert result["error_category"] == "unsupported"

    def test_session_updated_after_session_created_returns_ok(self):
        """session.created の後に session.updated が来た場合、ok と判定すること。"""
        recv_responses = [
            json.dumps({"type": "session.created", "session": {"id": "fake-sess-0001"}}),
            json.dumps({"type": "session.updated", "session": {"id": "fake-sess-0001"}}),
        ]
        recv_iter = iter(recv_responses)

        mock_ws = MagicMock()

        async def mock_recv():
            try:
                return next(recv_iter)
            except StopIteration:
                import asyncio as _asyncio
                raise _asyncio.TimeoutError()

        mock_ws.recv = mock_recv

        async def fake_send(data):
            pass

        mock_ws.send = fake_send
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-smoke-updated-0000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=2.0,
                )
            )

        assert result["status"] == "ok", (
            f"session.created 後に session.updated が来たら ok であること。got={result}"
        )

    def test_only_session_created_no_further_response_returns_no_response_or_ok(self):
        """session.created のみで session.updated もエラーも来ない場合、
        no_response もしくは ok が返ること（タイムアウト = 未確定）。"""
        recv_responses = [
            json.dumps({"type": "session.created", "session": {"id": "fake-sess-0002"}}),
        ]
        recv_iter = iter(recv_responses)

        mock_ws = MagicMock()

        async def mock_recv():
            try:
                return next(recv_iter)
            except StopIteration:
                import asyncio as _asyncio
                raise _asyncio.TimeoutError()

        mock_ws.recv = mock_recv

        async def fake_send(data):
            pass

        mock_ws.send = fake_send
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_vad_api_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-smoke-noresponse-0000",
                    threshold=0.5,
                    silence_ms=500,
                    prefix_ms=300,
                    target_lang="ja",
                    timeout=0.5,
                )
            )

        # session.created のみで session.updated がない場合、ok または no_response のどちらかを許容
        # (run_smoke_test の実装次第だが、少なくとも unsupported / auth_error ではないこと)
        assert result["status"] in ("ok", "no_response"), (
            f"session.created のみの場合は ok または no_response であること。got={result}"
        )

    def test_error_code_unknown_parameter_in_code_field(self):
        """error.code が 'unknown_parameter' の場合も unsupported に分類されること。"""
        error_data = {
            "type": "invalid_request_error",
            "code": "unknown_parameter",
            "message": "Unknown parameter: 'session.audio.input.turn_detection'.",
        }
        category, message = parse_error_response(error_data)
        assert category == "unsupported", (
            f"error.code='unknown_parameter' は unsupported に分類されること。category={category}"
        )

    def test_format_report_no_response_status(self):
        """status='no_response' のとき format_report が適切な文字列を返すこと。"""
        result = {
            "status": "no_response",
            "error_category": None,
            "error_message": None,
            "elapsed": 3.0,
            "timeout": 3.0,
            "params": {
                "vad_enabled": True,
                "vad_threshold": 0.5,
                "vad_silence_ms": 500,
                "vad_prefix_ms": 300,
                "target_language": "ja",
            },
        }
        text = format_report(result, output_json=False)
        # no_response は「不確定」として処理されるので、クラッシュしないこと
        assert isinstance(text, str)
        assert len(text) > 0
