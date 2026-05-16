"""
tests/test_route_tab.py

GUI 縦長解消 第3弾: 系統1 / 系統2 を dpg.tab_bar でタブ化したことを検証するテスト群。

設計方針:
- dpg を実起動せず、inspect.getsource / 定数検査で検証する
- _build_gui のソースコードに TabBar / Tab 定義が含まれることを確認
- 既存 TAG 定数が破壊されていないことを確認
- コールバック互換性（関数が存在しシグネチャが崩れていないこと）を確認
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 新規 TAG 定数が定義されていること
# ---------------------------------------------------------------------------


class TestNewTagConstants:
    """タブバー用の新規 TAG 定数が app.py に定義されていること。"""

    def test_tag_route_tab_bar_defined(self):
        """TAG_ROUTE_TAB_BAR が app に定義されていること。"""
        assert hasattr(app, "TAG_ROUTE_TAB_BAR"), (
            "TAG_ROUTE_TAB_BAR が app に定義されていない"
        )

    def test_tag_route_a_tab_defined(self):
        """TAG_ROUTE_A_TAB が app に定義されていること。"""
        assert hasattr(app, "TAG_ROUTE_A_TAB"), (
            "TAG_ROUTE_A_TAB が app に定義されていない"
        )

    def test_tag_route_b_tab_defined(self):
        """TAG_ROUTE_B_TAB が app に定義されていること。"""
        assert hasattr(app, "TAG_ROUTE_B_TAB"), (
            "TAG_ROUTE_B_TAB が app に定義されていない"
        )

    def test_tag_route_tab_bar_is_string(self):
        """TAG_ROUTE_TAB_BAR が文字列であること（dpg タグ規約）。"""
        assert isinstance(app.TAG_ROUTE_TAB_BAR, str), (
            f"TAG_ROUTE_TAB_BAR は str であるべき: {type(app.TAG_ROUTE_TAB_BAR)}"
        )

    def test_tag_route_a_tab_is_string(self):
        """TAG_ROUTE_A_TAB が文字列であること。"""
        assert isinstance(app.TAG_ROUTE_A_TAB, str), (
            f"TAG_ROUTE_A_TAB は str であるべき: {type(app.TAG_ROUTE_A_TAB)}"
        )

    def test_tag_route_b_tab_is_string(self):
        """TAG_ROUTE_B_TAB が文字列であること。"""
        assert isinstance(app.TAG_ROUTE_B_TAB, str), (
            f"TAG_ROUTE_B_TAB は str であるべき: {type(app.TAG_ROUTE_B_TAB)}"
        )


# ---------------------------------------------------------------------------
# 2. _build_gui のソースに tab_bar / tab 定義が含まれること
# ---------------------------------------------------------------------------


class TestBuildGuiTabStructure:
    """_build_gui に TabBar と Tab 定義が含まれることをソース検査で確認する。"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = inspect.getsource(app._build_gui)

    def test_tab_bar_added_in_build_gui(self):
        """_build_gui のソースに tab_bar 追加コードが含まれること。"""
        assert "tab_bar" in self.source, (
            "_build_gui に dpg.tab_bar の呼び出しが見当たらない"
        )

    def test_route_a_tab_tag_in_build_gui(self):
        """_build_gui のソースに TAG_ROUTE_A_TAB が含まれること。"""
        assert "TAG_ROUTE_A_TAB" in self.source, (
            "_build_gui に TAG_ROUTE_A_TAB が含まれていない"
        )

    def test_route_b_tab_tag_in_build_gui(self):
        """_build_gui のソースに TAG_ROUTE_B_TAB が含まれること。"""
        assert "TAG_ROUTE_B_TAB" in self.source, (
            "_build_gui に TAG_ROUTE_B_TAB が含まれていない"
        )

    def test_route_tab_bar_tag_in_build_gui(self):
        """_build_gui のソースに TAG_ROUTE_TAB_BAR が含まれること。"""
        assert "TAG_ROUTE_TAB_BAR" in self.source, (
            "_build_gui に TAG_ROUTE_TAB_BAR が含まれていない"
        )

    def test_route_a_tab_label_contains_keitou1(self):
        """系統1 タブのラベルに「系統1」「相手→自分」「聞き取り字幕」のいずれかが含まれること。"""
        has_label = (
            "系統1" in self.source
            or "相手→自分" in self.source
            or "聞き取り字幕" in self.source
        )
        assert has_label, (
            "_build_gui の系統1タブラベルに「系統1」「相手→自分」「聞き取り字幕」が含まれていない"
        )

    def test_route_b_tab_label_contains_keitou2(self):
        """系統2 タブのラベルに「系統2」「自分→相手」「同時通訳」のいずれかが含まれること。"""
        has_label = (
            "系統2" in self.source
            or "自分→相手" in self.source
            or "同時通訳" in self.source
        )
        assert has_label, (
            "_build_gui の系統2タブラベルに「系統2」「自分→相手」「同時通訳」が含まれていない"
        )


# ---------------------------------------------------------------------------
# 3. 既存 TAG 定数が破壊されていないこと（TAG 互換性）
# ---------------------------------------------------------------------------


