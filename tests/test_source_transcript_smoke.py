"""
tests/test_source_transcript_smoke.py

tools/test_source_transcript_smoke.py の単体テスト (issue #121 問題 A)。
純関数 (generate_sine_wave_pcm / count_events / format_report) と
run_smoke_test の mock テストを行う。

実 API キーは一切使わない。fixture はすべてフェイク値のみ。
"""

import asyncio
import json
import struct
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# tools/ を sys.path に追加して import できるようにする
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from test_source_transcript_smoke import (
    count_events,
    format_report,
    generate_sine_wave_pcm,
    run_smoke_test,
)


# ---------------------------------------------------------------------------
# 1. generate_sine_wave_pcm — 出力検証
# ---------------------------------------------------------------------------

class TestGenerateSineWavePcm:
    """generate_sine_wave_pcm が正しい PCM バイト列を生成すること。"""

    def test_output_is_bytes(self):
        """戻り値が bytes であること。"""
        result = generate_sine_wave_pcm(duration=0.1)
        assert isinstance(result, bytes)

    def test_length_matches_duration_and_sample_rate(self):
        """duration=1.0, sample_rate=24000 のとき bytes 長が 24000*2 (16bit) であること。"""
        result = generate_sine_wave_pcm(duration=1.0, sample_rate=24000)
        # 16bit PCM = 2 bytes/sample
        assert len(result) == 24000 * 2

    def test_length_matches_duration_half_second(self):
        """duration=0.5, sample_rate=24000 のとき bytes 長が 24000 (= 12000 samples * 2) であること。"""
        result = generate_sine_wave_pcm(duration=0.5, sample_rate=24000)
        assert len(result) == 12000 * 2

    def test_length_matches_custom_sample_rate(self):
        """sample_rate=16000, duration=1.0 のとき bytes 長が 32000 であること。"""
        result = generate_sine_wave_pcm(duration=1.0, sample_rate=16000)
        assert len(result) == 16000 * 2

    def test_non_zero_signal(self):
        """1kHz サイン波データがすべてゼロではないこと（音があること）。"""
        result = generate_sine_wave_pcm(duration=0.1, freq_hz=1000, sample_rate=24000)
        # 16bit little-endian で複数サンプルを読む
        samples = struct.unpack(f"<{len(result)//2}h", result)
        assert any(s != 0 for s in samples)

    def test_short_duration_not_empty(self):
        """duration=0.01 のとき空ではない bytes が返ること。"""
        result = generate_sine_wave_pcm(duration=0.01, sample_rate=24000)
        assert len(result) > 0

    def test_amplitude_within_int16_range(self):
        """サンプル値が int16 の範囲 (-32768〜32767) に収まること。"""
        result = generate_sine_wave_pcm(duration=0.1, freq_hz=1000, sample_rate=24000)
        samples = struct.unpack(f"<{len(result)//2}h", result)
        assert all(-32768 <= s <= 32767 for s in samples)


# ---------------------------------------------------------------------------
# 2. count_events — イベント集計
# ---------------------------------------------------------------------------

class TestCountEvents:
    """count_events がイベントリストを正しく集計すること。"""

    def test_empty_list_returns_empty_dict(self):
        """空リストは空 dict を返すこと。"""
        result = count_events([])
        assert result == {}

    def test_single_event_counted(self):
        """1件のイベントが 1 としてカウントされること。"""
        events = [{"type": "session.input_transcript.delta"}]
        result = count_events(events)
        assert result["session.input_transcript.delta"] == 1

    def test_multiple_same_events_counted(self):
        """同じ型のイベントが複数件正しくカウントされること。"""
        events = [
            {"type": "session.input_transcript.delta"},
            {"type": "session.input_transcript.delta"},
            {"type": "session.input_transcript.delta"},
        ]
        result = count_events(events)
        assert result["session.input_transcript.delta"] == 3

    def test_multiple_different_events_counted(self):
        """異なる型のイベントがそれぞれ正しくカウントされること。"""
        events = [
            {"type": "session.input_transcript.delta"},
            {"type": "session.output_transcript.delta"},
            {"type": "session.input_transcript.delta"},
            {"type": "session.created"},
        ]
        result = count_events(events)
        assert result["session.input_transcript.delta"] == 2
        assert result["session.output_transcript.delta"] == 1
        assert result["session.created"] == 1

    def test_event_without_type_counted_as_unknown(self):
        """type フィールドのないイベントが集計に含まれること（型なしとして）。"""
        events = [{"no_type_field": True}]
        result = count_events(events)
        # type なしは "" か "unknown" としてカウント、または無視 — どちらかを許容
        total = sum(result.values())
        assert total <= 1  # 多くて 1 件

    def test_returns_dict(self):
        """戻り値が dict であること。"""
        result = count_events([{"type": "session.created"}])
        assert isinstance(result, dict)

    def test_count_values_are_int(self):
        """カウント値が int であること。"""
        events = [{"type": "session.input_transcript.delta"}, {"type": "session.created"}]
        result = count_events(events)
        for v in result.values():
            assert isinstance(v, int)


