"""言語テーブル (constants.py) のユニットテスト。

仕様書: docs/spec/issue-38-translation-konnyaku.md §3, §6
"""

from constants import (
    SUPPORTED_LANGUAGES,
    get_language_display_name,
    get_language_codes,
    get_language_display_names,
)


def test_get_language_display_name_ja():
    """get_language_display_name("ja") が "日本語" を返すこと。"""
    assert get_language_display_name("ja") == "日本語"


def test_get_language_display_name_en():
    """get_language_display_name("en") が "English" を返すこと。"""
    assert get_language_display_name("en") == "English"


def test_get_language_display_name_unknown_returns_code():
    """未定義コード（例: "xx"）はコード文字列をそのまま返すこと。"""
    assert get_language_display_name("xx") == "xx"


def test_supported_languages_contains_ja_and_en():
    """SUPPORTED_LANGUAGES に ja と en の両方が含まれること。"""
    codes = [code for code, _ in SUPPORTED_LANGUAGES]
    assert "ja" in codes
    assert "en" in codes


def test_get_language_codes_returns_list():
    """get_language_codes() が ["ja", "en", ...] のリストを返すこと。"""
    codes = get_language_codes()
    assert isinstance(codes, list)
    assert "ja" in codes
    assert "en" in codes


def test_get_language_display_names_returns_list():
    """get_language_display_names() が ["日本語", "English", ...] のリストを返すこと。"""
    names = get_language_display_names()
    assert isinstance(names, list)
    assert "日本語" in names
    assert "English" in names


def test_get_language_codes_and_names_same_length():
    """codes と display_names が同じ長さで対応していること。"""
    codes = get_language_codes()
    names = get_language_display_names()
    assert len(codes) == len(names)
    assert len(codes) > 0
