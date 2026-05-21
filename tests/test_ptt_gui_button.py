"""
tests/test_ptt_gui_button.py

GUI PTT ボタン・ラッチ修正のテスト（issue #155 / #156 / #157）。

仕様: docs/spec-issue155-ptt-gui-button.md

テスト対象:
  - app._on_ptt_btn_pressed  : G-1.1 / G-1.4 スレッドモデル
  - app._on_ptt_btn_released : G-1.2
  - app._on_ptt_release      : G-3.4 ラッチガード
  - 系統A OFF / 系統B ON でのラッチ動作（issue #157）

設計方針:
  - dpg を実起動しない
  - app モジュールのグローバルを patch で操作
  - MultiCaptionSystem / CaptionSystem をモックで代替
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app  # noqa: E402
from main import RouteState  # noqa: E402


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_route(state: RouteState) -> MagicMock:
    m = MagicMock()
    m.state = state
    return m


def _make_konnyaku(b_state: RouteState, a_state: RouteState = RouteState.RUNNING) -> MagicMock:
    """route_a_system / route_b_system を持つ MultiCaptionSystem モックを返す。"""
    b_route = _make_route(b_state)
    a_route = _make_route(a_state)
    m = MagicMock()
    type(m).route_a_system = property(lambda self: a_route)
    type(m).route_b_system = property(lambda self: b_route)

    def _start_route(route_id):
        if route_id == "b":
            b_route.state = RouteState.RUNNING
        elif route_id == "a":
            a_route.state = RouteState.RUNNING

    m.start_route.side_effect = _start_route
    return m


def _make_konnyaku_no_route_a(b_state: RouteState) -> MagicMock:
    """route_a_system=None（系統A 無効）のモックを返す。"""
    b_route = _make_route(b_state)
    m = MagicMock()
    type(m).route_a_system = property(lambda self: None)
    type(m).route_b_system = property(lambda self: b_route)

    def _start_route(route_id):
        if route_id == "b":
            b_route.state = RouteState.RUNNING

    m.start_route.side_effect = _start_route
    return m


def _make_dpg_mock(latch_value: bool = False) -> MagicMock:
    dpg = MagicMock()
    dpg.does_item_exist.return_value = True
    dpg.get_value.return_value = latch_value
    return dpg


# ===========================================================================
# 1. _on_ptt_btn_pressed のテスト
# ===========================================================================

class TestOnPttBtnPressed:
    """G-1.1 / G-1.4: _on_ptt_btn_pressed の動作テスト。"""

    def test_pressed_when_idle_starts_route_b_in_thread(self):
        """通常押下: route B が IDLE なら start_route('b') がスレッド経由で呼ばれること（G-1.4）。"""
        system = _make_konnyaku(RouteState.IDLE)
        dpg_mock = _make_dpg_mock(latch_value=False)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue") as mock_queue, \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_btn_pressed(None, None, None)

        # スレッドが生成され、target=start_route であること
        mock_thread.assert_called_once()
        call_kwargs = mock_thread.call_args.kwargs
        assert call_kwargs.get("target") == system.start_route
        assert call_kwargs.get("args") == ("b",)
        assert call_kwargs.get("name") == "PttBtnStartRouteB"
        assert call_kwargs.get("daemon") is True
        # スレッドが start されること
        mock_thread.return_value.start.assert_called_once()

    def test_pressed_when_already_running_does_not_start(self):
        """route B が RUNNING のときは start_route が呼ばれないこと（G-1.1 step 4）。"""
        system = _make_konnyaku(RouteState.RUNNING)
        dpg_mock = _make_dpg_mock(latch_value=False)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue"), \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_btn_pressed(None, None, None)

        # スレッドが start_route で呼ばれていないこと
        for call in mock_thread.call_args_list:
            assert call.kwargs.get("target") != system.start_route, \
                "RUNNING 時に start_route がスレッドで呼ばれてはいけない"

    def test_pressed_with_latch_on_turns_off_latch_and_stops(self):
        """ラッチ ON 中にボタン押下: ラッチ OFF + stop_route がスレッドで呼ばれること（G-1.1 step 3）。"""
        system = _make_konnyaku(RouteState.RUNNING)
        dpg_mock = _make_dpg_mock(latch_value=True)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue") as mock_queue, \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_btn_pressed(None, None, None)

        # ラッチが OFF にセットされること
        dpg_mock.set_value.assert_called_with(app.TAG_PTT_LATCH_CHECK, False)
        # stop_route がスレッドで呼ばれること
        mock_thread.assert_called_once()
        call_kwargs = mock_thread.call_args.kwargs
        assert call_kwargs.get("target") == system.stop_route
        assert call_kwargs.get("args") == ("b",)

    def test_pressed_when_not_running_is_noop(self):
        """_konnyaku_running=False なら no-op（G-1.1 step 1）。"""
        system = _make_konnyaku(RouteState.IDLE)

        with patch.object(app, "_konnyaku_running", False), \
             patch.object(app, "_konnyaku_system", system), \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_btn_pressed(None, None, None)

        system.start_route.assert_not_called()
        mock_thread.assert_not_called()

    def test_pressed_ptt_enabled_false_still_works(self):
        """_ptt_enabled=False でも GUI ボタン押下が機能すること（G-1.3: F8 フラグ非依存）。"""
        system = _make_konnyaku(RouteState.IDLE)
        dpg_mock = _make_dpg_mock(latch_value=False)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_ptt_enabled", False), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue"), \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_btn_pressed(None, None, None)

        # スレッドが生成されること（_ptt_enabled は関係ない）
        mock_thread.assert_called_once()
        call_kwargs = mock_thread.call_args.kwargs
        assert call_kwargs.get("target") == system.start_route


# ===========================================================================
# 2. _on_ptt_btn_released のテスト
# ===========================================================================

class TestOnPttBtnReleased:
    """G-1.2: _on_ptt_btn_released の動作テスト。"""

    def test_released_when_running_stops_route_b(self):
        """通常離脱: route B が RUNNING なら stop_route('b') がスレッドで呼ばれること。"""
        system = _make_konnyaku(RouteState.RUNNING)
        dpg_mock = _make_dpg_mock(latch_value=False)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue"), \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_btn_released(None, None, None)

        mock_thread.assert_called_once()
        call_kwargs = mock_thread.call_args.kwargs
        assert call_kwargs.get("target") == system.stop_route
        assert call_kwargs.get("args") == ("b",)

    def test_released_with_latch_on_does_not_stop(self):
        """ラッチ ON 中は離脱しても stop_route が呼ばれないこと（G-1.2 step 3）。"""
        system = _make_konnyaku(RouteState.RUNNING)
        dpg_mock = _make_dpg_mock(latch_value=True)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue"), \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_btn_released(None, None, None)

        # stop_route を target にするスレッドが生成されていないこと
        for c in mock_thread.call_args_list:
            assert c.kwargs.get("target") != system.stop_route, \
                "ラッチ ON 中に stop_route が呼ばれてはいけない"


# ===========================================================================
# 3. _on_ptt_release にラッチガード追加のテスト（G-3.4）
# ===========================================================================

class TestOnPttReleaseWithLatchGuard:
    """G-3.4: F8 離脱時にラッチガードが機能すること（issue の修正確認）。"""

    def test_f8_release_with_latch_on_does_not_stop_route_b(self):
        """ラッチ ON 中に F8 を離しても stop_route が呼ばれないこと（G-3.4 修正確認）。"""
        system = _make_konnyaku(RouteState.RUNNING)
        dpg_mock = _make_dpg_mock(latch_value=True)

        # event はダミー
        event = MagicMock()

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue") as mock_queue, \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_release(event)

        # stop_route を target にするスレッドが生成されていないこと
        for c in mock_thread.call_args_list:
            assert c.kwargs.get("target") != system.stop_route, \
                "ラッチ ON 中に F8 離脱で stop_route が呼ばれてはいけない（G-3.4）"
        # GUI キューに update_ptt_visual が積まれること
        mock_queue.put.assert_called_with({"cmd": "update_ptt_visual"})

    def test_f8_release_without_latch_stops_route_b(self):
        """ラッチ OFF 時は F8 離脱で stop_route が呼ばれること（既存動作の維持）。"""
        system = _make_konnyaku(RouteState.RUNNING)
        dpg_mock = _make_dpg_mock(latch_value=False)
        event = MagicMock()

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_gui_queue"), \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_release(event)

        mock_thread.assert_called_once()
        call_kwargs = mock_thread.call_args.kwargs
        assert call_kwargs.get("target") == system.stop_route


# ===========================================================================
# 4. 系統A OFF / 系統B ON でのラッチ動作（issue #157）
# ===========================================================================

class TestIssuE157RouteBOnlyLatch:
    """issue #157: 系統A OFF・系統B ON 時にラッチが ON できること。"""

    def test_latch_on_with_route_a_off_calls_start_route_b(self):
        """系統A OFF（route_a_system=None）で start_route_b が呼ばれること（#157 修正確認）。"""
        # route_a_system が None のモック（系統A 無効）
        system = _make_konnyaku_no_route_a(RouteState.IDLE)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_gui_queue"):
            app._on_ptt_latch_changed(None, True, None)

        system.start_route.assert_called_once_with("b")

    def test_latch_on_with_konnyaku_running_true_and_route_b_idle(self):
        """_konnyaku_running=True かつ route_b が IDLE の状態でラッチ ON が機能すること。

        系統A ON/OFF に関わらず、_konnyaku_running=True であれば
        ラッチ ON で start_route('b') が呼ばれること。
        """
        system = _make_konnyaku(RouteState.IDLE)

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_gui_queue"):
            app._on_ptt_latch_changed(None, True, None)

        system.start_route.assert_called_once_with("b")
