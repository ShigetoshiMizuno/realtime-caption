"""
tests/test_restart_route_helper.py

refactor/restart-route-helper:
- _restart_route_for_change(route_id, reason_label) 共通ヘルパー化
- 連打防御（_restart_locks による per-系統 排他制御）
- W-COST-2 稼働中切替: _on_route_a/b_source_transcript_change で即時再起動

テスト対象:
1. _restart_route_for_change が stop_route -> start_route を呼ぶこと
2. reason_label がステータスバーに表示されること
3. 連打防御: 同じ系統に対して複数回呼ばれても1回しか実行されないこと
4. 連打防御スキップ時にステータスバーにスキップ通知が出ること
5. W-COST-2: 稼働中に原文表示を切り替えると stop_route -> start_route が呼ばれること
6. W-COST-2: 停止中では再起動が発生しないこと
"""

import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

import app
from main import RouteState


# ---------------------------------------------------------------------------
# ヘルパースタブ
# ---------------------------------------------------------------------------

class FakeRouteSystem:
    """CaptionSystem の最小スタブ。"""

    def __init__(self, state: RouteState = RouteState.IDLE):
        self._state = state
        self.set_output_device = MagicMock()
        self.update_output_config = MagicMock()

    @property
    def state(self) -> RouteState:
        return self._state


class FakeMultiCaptionSystem:
    """MultiCaptionSystem の最小スタブ。"""

    def __init__(self, route_a_state=RouteState.IDLE, route_b_state=RouteState.IDLE):
        self.route_a_system = FakeRouteSystem(route_a_state)
        self.route_b_system = FakeRouteSystem(route_b_state)
        self.stop_route = MagicMock()
        self.start_route = MagicMock()


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_app_state():
    """各テスト前後に app モジュールのグローバル状態をリセットする。"""
    old_system = app._konnyaku_system
    old_running = app._konnyaku_running
    # _restart_locks を再生成してクリーンな状態にする
    app._restart_locks = {"a": threading.Lock(), "b": threading.Lock()}

    yield

    app._konnyaku_system = old_system
    app._konnyaku_running = old_running
    app._restart_locks = {"a": threading.Lock(), "b": threading.Lock()}


# ---------------------------------------------------------------------------
# 1. _restart_route_for_change: 基本動作
# ---------------------------------------------------------------------------

