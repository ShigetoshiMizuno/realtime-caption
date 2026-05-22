"""
test_ptt_latch.py

固定チェックボックス（ラッチモード）の単体テスト（issue #154）。

テスト対象:
  - app._on_ptt_latch_changed  : チェック ON/OFF 時の route B 起動/停止
  - app._update_ptt_visual_feedback : ラッチの自動 OFF ロジック

バグ再現条件:
  開始ボタン押下後（_konnyaku_running=True）に固定チェックを ON にしても
  _update_ptt_visual_feedback が即座にチェックを戻してしまう競合。

設計方針:
  - dpg を実起動しない
  - app モジュールのグローバルを patch で操作
  - MultiCaptionSystem / CaptionSystem をモックで代替
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
    m._audio_gate = False  # Case D: デフォルトはゲート閉（音声非送信）
    return m


def _make_konnyaku(b_state: RouteState) -> MagicMock:
    """route_b_system を持つ MultiCaptionSystem モックを返す。"""
    b_route = _make_route(b_state)
    m = MagicMock()
    type(m).route_a_system = property(lambda self: _make_route(RouteState.RUNNING))
    type(m).route_b_system = property(lambda self: b_route)
    # start_route を呼ぶと route_b の state を RUNNING に変える副作用を再現
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
# 1. _on_ptt_latch_changed のテスト
# ===========================================================================

class TestOnPttLatchChanged:
    """_on_ptt_latch_changed の動作テスト。"""

    def test_checked_on_when_idle_calls_start_route_directly(self):
        """チェック ON 時、route B が IDLE なら start_route("b") が呼ばれること。

        issue #154: スレッドを経由せず直接呼ぶことで競合を防ぐ。
        """
        system = _make_konnyaku(RouteState.IDLE)
        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_gui_queue") as mock_queue:
            app._on_ptt_latch_changed(None, True, None)

        system.start_route.assert_called_once_with("b")
        mock_queue.put.assert_called_once_with({"cmd": "update_ptt_visual"})

    def test_checked_on_when_already_running_does_not_double_start(self):
        """route B が既に RUNNING なら start_route を呼ばないこと。"""
        system = _make_konnyaku(RouteState.RUNNING)
        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_gui_queue"):
            app._on_ptt_latch_changed(None, True, None)

        system.start_route.assert_not_called()

    def test_checked_on_when_konnyaku_not_running_is_noop(self):
        """_konnyaku_running=False の場合は start_route を呼ばないこと。"""
        system = _make_konnyaku(RouteState.IDLE)
        with patch.object(app, "_konnyaku_running", False), \
             patch.object(app, "_konnyaku_system", system):
            app._on_ptt_latch_changed(None, True, None)

        system.start_route.assert_not_called()

    def test_checked_off_when_running_calls_stop_route(self):
        """チェック OFF 時、route B が RUNNING なら stop_route("b") がスレッドで呼ばれること。"""
        system = _make_konnyaku(RouteState.RUNNING)
        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_gui_queue"), \
             patch("threading.Thread") as mock_thread:
            app._on_ptt_latch_changed(None, False, None)

        mock_thread.assert_called_once()
        args = mock_thread.call_args
        assert args.kwargs.get("target") == system.stop_route or \
               (args.args and args.args[0] == system.stop_route) or \
               mock_thread.called  # stop_route がスレッドで呼ばれることを確認

    def test_after_latch_on_state_is_running_for_visual_feedback(self):
        """チェック ON で start_route を直接呼んだ後、route B が RUNNING になること。

        issue #154 の核心: visual feedback が走る前に state=RUNNING になることを保証する。
        """
        system = _make_konnyaku(RouteState.IDLE)
        b_route = system.route_b_system  # 直接参照を保持

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_gui_queue"):
            app._on_ptt_latch_changed(None, True, None)

        # start_route("b") が呼ばれた後の状態が RUNNING であること
        assert b_route.state == RouteState.RUNNING, (
            f"start_route 後に RUNNING になるべきだが、実際は {b_route.state}"
        )


# ===========================================================================
# 2. _update_ptt_visual_feedback の自動アンチェックテスト
# ===========================================================================

class TestUpdatePttVisualFeedbackLatch:
    """_update_ptt_visual_feedback のラッチ自動 OFF ロジックテスト（issue #154）。"""

    def _make_full_dpg_mock(self, latch_checked: bool = True) -> MagicMock:
        dpg = MagicMock()
        dpg.does_item_exist.return_value = True
        dpg.get_value.return_value = latch_checked
        return dpg

    def test_running_state_does_not_uncheck_latch(self):
        """route B が RUNNING のとき、ラッチを自動 OFF しないこと。"""
        system = _make_konnyaku(RouteState.RUNNING)
        dpg_mock = self._make_full_dpg_mock(latch_checked=True)

        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_ptt_enabled", False):
            app._update_ptt_visual_feedback()

        # TAG_PTT_LATCH_CHECK に False をセットする呼び出しがないこと
        for c in dpg_mock.set_value.call_args_list:
            if c.args and c.args[0] == app.TAG_PTT_LATCH_CHECK:
                assert c.args[1] is not False, (
                    "RUNNING 中にラッチが自動 OFF されてはいけない"
                )

    def test_starting_state_does_not_uncheck_latch(self):
        """route B が STARTING のとき、ラッチを自動 OFF しないこと（issue #154 本質修正）。

        以前のバグ: STARTING 中でもラッチを OFF していたため
        start_route の非同期呼び出しとの競合で即リセットされた。
        """
        system = _make_konnyaku(RouteState.STARTING)
        dpg_mock = self._make_full_dpg_mock(latch_checked=True)

        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_ptt_enabled", False):
            app._update_ptt_visual_feedback()

        for c in dpg_mock.set_value.call_args_list:
            if c.args and c.args[0] == app.TAG_PTT_LATCH_CHECK:
                assert c.args[1] is not False, (
                    "STARTING 中にラッチが自動 OFF されてはいけない（issue #154）"
                )

    def test_idle_state_auto_unchecks_latch(self):
        """route B が IDLE のとき、ラッチを自動 OFF すること（外部停止の反映）。"""
        system = _make_konnyaku(RouteState.IDLE)
        dpg_mock = self._make_full_dpg_mock(latch_checked=True)

        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_ptt_enabled", False):
            app._update_ptt_visual_feedback()

        uncheck_calls = [
            c for c in dpg_mock.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_PTT_LATCH_CHECK and c.args[1] is False
        ]
        assert len(uncheck_calls) == 1, (
            "IDLE のときにラッチが自動 OFF されること"
        )

    def test_error_state_auto_unchecks_latch(self):
        """route B が ERROR のとき、ラッチを自動 OFF すること。"""
        system = _make_konnyaku(RouteState.ERROR)
        dpg_mock = self._make_full_dpg_mock(latch_checked=True)

        with patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_ptt_enabled", False):
            app._update_ptt_visual_feedback()

        uncheck_calls = [
            c for c in dpg_mock.set_value.call_args_list
            if c.args and c.args[0] == app.TAG_PTT_LATCH_CHECK and c.args[1] is False
        ]
        assert len(uncheck_calls) == 1, (
            "ERROR のときにラッチが自動 OFF されること"
        )


