"""
tests/test_qa_residuals.py

Issue #3 残項目（#5: DeepL 言語マップ正規化 / #6: API キー判定強化）のテスト。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# #5: DeepL 言語マップ正規化（_normalize_deepl_lang）
# ---------------------------------------------------------------------------

class TestNormalizeDeeplLang:
    def test_japanese_keys(self):
        from main import _normalize_deepl_lang
        assert _normalize_deepl_lang("日本語") == "JA"
        assert _normalize_deepl_lang("英語") == "EN-US"
        assert _normalize_deepl_lang("中国語") == "ZH"

    def test_english_keys_case_insensitive(self):
        from main import _normalize_deepl_lang
        assert _normalize_deepl_lang("english") == "EN-US"
        assert _normalize_deepl_lang("English") == "EN-US"
        assert _normalize_deepl_lang("ENGLISH") == "EN-US"
        assert _normalize_deepl_lang("japanese") == "JA"

    def test_whitespace_stripped(self):
        from main import _normalize_deepl_lang
        assert _normalize_deepl_lang("  日本語  ") == "JA"
        assert _normalize_deepl_lang(" english ") == "EN-US"

    def test_unknown_falls_back_to_uppercase(self):
        from main import _normalize_deepl_lang
        # マップにない値は uppercase で直接返す（"EN-US" や "JA" の直接指定も許容）
        assert _normalize_deepl_lang("EN-US") == "EN-US"
        assert _normalize_deepl_lang("ja") == "JA"


# ---------------------------------------------------------------------------
# #6: API キー判定強化（_looks_like_openai_key / _looks_like_deepl_key）
# ---------------------------------------------------------------------------

class TestLooksLikeOpenAIKey:
    def test_valid_keys(self):
        from app import _looks_like_openai_key
        assert _looks_like_openai_key("sk-abcdefghijklmnopqrstuvwxyz1234")
        assert _looks_like_openai_key("sk-proj-abcdefghijklmnopqrstuvwxyz")

    def test_placeholder_rejected(self):
        from app import _looks_like_openai_key
        assert not _looks_like_openai_key("sk-xxxxxxxxxxxxxxxxxxxx")
        assert not _looks_like_openai_key("your-api-key-here")
        assert not _looks_like_openai_key("placeholder-key-here")

    def test_empty_or_too_short(self):
        from app import _looks_like_openai_key
        assert not _looks_like_openai_key("")
        assert not _looks_like_openai_key("sk-")
        assert not _looks_like_openai_key("sk-tooshort")

    def test_missing_prefix(self):
        from app import _looks_like_openai_key
        assert not _looks_like_openai_key("abcdefghijklmnopqrstuvwxyz1234")


class TestLooksLikeDeepLKey:
    # 注: gitleaks の deepl-api-key ルール（最初の 8 文字が [0-9a-f]）を回避するため、
    # テスト値の先頭に 16 進数外の "g" を入れている。
    def test_valid_free_tier_key(self):
        from app import _looks_like_deepl_key
        # UUID + :fx 形式（35 文字以上）
        assert _looks_like_deepl_key("g1234567-test-fake-deepl-key0987654321:fx")

    def test_valid_pro_tier_key(self):
        from app import _looks_like_deepl_key
        # UUID 形式（pro は :fx なし、36 文字）
        assert _looks_like_deepl_key("g1234567-test-fake-deepl-key0987654321")

    def test_placeholder_rejected(self):
        from app import _looks_like_deepl_key
        assert not _looks_like_deepl_key("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx")
        assert not _looks_like_deepl_key("your-deepl-key-here-12345678901234")

    def test_empty_or_too_short(self):
        from app import _looks_like_deepl_key
        assert not _looks_like_deepl_key("")
        assert not _looks_like_deepl_key("short-key-12345")