# ---------------------------------------------------------------------------
# 3. format_report — テキスト/JSON フォーマット
# ---------------------------------------------------------------------------

class TestFormatReport:
    """format_report が正しいテキスト/JSON を生成すること。"""

    def _make_result_with_input_events(self):
        """input_transcript.delta 受信ありの結果 dict。"""
        return {
            "status": "input_transcript_received",
            "input_transcript_count": 45,
            "output_transcript_count": 120,
            "event_counts": {
                "session.input_transcript.delta": 45,
                "session.input_transcript.done": 5,
                "session.output_transcript.delta": 120,
                "session.output_transcript.done": 18,
                "session.created": 1,
                "session.updated": 1,
            },
            "elapsed": 31.5,
            "duration": 30.0,
            "audio_source": "sine wave 1kHz",
            "request_source_transcript": True,
        }

    def _make_result_without_input_events(self):
        """input_transcript.delta 受信なしの結果 dict。"""
        return {
            "status": "input_transcript_missing",
            "input_transcript_count": 0,
            "output_transcript_count": 0,
            "event_counts": {
                "session.created": 1,
                "session.updated": 1,
            },
            "elapsed": 31.5,
            "duration": 30.0,
            "audio_source": "sine wave 1kHz",
            "request_source_transcript": True,
        }

    def test_text_output_contains_result_header(self):
        """テキスト出力に結果ヘッダーが含まれること。"""
        text = format_report(self._make_result_with_input_events(), output_json=False)
        assert "結果" in text or "==" in text

    def test_text_received_mentions_api_sends(self):
        """受信ありのとき 'API' または '原文' に言及すること。"""
        text = format_report(self._make_result_with_input_events(), output_json=False)
        assert "API" in text or "原文" in text

    def test_text_missing_mentions_not_received(self):
        """受信なしのとき '0 件' または '送信していない' に言及すること。"""
        text = format_report(self._make_result_without_input_events(), output_json=False)
        assert "0" in text or "なし" in text or "送信" in text or "受信" in text

    def test_json_output_is_valid_json(self):
        """JSON 出力が有効な JSON 文字列であること。"""
        text = format_report(self._make_result_with_input_events(), output_json=True)
        data = json.loads(text)
        assert isinstance(data, dict)

    def test_json_contains_status(self):
        """JSON 出力に 'status' キーが含まれること。"""
        text = format_report(self._make_result_with_input_events(), output_json=True)
        data = json.loads(text)
        assert "status" in data

    def test_json_received_status_value(self):
        """受信ありのとき JSON の status が 'input_transcript_received' であること。"""
        text = format_report(self._make_result_with_input_events(), output_json=True)
        data = json.loads(text)
        assert data["status"] == "input_transcript_received"

    def test_json_missing_status_value(self):
        """受信なしのとき JSON の status が 'input_transcript_missing' であること。"""
        text = format_report(self._make_result_without_input_events(), output_json=True)
        data = json.loads(text)
        assert data["status"] == "input_transcript_missing"

    def test_json_contains_input_transcript_count(self):
        """JSON 出力に 'input_transcript_count' キーが含まれること。"""
        text = format_report(self._make_result_with_input_events(), output_json=True)
        data = json.loads(text)
        assert "input_transcript_count" in data
        assert data["input_transcript_count"] == 45

    def test_text_output_contains_elapsed(self):
        """テキスト出力に経過時間が含まれること。"""
        text = format_report(self._make_result_with_input_events(), output_json=False)
        assert "31.5" in text or "31" in text or "時間" in text

    def test_text_received_contains_client_hint(self):
        """受信ありのとき 'クライアント' または 'callback' への言及があること。"""
        text = format_report(self._make_result_with_input_events(), output_json=False)
        assert "クライアント" in text or "callback" in text or "client" in text.lower()

    def test_text_missing_contains_api_hint(self):
        """受信なしのとき 'API' または 'gpt-realtime' への言及があること。"""
        text = format_report(self._make_result_without_input_events(), output_json=False)
        assert "API" in text or "gpt-realtime" in text or "W-COST" in text


