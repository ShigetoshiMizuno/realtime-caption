"""
tests/test_quota_url_button.py

issue #80 — クォータ確認 URL クリック対応。

_open_quota_usage_page() の単体テスト。
TDD: RED → GREEN の順で実装。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import _open_quota_usage_page

QUOTA_URL = "https://platform.openai.com/usage"


class TestOpenQuotaUsagePage:
    """_open_quota_usage_page が webbrowser.open を正しいURLで呼ぶこと。"""

    def test_opens_correct_url_with_default_module(self):
        """デフォルト引数なしで呼んだとき webbrowser.open が正しいURLで呼ばれること。"""
        fake_wb = MagicMock()
        _open_quota_usage_page(webbrowser_module=fake_wb)
        fake_wb.open.assert_called_once_with(QUOTA_URL)

    def test_opens_platform_openai_usage(self):
        """渡した URL が https://platform.openai.com/usage であること。"""
        fake_wb = MagicMock()
        _open_quota_usage_page(webbrowser_module=fake_wb)
        called_url = fake_wb.open.call_args[0][0]
        assert called_url == QUOTA_URL

    def test_webbrowser_module_is_optional(self):
        """webbrowser_module 引数なしで呼んでもエラーにならないこと（実際の open は呼ばない）。"""
        # webbrowser_module=None のときは本物の webbrowser を使う設計だが、
        # ここでは実際のブラウザ起動を避けるため webbrowser をモジュールレベルで差し替えて確認する。
        import unittest.mock as mock
        import webbrowser
        with mock.patch.object(webbrowser, "open") as patched:
            _open_quota_usage_page()
            patched.assert_called_once_with(QUOTA_URL)

    def test_custom_module_open_called_exactly_once(self):
        """open() が 1 回だけ呼ばれること（2 回以上は呼ばれないこと）。"""
        fake_wb = MagicMock()
        _open_quota_usage_page(webbrowser_module=fake_wb)
        assert fake_wb.open.call_count == 1
