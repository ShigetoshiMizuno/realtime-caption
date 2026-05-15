"""
test_billing_lamp.py

課金状態ランプ表示（issue #99）の単体テスト。

テスト対象:
  - app._get_billing_state  : 課金判定ヘルパー
  - app._update_billing_lamp: ラベル更新関数
  - app._build_gui          : TAG_BILLING_LAMP ウィジェットが追加されること
  - _update_konnyaku_level_meters 呼び出しと _update_billing_lamp の結線

設計方針:
  - dpg を実起動しない
  - app モジュールのグローバルを patch で操作
  - MultiCaptionSystem / CaptionSystem をモックで代替
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app  # noqa: E402
from main import RouteState  # noqa: E402


# ---------------------------------------------------------------------------
# ヘルパー: モック系統を作るファクトリ
# ---------------------------------------------------------------------------

def _make_route(state: RouteState) -> MagicMock:
    """指定した RouteState を持つ CaptionSystem モックを返す。"""
    m = MagicMock()
    m.state = state
    return m


def _make_konnyaku(a_route, b_route) -> MagicMock:
    """MultiCaptionSystem モックを返す。None を渡すと系統なし（None）扱い。"""
    m = MagicMock()
    # property は type-level の mock が必要なので type(m).route_a_system を使う
    type(m).route_a_system = property(lambda self: a_route)
    type(m).route_b_system = property(lambda self: b_route)
    return m


# ===========================================================================
# 1. _get_billing_state の単体テスト
# ===========================================================================

class TestGetBillingState:
    """_get_billing_state の状態遷移テスト。"""

    def test_system_none_returns_none(self):
        """_konnyaku_system が None のとき "none" を返すこと。"""
        with patch.object(app, "_konnyaku_system", None):
            assert app._get_billing_state() == "none"

    def test_both_idle_returns_none(self):
        """両系統 IDLE のとき "none" を返すこと。"""
        system = _make_konnyaku(
            _make_route(RouteState.IDLE),
            _make_route(RouteState.IDLE),
        )
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "none"

    def test_route_a_running_b_idle_returns_single(self):
        """系統A RUNNING、系統B IDLE のとき "single" を返すこと。"""
        system = _make_konnyaku(
            _make_route(RouteState.RUNNING),
            _make_route(RouteState.IDLE),
        )
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "single"

    def test_route_a_idle_b_starting_returns_single(self):
        """系統A IDLE、系統B STARTING のとき "single" を返すこと。"""
        system = _make_konnyaku(
            _make_route(RouteState.IDLE),
            _make_route(RouteState.STARTING),
        )
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "single"

    def test_both_running_returns_both(self):
        """両系統 RUNNING のとき "both" を返すこと。"""
        system = _make_konnyaku(
            _make_route(RouteState.RUNNING),
            _make_route(RouteState.RUNNING),
        )
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "both"

    def test_a_starting_b_running_returns_both(self):
        """系統A STARTING、系統B RUNNING のとき "both" を返すこと。"""
        system = _make_konnyaku(
            _make_route(RouteState.STARTING),
            _make_route(RouteState.RUNNING),
        )
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "both"

    def test_error_state_returns_none(self):
        """両系統 ERROR 状態のとき "none" を返すこと（課金なし扱い）。"""
        system = _make_konnyaku(
            _make_route(RouteState.ERROR),
            _make_route(RouteState.ERROR),
        )
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "none"

    def test_stopping_state_returns_none(self):
        """両系統 STOPPING 状態のとき "none" を返すこと（課金なし扱い）。"""
        system = _make_konnyaku(
            _make_route(RouteState.STOPPING),
            _make_route(RouteState.STOPPING),
        )
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "none"

    def test_route_a_none_b_running_returns_single(self):
        """系統A が None（未設定）で系統B RUNNING のとき "single" を返すこと。"""
        system = _make_konnyaku(None, _make_route(RouteState.RUNNING))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_billing_state() == "single"


# ===========================================================================
# 2. _update_billing_lamp のテスト
# ===========================================================================

class TestUpdateBillingLamp:
    """_update_billing_lamp のテスト。"""

    def _make_dpg_mock(self, item_exists: bool = True) -> MagicMock:
        """dpg モックを返す。"""
        mock = MagicMock()
        mock.does_item_exist.return_value = item_exists
        mock.get_value.return_value = "● 課金なし"
        return mock

    def test_dpg_not_ready_is_noop(self):
        """_dpg_ready が False のとき dpg.set_value を呼ばないこと。"""
        dpg_mock = self._make_dpg_mock()
        with patch.object(app, "_dpg_ready", False), \
             patch.object(app, "dpg", dpg_mock):
            app._update_billing_lamp()
        dpg_mock.set_value.assert_not_called()

    def test_widget_not_exists_is_noop(self):
        """TAG_BILLING_LAMP ウィジェットが存在しないとき dpg.set_value を呼ばないこと。"""
        dpg_mock = self._make_dpg_mock(item_exists=False)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", None):
            app._update_billing_lamp()
        dpg_mock.set_value.assert_not_called()

    def test_none_state_sets_green_label(self):
        """課金なし（"none"）のとき "● 課金なし" がセットされること。"""
        dpg_mock = self._make_dpg_mock()
        dpg_mock.get_value.return_value = "● 両方課金"  # 既存値が違う
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", None):
            app._update_billing_lamp()
        dpg_mock.set_value.assert_called_once_with(app.TAG_BILLING_LAMP, "● 課金なし")

    def test_single_state_sets_yellow_label(self):
        """片方課金（"single"）のとき "● 片方課金" がセットされること。"""
        dpg_mock = self._make_dpg_mock()
        dpg_mock.get_value.return_value = "● 課金なし"
        system = _make_konnyaku(
            _make_route(RouteState.RUNNING),
            _make_route(RouteState.IDLE),
        )
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.set_value.assert_called_once_with(app.TAG_BILLING_LAMP, "● 片方課金")

    def test_both_state_sets_red_label(self):
        """両方課金（"both"）のとき "● 両方課金" がセットされること。"""
        dpg_mock = self._make_dpg_mock()
        dpg_mock.get_value.return_value = "● 課金なし"
        system = _make_konnyaku(
            _make_route(RouteState.RUNNING),
            _make_route(RouteState.RUNNING),
        )
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.set_value.assert_called_once_with(app.TAG_BILLING_LAMP, "● 両方課金")

    def test_none_state_sets_green_color(self):
        """課金なし（"none"）のとき configure_item で緑色 (0, 200, 0, 255) がセットされること。"""
        dpg_mock = self._make_dpg_mock()
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", None):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_called_once_with(
            app.TAG_BILLING_LAMP, color=(0, 200, 0, 255)
        )

    def test_single_state_sets_yellow_color(self):
        """片方課金（"single"）のとき configure_item で黄色 (255, 200, 0, 255) がセットされること。"""
        dpg_mock = self._make_dpg_mock()
        system = _make_konnyaku(
            _make_route(RouteState.RUNNING),
            _make_route(RouteState.IDLE),
        )
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_called_once_with(
            app.TAG_BILLING_LAMP, color=(255, 200, 0, 255)
        )

    def test_both_state_sets_red_color(self):
        """両方課金（"both"）のとき configure_item で赤色 (220, 0, 0, 255) がセットされること。"""
        dpg_mock = self._make_dpg_mock()
        system = _make_konnyaku(
            _make_route(RouteState.RUNNING),
            _make_route(RouteState.RUNNING),
        )
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_called_once_with(
            app.TAG_BILLING_LAMP, color=(220, 0, 0, 255)
        )


# ===========================================================================
# 3. GUI 配置テスト: _build_gui に TAG_BILLING_LAMP が含まれること
# ===========================================================================

class TestBuildGuiHasBillingLamp:
    """_build_gui のソースコードに TAG_BILLING_LAMP の登録が含まれることを確認。"""

    def test_tag_billing_lamp_constant_exists(self):
        """TAG_BILLING_LAMP 定数が app モジュールに存在すること。"""
        assert hasattr(app, "TAG_BILLING_LAMP"), "app.TAG_BILLING_LAMP が定義されていない"

    def test_build_gui_adds_billing_lamp_widget(self):
        """_build_gui のソースに TAG_BILLING_LAMP ウィジェット追加コードが含まれること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        assert "TAG_BILLING_LAMP" in source, (
            "_build_gui に TAG_BILLING_LAMP のウィジェット追加コードがない"
        )