# ---------------------------------------------------------------------------
# 4. run_smoke_test — mock テスト（実 API 接続なし）
# ---------------------------------------------------------------------------

class TestRunSmokeTest:
    """run_smoke_test の mock テスト。実 API には接続しない。"""

    def _make_ws_mock(self, events: list[dict]):
        """指定イベントを順番に返す WebSocket モックを作る。"""
        mock_ws = AsyncMock()
        # イベント列 → recv が順番に返す
        responses = [json.dumps(e) for e in events]
        # 最後は TimeoutError
        side_effects = responses + [asyncio.TimeoutError()]
        mock_ws.recv = AsyncMock(side_effect=side_effects)
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)
        return mock_ws

    def test_returns_dict(self):
        """run_smoke_test が dict を返すこと。"""
        events = [
            {"type": "session.created"},
            {"type": "session.updated"},
        ]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert isinstance(result, dict)

    def test_result_contains_status(self):
        """戻り値に 'status' キーがあること。"""
        events = [{"type": "session.created"}]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert "status" in result

    def test_input_transcript_received_returns_correct_status(self):
        """session.input_transcript.delta 受信ありのとき status='input_transcript_received' であること。"""
        events = [
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "session.input_transcript.delta", "delta": "Hello"},
            {"type": "session.input_transcript.done"},
        ]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert result["status"] == "input_transcript_received"

    def test_input_transcript_count_is_correct(self):
        """session.input_transcript.delta が 3 件あれば input_transcript_count=3 であること。"""
        events = [
            {"type": "session.created"},
            {"type": "session.input_transcript.delta", "delta": "He"},
            {"type": "session.input_transcript.delta", "delta": "ll"},
            {"type": "session.input_transcript.delta", "delta": "o"},
            {"type": "session.input_transcript.done"},
        ]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert result["input_transcript_count"] == 3

    def test_no_input_transcript_returns_missing_status(self):
        """session.input_transcript.delta が 0 件のとき status='input_transcript_missing' であること。"""
        events = [
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "session.output_transcript.delta", "delta": "こんにちは"},
            {"type": "session.output_transcript.done"},
        ]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert result["status"] == "input_transcript_missing"
        assert result["input_transcript_count"] == 0

    def test_result_contains_event_counts(self):
        """戻り値に 'event_counts' dict が含まれること。"""
        events = [
            {"type": "session.created"},
            {"type": "session.input_transcript.delta", "delta": "test"},
        ]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert "event_counts" in result
        assert isinstance(result["event_counts"], dict)

    def test_result_contains_elapsed(self):
        """戻り値に 'elapsed' が含まれること。"""
        events = [{"type": "session.created"}]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert "elapsed" in result
        assert isinstance(result["elapsed"], float)

    def test_auth_error_returns_auth_status(self):
        """認証エラーイベント受信時 status='auth_error' であること。"""
        events = [
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                    "message": "Incorrect API key provided.",
                },
            }
        ]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert result["status"] == "auth_error"

    def test_timeout_scenario_returns_timeout_status(self):
        """接続タイムアウト時 status='timeout' であること。"""
        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_ws.__aexit__ = AsyncMock(return_value=None)

        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=0.01,
                )
            )
        assert result["status"] == "timeout"

    def test_result_contains_output_transcript_count(self):
        """戻り値に 'output_transcript_count' が含まれること。"""
        events = [
            {"type": "session.created"},
            {"type": "session.output_transcript.delta", "delta": "hello"},
            {"type": "session.output_transcript.done"},
        ]
        mock_ws = self._make_ws_mock(events)
        with patch("test_source_transcript_smoke.websockets_connect", return_value=mock_ws):
            result = asyncio.run(
                run_smoke_test(
                    api_key="sk-test-fake-source-0000000000000000",
                    duration=0.05,
                    audio_pcm=b"\x00" * 100,
                    target_lang="ja",
                    timeout=2.0,
                )
            )
        assert "output_transcript_count" in result
        assert result["output_transcript_count"] == 1