class TestExistingTagCompatibility:
    """タブ化後も既存 TAG 定数がすべて維持されていること。"""

    EXISTING_TAGS = [
        "TAG_ROUTE_A_DEVICE_COMBO",
        "TAG_ROUTE_A_LANG_COMBO",
        "TAG_ROUTE_A_OUTPUT_ENABLE",
        "TAG_ROUTE_A_OUTPUT_DEVICE_COMBO",
        "TAG_ROUTE_A_OUTPUT_VOLUME",
        "TAG_ROUTE_A_ENABLE",
        "TAG_ROUTE_A_SOURCE_TRANSCRIPT_ENABLE",
        "TAG_ROUTE_B_DEVICE_COMBO",
        "TAG_ROUTE_B_LANG_COMBO",
        "TAG_ROUTE_B_OUTPUT_ENABLE",
        "TAG_ROUTE_B_OUTPUT_DEVICE_COMBO",
        "TAG_ROUTE_B_OUTPUT_VOLUME",
        "TAG_ROUTE_B_ENABLE",
        "TAG_ROUTE_B_SOURCE_TRANSCRIPT_ENABLE",
        "TAG_ROUTE_B_LABEL",
        "TAG_LEVEL_METER_A_IN",
        "TAG_LEVEL_METER_A_OUT",
        "TAG_LEVEL_METER_B_IN",
        "TAG_LEVEL_METER_B_OUT",
    ]

    @pytest.mark.parametrize("tag_name", EXISTING_TAGS)
    def test_existing_tag_still_defined(self, tag_name):
        """タブ化後も既存 TAG が app に定義されていること。"""
        assert hasattr(app, tag_name), (
            f"{tag_name} が app から削除されている（TAG 互換性破壊）"
        )

    @pytest.mark.parametrize("tag_name", EXISTING_TAGS)
    def test_existing_tag_is_string(self, tag_name):
        """既存 TAG の値が文字列であること。"""
        val = getattr(app, tag_name)
        assert isinstance(val, str), (
            f"{tag_name} が str でなくなっている: {type(val)}"
        )


# ---------------------------------------------------------------------------
# 4. 既存 TAG が _build_gui のソースに使用されていること
# ---------------------------------------------------------------------------


class TestExistingTagUsedInBuildGui:
    """系統 1/2 の主要 TAG が _build_gui 内で使用されていること。"""

    @pytest.fixture(autouse=True)
    def _source(self):
        self.source = inspect.getsource(app._build_gui)

    ROUTE_A_TAGS = [
        "TAG_ROUTE_A_ENABLE",
        "TAG_ROUTE_A_DEVICE_COMBO",
        "TAG_ROUTE_A_LANG_COMBO",
    ]

    ROUTE_B_TAGS = [
        "TAG_ROUTE_B_ENABLE",
        "TAG_ROUTE_B_DEVICE_COMBO",
        "TAG_ROUTE_B_LANG_COMBO",
    ]

    @pytest.mark.parametrize("tag_name", ROUTE_A_TAGS)
    def test_route_a_tag_used_in_build_gui(self, tag_name):
        """系統1の主要 TAG が _build_gui のソース内で使用されていること（タブ内配置の確認）。"""
        assert tag_name in self.source, (
            f"{tag_name} が _build_gui のソースに見当たらない"
        )

    @pytest.mark.parametrize("tag_name", ROUTE_B_TAGS)
    def test_route_b_tag_used_in_build_gui(self, tag_name):
        """系統2の主要 TAG が _build_gui のソース内で使用されていること（タブ内配置の確認）。"""
        assert tag_name in self.source, (
            f"{tag_name} が _build_gui のソースに見当たらない"
        )


# ---------------------------------------------------------------------------
# 5. コールバック互換性（タブ切替に関係するコールバックが存在すること）
# ---------------------------------------------------------------------------


class TestCallbackCompatibility:
    """タブ化後も既存コールバック関数が app に存在することを確認する。"""

    CALLBACKS = [
        "_on_route_a_device_change",
        "_on_route_a_language_change",
        "_on_route_a_output_enable_change",
        "_on_route_a_source_transcript_change",
        "_on_route_a_output_device_change",
        "_on_route_a_volume_change",
        "_on_route_b_device_change",
        "_on_route_b_language_change",
        "_on_route_b_output_enable_change",
        "_on_route_b_source_transcript_change",
        "_on_route_b_output_device_change",
        "_on_route_b_volume_change",
    ]

    @pytest.mark.parametrize("cb_name", CALLBACKS)
    def test_callback_still_exists(self, cb_name):
        """タブ化後も既存コールバック関数が app に存在すること。"""
        assert hasattr(app, cb_name), (
            f"{cb_name} が app から削除されている（コールバック互換性破壊）"
        )

    @pytest.mark.parametrize("cb_name", CALLBACKS)
    def test_callback_is_callable(self, cb_name):
        """既存コールバック関数が callable であること。"""
        cb = getattr(app, cb_name)
        assert callable(cb), (
            f"{cb_name} が callable でなくなっている"
        )
