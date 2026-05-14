"""
tests/test_realtime_error_classify.py

_classify_realtime_error ヘルパー関数の単体テスト (Issue #48)。

TDD: RED → GREEN の順で実装。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import _classify_realtime_error


# ---------------------------------------------------------------------------
# _classify_realtime_error の基本分類テスト
# ---------------------------------------------------------------------------

class TestClassifyRealtimeError:
    """_classify_realtime_error がエラー文字列を正しく分類すること。"""

    def test_classify_insufficient_quota(self):
        """insufficient_quota を含むメッセージは quota カテゴリになること。"""
        cat, txt = _classify_realtime_error(
            "Error code: 429 - {'error': {'message': 'insufficient_quota.insufficient_quota'}}"
        )
        assert cat == "quota"
        assert "クォータ" in txt

    def test_classify_invalid_api_key(self):
        """invalid_api_key を含むメッセージは auth カテゴリになること。"""
        cat, txt = _classify_realtime_error(
            "invalid_api_key: The API key provided is invalid."
        )
        assert cat == "auth"
        assert "API キーが無効" in txt

    def test_classify_incorrect_api_key(self):
        """'incorrect api key' を含むメッセージも auth カテゴリになること。"""
        cat, txt = _classify_realtime_error(
            "Incorrect API key provided: sk-test-fake-0000000000000000"
        )
        assert cat == "auth"
        assert "API キーが無効" in txt

    def test_classify_authentication(self):
        """'authentication' を含むメッセージも auth カテゴリになること。"""
        cat, txt = _classify_realtime_error(
            "Authentication failed: invalid credentials"
        )
        assert cat == "auth"

    def test_classify_rate_limit_with_text(self):
        """rate_limit_exceeded を含むメッセージは rate_limit カテゴリになること。"""
        cat, txt = _classify_realtime_error("429 rate_limit_exceeded")
        assert cat == "rate_limit"

    def test_classify_rate_limit_with_space(self):
        """'rate limit' (スペース区切り) を含むメッセージも rate_limit カテゴリになること。"""
        cat, txt = _classify_realtime_error(
            "You exceeded your rate limit. Please try again later."
        )
        assert cat == "rate_limit"

    def test_classify_429_status_code(self):
        """'429' を含むメッセージも rate_limit カテゴリになること。"""
        cat, txt = _classify_realtime_error("HTTP 429 Too Many Requests")
        assert cat == "rate_limit"

    def test_classify_connection_refused(self):
        """'connection refused' を含むメッセージは connection カテゴリになること。"""
        cat, txt = _classify_realtime_error("Connection refused: could not connect to API")
        assert cat == "connection"

    def test_classify_timeout(self):
        """'timeout' を含むメッセージも connection カテゴリになること。"""
        cat, txt = _classify_realtime_error("Request timeout after 30 seconds")
        assert cat == "connection"

    def test_classify_refused(self):
        """'refused' を含むメッセージも connection カテゴリになること。"""
        cat, txt = _classify_realtime_error("TCP connection refused on port 443")
        assert cat == "connection"

    def test_classify_other_unknown_message(self):
        """不明なメッセージは other カテゴリになること。"""
        cat, txt = _classify_realtime_error("Something completely unexpected happened")
        assert cat == "other"
        assert "OpenAI API エラー" in txt

    def test_classify_other_truncates_long_message(self):
        """200文字を超えるメッセージは切り詰められること。"""
        long = "x" * 200
        cat, txt = _classify_realtime_error(long)
        assert cat == "other"
        assert len(txt) < 150

    def test_classify_other_short_message_no_ellipsis(self):
        """100文字以内のメッセージは '...' が付かないこと。"""
        short = "short error"
        cat, txt = _classify_realtime_error(short)
        assert cat == "other"
        assert "..." not in txt

    def test_classify_long_message_has_ellipsis(self):
        """100文字を超えるメッセージは '...' が付くこと。"""
        long = "e" * 101
        cat, txt = _classify_realtime_error(long)
        assert cat == "other"
        assert txt.endswith("...")

    def test_classify_case_insensitive(self):
        """大文字小文字を区別しないこと（Insufficient_Quota など）。"""
        cat, txt = _classify_realtime_error("Insufficient_Quota exceeded")
        assert cat == "quota"

    def test_classify_display_text_contains_warning_prefix(self):
        """すべてのカテゴリで display_text が ⚠️ から始まること。"""
        cases = [
            "insufficient_quota",
            "invalid_api_key",
            "rate_limit exceeded",
            "Connection refused",
            "unknown error",
        ]
        for msg in cases:
            cat, txt = _classify_realtime_error(msg)
            assert txt.startswith("⚠️"), (
                f"msg={msg!r} → cat={cat!r}: display_text が ⚠️ から始まらない: {txt!r}"
            )