# ===========================================================================
# 4. レンダリングループ結線テスト
# ===========================================================================

class TestRenderingLoopWiring:
    """_update_konnyaku_level_meters から _update_billing_lamp が呼ばれること。"""

    def test_update_billing_lamp_called_from_level_meters(self):
        """_update_konnyaku_level_meters 呼び出し時に _update_billing_lamp も呼ばれること。

        _update_konnyaku_level_meters の末尾（または呼び出し元）で
        _update_billing_lamp() が呼ばれる設計を確認する。
        """
        import inspect
        source = inspect.getsource(app._update_konnyaku_level_meters)
        assert "_update_billing_lamp" in source, (
            "_update_konnyaku_level_meters の中で _update_billing_lamp() が呼ばれていない"
        )

    def test_billing_lamp_updates_even_when_konnyaku_system_is_none(self):
        """_konnyaku_system = None の状態で _update_konnyaku_level_meters() を呼ぶと
        _update_billing_lamp() が呼ばれること。（Issue #99 QA 仕切り直し W-1）

        早期リターン（_konnyaku_system is None）の前に _update_billing_lamp() が
        配置されていることを実際の呼び出しで確認する。
        """
        called = []

        with patch.object(app, "_konnyaku_system", None), \
             patch.object(app, "_update_billing_lamp", side_effect=lambda: called.append(True)):
            app._update_konnyaku_level_meters()

        assert len(called) == 1, (
            "_konnyaku_system=None のとき _update_billing_lamp() が 1 回呼ばれること。"
            f"実際の呼び出し回数: {len(called)}"
        )
