"""
test_billing_lamp_3color.py

課金ランプ 3 色化（SPEC SEM-B5 / §20.3）の単体テスト。

テスト対象:
  - app._get_route_billing_color : audio_gate ベースの 3 色判定ヘルパー
  - app._update_billing_lamp     : 個別ランプ（A/B）が 3 色を適用すること
  - app._BILLING_ROUTE_LAMP_COLOR_RED / YELLOW / GREEN 定数の存在

仕様:
  | 状態条件                                  | ランプ色 | 返り値   |
  |-------------------------------------------|---------|---------|
  | audio_gate=True                           | 赤      | "red"   |
  | state in (STARTING, RUNNING) + gate=False | 黄      | "yellow"|
  | state=IDLE (その他)                       | 緑      | "green" |

設計方針:
  - dpg を実起動しない
  - app モジュールのグローバルを patch で操作
  - MagicMock で route オブジェクトを差し替え
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app  # noqa: E402
from main import RouteState  # noqa: E402


# ---------------------------------------------------------------------------
# ヘルパー: モック系統を作るファクトリ
# ---------------------------------------------------------------------------

def _make_route(state: RouteState, audio_gate: bool = False) -> MagicMock:
    """指定した RouteState と audio_gate を持つ CaptionSystem モックを返す。"""
    m = MagicMock()
    m.state = state
    type(m).audio_gate_open = PropertyMock(return_value=audio_gate)
    return m


def _make_konnyaku(a_route, b_route) -> MagicMock:
    """MultiCaptionSystem モックを返す。None を渡すと系統なし（None）扱い。"""
    m = MagicMock()
    type(m).route_a_system = property(lambda self: a_route)
    type(m).route_b_system = property(lambda self: b_route)
    return m


# ===========================================================================
# 1. _get_route_billing_color の単体テスト
# ===========================================================================

class TestGetRouteBillingColor:
    """_get_route_billing_color の 3 色判定テスト。"""

    # --- system=None / route=None のフォールバック ---

    def test_system_none_returns_green(self):
        """_konnyaku_system が None のとき "green" を返すこと。"""
        with patch.object(app, "_konnyaku_system", None):
            assert app._get_route_billing_color("a") == "green"

    def test_system_none_route_b_returns_green(self):
        """_konnyaku_system が None のとき route_id="b" でも "green" を返すこと。"""
        with patch.object(app, "_konnyaku_system", None):
            assert app._get_route_billing_color("b") == "green"

    def test_route_none_returns_green(self):
        """route が None（未設定）のとき "green" を返すこと。"""
        system = _make_konnyaku(None, None)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "green"

    def test_route_b_none_returns_green(self):
        """route_b が None のとき "green" を返すこと。"""
        system = _make_konnyaku(_make_route(RouteState.IDLE), None)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("b") == "green"

    # --- audio_gate=True → 赤 ---

    def test_red_when_running_and_gate_open_route_a(self):
        """系統A: state=RUNNING + audio_gate=True のとき "red" を返すこと。"""
        route = _make_route(RouteState.RUNNING, audio_gate=True)
        system = _make_konnyaku(route, _make_route(RouteState.IDLE))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "red"

    def test_red_when_starting_and_gate_open_route_a(self):
        """系統A: state=STARTING + audio_gate=True のとき "red" を返すこと。"""
        route = _make_route(RouteState.STARTING, audio_gate=True)
        system = _make_konnyaku(route, _make_route(RouteState.IDLE))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "red"

    def test_red_when_running_and_gate_open_route_b(self):
        """系統B: state=RUNNING + audio_gate=True のとき "red" を返すこと。"""
        route = _make_route(RouteState.RUNNING, audio_gate=True)
        system = _make_konnyaku(_make_route(RouteState.IDLE), route)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("b") == "red"

    def test_red_when_starting_and_gate_open_route_b(self):
        """系統B: state=STARTING + audio_gate=True のとき "red" を返すこと。"""
        route = _make_route(RouteState.STARTING, audio_gate=True)
        system = _make_konnyaku(_make_route(RouteState.IDLE), route)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("b") == "red"

    def test_red_when_idle_but_gate_open_route_a(self):
        """系統A: state=IDLE でも audio_gate=True なら "red" を返すこと（ゲート優先）。"""
        route = _make_route(RouteState.IDLE, audio_gate=True)
        system = _make_konnyaku(route, _make_route(RouteState.IDLE))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "red"

    # --- state in (STARTING, RUNNING) + gate=False → 黄 ---

    def test_yellow_when_running_and_gate_closed_route_a(self):
        """系統A: state=RUNNING + audio_gate=False のとき "yellow" を返すこと。"""
        route = _make_route(RouteState.RUNNING, audio_gate=False)
        system = _make_konnyaku(route, _make_route(RouteState.IDLE))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "yellow"

    def test_yellow_when_starting_and_gate_closed_route_a(self):
        """系統A: state=STARTING + audio_gate=False のとき "yellow" を返すこと。"""
        route = _make_route(RouteState.STARTING, audio_gate=False)
        system = _make_konnyaku(route, _make_route(RouteState.IDLE))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "yellow"

    def test_yellow_when_running_and_gate_closed_route_b(self):
        """系統B: state=RUNNING + audio_gate=False のとき "yellow" を返すこと。"""
        route = _make_route(RouteState.RUNNING, audio_gate=False)
        system = _make_konnyaku(_make_route(RouteState.IDLE), route)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("b") == "yellow"

    def test_yellow_when_starting_and_gate_closed_route_b(self):
        """系統B: state=STARTING + audio_gate=False のとき "yellow" を返すこと。"""
        route = _make_route(RouteState.STARTING, audio_gate=False)
        system = _make_konnyaku(_make_route(RouteState.IDLE), route)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("b") == "yellow"

    # --- state=IDLE → 緑 ---

    def test_green_when_idle_route_a(self):
        """系統A: state=IDLE + audio_gate=False のとき "green" を返すこと。"""
        route = _make_route(RouteState.IDLE, audio_gate=False)
        system = _make_konnyaku(route, _make_route(RouteState.RUNNING, audio_gate=True))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "green"

    def test_green_when_idle_route_b(self):
        """系統B: state=IDLE + audio_gate=False のとき "green" を返すこと。"""
        route = _make_route(RouteState.IDLE, audio_gate=False)
        system = _make_konnyaku(_make_route(RouteState.RUNNING, audio_gate=True), route)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("b") == "green"

    def test_green_when_error_route_a(self):
        """系統A: state=ERROR + audio_gate=False のとき "green" を返すこと（停止扱い）。"""
        route = _make_route(RouteState.ERROR, audio_gate=False)
        system = _make_konnyaku(route, _make_route(RouteState.IDLE))
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("a") == "green"

    def test_green_when_stopping_route_b(self):
        """系統B: state=STOPPING + audio_gate=False のとき "green" を返すこと（停止扱い）。"""
        route = _make_route(RouteState.STOPPING, audio_gate=False)
        system = _make_konnyaku(_make_route(RouteState.IDLE), route)
        with patch.object(app, "_konnyaku_system", system):
            assert app._get_route_billing_color("b") == "green"


# ===========================================================================
# 2. 色定数の存在確認
# ===========================================================================

class TestBillingRouteLampColorConstants:
    """3 色の色定数が app モジュールに定義されていること。"""

    def test_color_red_exists_and_is_correct(self):
        """_BILLING_ROUTE_LAMP_COLOR_RED が (220, 0, 0, 255) であること。"""
        assert hasattr(app, "_BILLING_ROUTE_LAMP_COLOR_RED"), \
            "app._BILLING_ROUTE_LAMP_COLOR_RED が定義されていない"
        assert app._BILLING_ROUTE_LAMP_COLOR_RED == (220, 0, 0, 255)

    def test_color_yellow_exists_and_is_correct(self):
        """_BILLING_ROUTE_LAMP_COLOR_YELLOW が (255, 200, 0, 255) であること。"""
        assert hasattr(app, "_BILLING_ROUTE_LAMP_COLOR_YELLOW"), \
            "app._BILLING_ROUTE_LAMP_COLOR_YELLOW が定義されていない"
        assert app._BILLING_ROUTE_LAMP_COLOR_YELLOW == (255, 200, 0, 255)

    def test_color_green_exists_and_is_correct(self):
        """_BILLING_ROUTE_LAMP_COLOR_GREEN が (0, 200, 0, 255) であること。"""
        assert hasattr(app, "_BILLING_ROUTE_LAMP_COLOR_GREEN"), \
            "app._BILLING_ROUTE_LAMP_COLOR_GREEN が定義されていない"
        assert app._BILLING_ROUTE_LAMP_COLOR_GREEN == (0, 200, 0, 255)


# ===========================================================================
# 3. _update_billing_lamp が 3 色ランプを適用すること
# ===========================================================================

class TestUpdateBillingLamp3Color:
    """_update_billing_lamp が audio_gate ベースの 3 色を A/B 個別ランプに適用すること。"""

    def _make_dpg_mock(self, item_exists: bool = True) -> MagicMock:
        mock = MagicMock()
        mock.does_item_exist.return_value = item_exists
        mock.get_value.return_value = "● 課金なし"
        return mock

    def test_route_a_gate_open_lamp_a_is_red(self):
        """系統A audio_gate=True のとき TAG_BILLING_LAMP_A が赤になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.RUNNING, audio_gate=True)
        route_b = _make_route(RouteState.IDLE, audio_gate=False)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_A, color=(220, 0, 0, 255)
        )

    def test_route_b_gate_open_lamp_b_is_red(self):
        """系統B audio_gate=True のとき TAG_BILLING_LAMP_B が赤になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.IDLE, audio_gate=False)
        route_b = _make_route(RouteState.RUNNING, audio_gate=True)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_B, color=(220, 0, 0, 255)
        )

    def test_route_a_running_gate_closed_lamp_a_is_yellow(self):
        """系統A: RUNNING + gate=False のとき TAG_BILLING_LAMP_A が黄になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.RUNNING, audio_gate=False)
        route_b = _make_route(RouteState.IDLE, audio_gate=False)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_A, color=(255, 200, 0, 255)
        )

    def test_route_b_starting_gate_closed_lamp_b_is_yellow(self):
        """系統B: STARTING + gate=False のとき TAG_BILLING_LAMP_B が黄になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.IDLE, audio_gate=False)
        route_b = _make_route(RouteState.STARTING, audio_gate=False)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_B, color=(255, 200, 0, 255)
        )

    def test_route_a_idle_lamp_a_is_green(self):
        """系統A: IDLE のとき TAG_BILLING_LAMP_A が緑になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.IDLE, audio_gate=False)
        route_b = _make_route(RouteState.IDLE, audio_gate=False)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_A, color=(0, 200, 0, 255)
        )

    def test_route_b_idle_lamp_b_is_green(self):
        """系統B: IDLE のとき TAG_BILLING_LAMP_B が緑になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.IDLE, audio_gate=False)
        route_b = _make_route(RouteState.IDLE, audio_gate=False)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_B, color=(0, 200, 0, 255)
        )

    def test_both_gates_open_both_lamps_are_red(self):
        """A/B 両方 audio_gate=True のとき A/B 両ランプが赤になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.RUNNING, audio_gate=True)
        route_b = _make_route(RouteState.RUNNING, audio_gate=True)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_A, color=(220, 0, 0, 255)
        )
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_B, color=(220, 0, 0, 255)
        )

    def test_a_gate_open_b_running_gate_closed(self):
        """系統A gate=True（赤）、系統B RUNNING+gate=False（黄）になること。"""
        dpg_mock = self._make_dpg_mock()
        route_a = _make_route(RouteState.RUNNING, audio_gate=True)
        route_b = _make_route(RouteState.RUNNING, audio_gate=False)
        system = _make_konnyaku(route_a, route_b)
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_A, color=(220, 0, 0, 255)
        )
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_B, color=(255, 200, 0, 255)
        )

    def test_system_none_both_lamps_are_green(self):
        """system=None のとき A/B 両ランプが緑になること。"""
        dpg_mock = self._make_dpg_mock()
        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", None):
            app._update_billing_lamp()
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_A, color=(0, 200, 0, 255)
        )
        dpg_mock.configure_item.assert_any_call(
            app.TAG_BILLING_LAMP_B, color=(0, 200, 0, 255)
        )