class TestRestartRouteForChangeBasic:
    """_restart_route_for_change の基本動作テスト。"""

    def test_calls_stop_then_start_route_a(self):
        """route_id='a' で stop_route('a') -> start_route('a') が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        app._restart_route_for_change("a", "テスト切替")

        fake_system.stop_route.assert_called_once_with("a")
        fake_system.start_route.assert_called_once_with("a")

    def test_calls_stop_then_start_route_b(self):
        """route_id='b' で stop_route('b') -> start_route('b') が呼ばれること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        app._restart_route_for_change("b", "テスト切替")

        fake_system.stop_route.assert_called_once_with("b")
        fake_system.start_route.assert_called_once_with("b")

    def test_stop_is_called_before_start(self):
        """stop_route が start_route より先に呼ばれること（順序チェック）。"""
        call_order = []
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        fake_system.stop_route.side_effect = lambda rid: call_order.append(f"stop_{rid}")
        fake_system.start_route.side_effect = lambda rid: call_order.append(f"start_{rid}")
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        app._restart_route_for_change("a", "テスト切替")

        assert call_order == ["stop_a", "start_a"], f"順序が違う: {call_order}"

    def test_noop_when_system_is_none(self):
        """_konnyaku_system=None のとき例外なく終了すること。"""
        app._konnyaku_system = None
        # 例外が発生しなければ OK
        app._restart_route_for_change("a", "テスト切替")

    def test_gui_queue_receives_reset_after_restart(self):
        """再起動後に _gui_queue に set_status '' が入ること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        import queue as _queue
        original_queue = app._gui_queue
        fake_queue = _queue.Queue()
        app._gui_queue = fake_queue

        try:
            app._restart_route_for_change("a", "テスト切替")
            item = fake_queue.get_nowait()
            assert item == {"cmd": "set_status", "text": ""}, f"queue に期待値なし: {item}"
        finally:
            app._gui_queue = original_queue


# ---------------------------------------------------------------------------
# 2. reason_label のステータスバー表示
# ---------------------------------------------------------------------------

class TestRestartRouteForChangeReasonLabel:
    """reason_label がログ出力に含まれること。"""

    def test_reason_label_appears_in_print(self, capsys):
        """reason_label が標準出力に表示されること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        app._restart_route_for_change("a", "音声出力 ON/OFF 切替")

        captured = capsys.readouterr()
        assert "音声出力 ON/OFF 切替" in captured.out, (
            f"reason_label が出力に含まれること。out={captured.out!r}"
        )

    def test_different_reason_label(self, capsys):
        """別の reason_label も正しく表示されること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        app._restart_route_for_change("b", "原文表示 ON/OFF 切替")

        captured = capsys.readouterr()
        assert "原文表示 ON/OFF 切替" in captured.out, (
            f"reason_label が出力に含まれること。out={captured.out!r}"
        )


# ---------------------------------------------------------------------------
# 3. 連打防御: 1系統に対して複数回呼ばれても1回しか実行されない
# ---------------------------------------------------------------------------

class TestRestartLockDebounce:
    """連打防御（_restart_locks）のテスト。"""

    def test_concurrent_calls_execute_only_once(self):
        """同じ系統に対して同時に複数回呼ばれても stop/start は1回ずつのみ実行されること。"""
        # stop_route をゆっくり（0.2秒）実行させて競合を作る
        def slow_stop(rid):
            time.sleep(0.2)

        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        fake_system.stop_route.side_effect = slow_stop
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        results = []

        def call_helper():
            app._restart_route_for_change("a", "テスト切替")
            results.append("done")

        # 2スレッドから同時に呼ぶ
        threads = [threading.Thread(target=call_helper) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # stop_route は1回のみのはず（2回目は lock で弾かれる）
        assert fake_system.stop_route.call_count == 1, (
            f"stop_route は1回のみ呼ばれること。call_count={fake_system.stop_route.call_count}"
        )
        assert fake_system.start_route.call_count == 1, (
            f"start_route は1回のみ呼ばれること。call_count={fake_system.start_route.call_count}"
        )

    def test_concurrent_calls_different_routes_both_execute(self):
        """異なる系統への同時呼び出しは両方とも実行されること。"""
        def slow_stop(rid):
            time.sleep(0.1)

        fake_system = FakeMultiCaptionSystem(
            route_a_state=RouteState.RUNNING,
            route_b_state=RouteState.RUNNING,
        )
        fake_system.stop_route.side_effect = slow_stop
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        done_events = {"a": threading.Event(), "b": threading.Event()}

        def call_a():
            app._restart_route_for_change("a", "テスト切替")
            done_events["a"].set()

        def call_b():
            app._restart_route_for_change("b", "テスト切替")
            done_events["b"].set()

        ta = threading.Thread(target=call_a)
        tb = threading.Thread(target=call_b)
        ta.start()
        tb.start()
        ta.join(timeout=5.0)
        tb.join(timeout=5.0)

        # 両系統それぞれ1回ずつ呼ばれること
        assert fake_system.stop_route.call_count == 2, (
            f"stop_route は計2回（各系統1回）呼ばれること。"
            f"call_count={fake_system.stop_route.call_count}"
        )

    def test_skip_notification_sent_to_gui_queue_when_locked(self):
        """既に再起動中の系統へ追加呼び出しした場合、_gui_queue にスキップ通知が入ること。"""
        import queue as _queue

        # ロックを手動で取得して「再起動中」状態を作る
        app._restart_locks["a"].acquire()

        fake_queue = _queue.Queue()
        original_queue = app._gui_queue
        app._gui_queue = fake_queue

        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        try:
            app._restart_route_for_change("a", "テスト切替")

            # スキップ通知が queue に入ること
            item = fake_queue.get_nowait()
            assert item["cmd"] == "set_status", f"cmd が set_status であること: {item}"
            assert "スキップ" in item["text"] or "A" in item["text"] or "a" in item["text"].lower(), (
                f"スキップ通知のテキストに系統情報が含まれること: {item['text']!r}"
            )
        finally:
            app._restart_locks["a"].release()
            app._gui_queue = original_queue

    def test_lock_released_after_completion(self):
        """再起動完了後にロックが解放されること（次回の呼び出しが可能）。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        # 1回目
        app._restart_route_for_change("a", "テスト切替")

        # ロックが解放されているため2回目も実行可能なこと
        app._restart_route_for_change("a", "テスト切替2回目")

        assert fake_system.stop_route.call_count == 2, (
            f"ロック解放後に2回目の呼び出しも実行されること。"
            f"call_count={fake_system.stop_route.call_count}"
        )

    def test_lock_released_even_after_exception(self):
        """stop_route が例外を投げてもロックが解放されること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        fake_system.stop_route.side_effect = RuntimeError("fake error")
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        # 例外が外に伝播しないこと
        app._restart_route_for_change("a", "テスト切替")

        # ロックが解放されていること（acquire(blocking=False) が True を返す）
        acquired = app._restart_locks["a"].acquire(blocking=False)
        assert acquired, "例外後もロックが解放されること"
        app._restart_locks["a"].release()


# ---------------------------------------------------------------------------
# 4. _on_route_a/b_output_enable_change との後方互換性
# ---------------------------------------------------------------------------

class TestAudioOutputEnableChangeBackwardCompat:
    """既存の _on_route_a/b_output_enable_change の動作が維持されること。"""

    def test_route_a_output_off_still_triggers_restart(self):
        """_on_route_a_output_enable_change OFF で stop_route('a') が呼ばれること（後方互換）。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_a_output_enable_change(sender=None, app_data=False, user_data=None)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.stop_route.assert_called_once_with("a")
        fake_system.start_route.assert_called_once_with("a")

    def test_route_b_output_off_still_triggers_restart(self):
        """_on_route_b_output_enable_change OFF で stop_route('b') が呼ばれること（後方互換）。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            mock_dpg.get_value.return_value = "(なし)"

            app._on_route_b_output_enable_change(sender=None, app_data=False, user_data=None)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.stop_route.assert_called_once_with("b")
        fake_system.start_route.assert_called_once_with("b")


# ---------------------------------------------------------------------------
# 5. W-COST-2: 稼働中に原文表示切替で即時再起動
# ---------------------------------------------------------------------------

class TestSourceTranscriptChangeTriggersRestart:
    """W-COST-2: 稼働中の原文表示 ON/OFF 切替で stop_route -> start_route が呼ばれること。"""

    def test_route_a_source_transcript_change_triggers_restart_when_running(self):
        """系統A 稼働中に原文表示切替で再起動が発生すること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            app._on_route_a_source_transcript_change(sender=None, app_data=True)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.stop_route.assert_called_once_with("a")
        fake_system.start_route.assert_called_once_with("a")

    def test_route_b_source_transcript_change_triggers_restart_when_running(self):
        """系統B 稼働中に原文表示切替で再起動が発生すること。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            app._on_route_b_source_transcript_change(sender=None, app_data=False)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        fake_system.stop_route.assert_called_once_with("b")
        fake_system.start_route.assert_called_once_with("b")

    def test_route_a_source_transcript_no_restart_when_not_running(self):
        """_konnyaku_running=False のとき原文表示切替で再起動しないこと。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            app._on_route_a_source_transcript_change(sender=None, app_data=True)

        time.sleep(0.2)
        fake_system.stop_route.assert_not_called()
        fake_system.start_route.assert_not_called()

    def test_route_b_source_transcript_no_restart_when_not_running(self):
        """系統B: _konnyaku_running=False のとき原文表示切替で再起動しないこと。"""
        fake_system = FakeMultiCaptionSystem(route_b_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = False

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            app._on_route_b_source_transcript_change(sender=None, app_data=True)

        time.sleep(0.2)
        fake_system.stop_route.assert_not_called()
        fake_system.start_route.assert_not_called()

    def test_route_a_source_transcript_no_restart_when_route_idle(self):
        """系統A が IDLE 状態のとき原文表示切替で再起動しないこと。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.IDLE)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            app._on_route_a_source_transcript_change(sender=None, app_data=True)

        time.sleep(0.2)
        fake_system.stop_route.assert_not_called()
        fake_system.start_route.assert_not_called()

    def test_route_a_source_transcript_no_restart_when_system_none(self):
        """_konnyaku_system=None のとき例外なく終了すること。"""
        app._konnyaku_system = None
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            # 例外が発生しないこと
            app._on_route_a_source_transcript_change(sender=None, app_data=True)

        time.sleep(0.1)

    def test_route_a_source_transcript_restart_uses_reason_label(self, capsys):
        """原文表示切替の再起動で reason_label が出力に含まれること。"""
        fake_system = FakeMultiCaptionSystem(route_a_state=RouteState.RUNNING)
        app._konnyaku_system = fake_system
        app._konnyaku_running = True

        with patch("app.dpg") as mock_dpg:
            mock_dpg.does_item_exist.return_value = True
            app._on_route_a_source_transcript_change(sender=None, app_data=True)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if fake_system.start_route.called:
                break
            time.sleep(0.05)

        captured = capsys.readouterr()
        assert "原文表示" in captured.out, (
            f"原文表示切替の reason_label が出力に含まれること。out={captured.out!r}"
        )


# ---------------------------------------------------------------------------
# 6. _restart_locks グローバル変数の存在確認
# ---------------------------------------------------------------------------

class TestRestartLocksGlobal:
    """_restart_locks が app モジュールに存在し、正しく初期化されていること。"""

    def test_restart_locks_exists(self):
        """app._restart_locks が存在すること。"""
        assert hasattr(app, "_restart_locks"), "app._restart_locks が存在すること"

    def test_restart_locks_has_route_a_and_b(self):
        """_restart_locks に 'a' と 'b' のキーがあること。"""
        assert "a" in app._restart_locks, "_restart_locks['a'] が存在すること"
        assert "b" in app._restart_locks, "_restart_locks['b'] が存在すること"

    def test_restart_locks_are_threading_locks(self):
        """_restart_locks の各値が threading.Lock インスタンスであること。"""
        for key in ("a", "b"):
            lock = app._restart_locks[key]
            # Lock は acquire/release メソッドを持つ
            assert hasattr(lock, "acquire"), f"_restart_locks[{key!r}] は Lock であること"
            assert hasattr(lock, "release"), f"_restart_locks[{key!r}] は Lock であること"