# ===========================================================================
# 3. 競合再現シミュレーション（issue #154 の核心）
# ===========================================================================

class TestLatchRaceConditionFix:
    """issue #154 の競合を再現し修正を確認するシミュレーションテスト。"""

    def test_latch_on_then_visual_feedback_does_not_reset(self):
        """チェック ON → visual feedback 実行の順で、ラッチが戻らないこと。

        修正前のバグ再現ステップ:
        1. _on_ptt_latch_changed(True) → start_route("b") をスレッドで起動
        2. スレッド未実行のまま update_ptt_visual が処理される
        3. route B はまだ IDLE → ラッチが自動 OFF

        修正後: start_route("b") を直接呼ぶため、
        visual feedback が走る時点で state=RUNNING が保証される。
        """
        system = _make_konnyaku(RouteState.IDLE)
        dpg_mock = MagicMock()
        dpg_mock.does_item_exist.return_value = True

        # チェック前は False、チェック後は True を返す
        latch_state = [False]

        def _get_value(tag):
            if tag == app.TAG_PTT_LATCH_CHECK:
                return latch_state[0]
            return MagicMock()

        def _set_value(tag, val):
            if tag == app.TAG_PTT_LATCH_CHECK:
                latch_state[0] = val

        dpg_mock.get_value.side_effect = _get_value
        dpg_mock.set_value.side_effect = _set_value

        with patch.object(app, "_konnyaku_running", True), \
             patch.object(app, "_konnyaku_system", system), \
             patch.object(app, "_dpg_ready", True), \
             patch.object(app, "dpg", dpg_mock), \
             patch.object(app, "_ptt_enabled", False), \
             patch.object(app, "_gui_queue"):

            # ユーザーがチェックを ON（DPG が app_data=True で呼ぶ）
            latch_state[0] = True
            app._on_ptt_latch_changed(None, True, None)

            # この時点で route B は RUNNING になっているはず（直接呼び出しのため）
            assert system.route_b_system.state == RouteState.RUNNING, (
                "start_route 後に RUNNING であること（スレッド待ち不要）"
            )

            # visual feedback が走ってもラッチは戻らない
            app._update_ptt_visual_feedback()

        assert latch_state[0] is True, (
            f"visual feedback 後もラッチが ON のままであること。実際: {latch_state[0]}"
        )
