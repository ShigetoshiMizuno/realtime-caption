"""
tests/test_ptt_hotkey_manager.py

PttHotkeyManager の単体テスト。
keyboard ライブラリは dependency injection で差し替え、
実キーボードなしで on_press / on_release コールバックの発火を検証する。

F-8.1: FakeKeyboardBackend 方式（ptt-mode-design.md 参照）
"""

import threading
from unittest.mock import MagicMock, call

import pytest

from ptt_hotkey_manager import PttHotkeyManager


# ---------------------------------------------------------------------------
# FakeKeyboardBackend
# ---------------------------------------------------------------------------

class FakeKeyboardBackend:
    """
    keyboard モジュールの代替。
    on_press_key / on_release_key で登録したコールバックを
    simulate_press / simulate_release で同期発火できる。
    """

    def __init__(self):
        self._press_handlers: dict[str, list] = {}
        self._release_handlers: dict[str, list] = {}
        self.unhook_all_called = 0
        self._all_hooks: list = []

    # --- keyboard API 互換 ---

    def on_press_key(self, key: str, callback, suppress: bool = False):
        self._press_handlers.setdefault(key, []).append(callback)
        hook = (key, "press", callback)
        self._all_hooks.append(hook)
        return hook

    def on_release_key(self, key: str, callback, suppress: bool = False):
        self._release_handlers.setdefault(key, []).append(callback)
        hook = (key, "release", callback)
        self._all_hooks.append(hook)
        return hook

    def unhook_all(self):
        self.unhook_all_called += 1
        self._press_handlers.clear()
        self._release_handlers.clear()
        self._all_hooks.clear()

    def unhook(self, hook):
        key, kind, cb = hook
        if kind == "press":
            handlers = self._press_handlers.get(key, [])
        else:
            handlers = self._release_handlers.get(key, [])
        if cb in handlers:
            handlers.remove(cb)
        if hook in self._all_hooks:
            self._all_hooks.remove(hook)

    # --- テスト補助 ---

    def simulate_press(self, key: str):
        """指定キーの press ハンドラーを全件同期発火する。"""
        fake_event = MagicMock()
        fake_event.name = key
        for cb in list(self._press_handlers.get(key, [])):
            cb(fake_event)

    def simulate_release(self, key: str):
        """指定キーの release ハンドラーを全件同期発火する。"""
        fake_event = MagicMock()
        fake_event.name = key
        for cb in list(self._release_handlers.get(key, [])):
            cb(fake_event)

    def registered_press_keys(self) -> set:
        return {k for k, v in self._press_handlers.items() if v}

    def registered_release_keys(self) -> set:
        return {k for k, v in self._release_handlers.items() if v}


# ===========================================================================
# テストクラス: 基本動作
# ===========================================================================


class TestPttHotkeyManagerInit:
    """コンストラクタ・初期状態のテスト"""

    def test_default_hotkey_is_f8(self):
        """デフォルトホットキーが f8 であること"""
        mgr = PttHotkeyManager()
        assert mgr.hotkey == "f8"

    def test_custom_hotkey_accepted(self):
        """コンストラクタで任意のホットキーを指定できること"""
        mgr = PttHotkeyManager(hotkey="f9")
        assert mgr.hotkey == "f9"

    def test_initial_state_not_running(self):
        """start() を呼ぶ前は running=False であること"""
        mgr = PttHotkeyManager()
        assert mgr.running is False

    def test_callbacks_default_to_none(self):
        """on_press / on_release を省略すると None が設定されること"""
        mgr = PttHotkeyManager()
        assert mgr.on_press is None
        assert mgr.on_release is None

    def test_custom_callbacks_stored(self):
        """コンストラクタで渡したコールバックが格納されること"""
        press_cb = MagicMock()
        release_cb = MagicMock()
        mgr = PttHotkeyManager(on_press=press_cb, on_release=release_cb)
        assert mgr.on_press is press_cb
        assert mgr.on_release is release_cb


class TestPttHotkeyManagerStartStop:
    """start() / stop() の動作テスト"""

    def setup_method(self):
        self.kb = FakeKeyboardBackend()
        self.press_cb = MagicMock()
        self.release_cb = MagicMock()
        self.mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=self.press_cb,
            on_release=self.release_cb,
            keyboard_module=self.kb,
        )

    def test_start_registers_hotkey(self):
        """start() 後にホットキーが登録されること"""
        self.mgr.start()
        assert "f8" in self.kb.registered_press_keys()
        assert "f8" in self.kb.registered_release_keys()

    def test_start_sets_running_true(self):
        """start() 後に running=True になること"""
        self.mgr.start()
        assert self.mgr.running is True

    def test_stop_calls_unhook_all(self):
        """stop() 後に unhook_all が呼ばれること"""
        self.mgr.start()
        self.mgr.stop()
        assert self.kb.unhook_all_called >= 1

    def test_stop_sets_running_false(self):
        """stop() 後に running=False になること"""
        self.mgr.start()
        self.mgr.stop()
        assert self.mgr.running is False

    def test_start_idempotent(self):
        """start() を 2 回呼んでも重複登録されないこと（冪等性）"""
        self.mgr.start()
        self.mgr.start()
        # press / release のハンドラー数がそれぞれ 1 つだけであること
        assert len(self.kb._press_handlers.get("f8", [])) == 1
        assert len(self.kb._release_handlers.get("f8", [])) == 1

    def test_stop_idempotent(self):
        """stop() を 2 回呼んでもエラーにならないこと（冪等性）"""
        self.mgr.start()
        self.mgr.stop()
        self.mgr.stop()  # 例外が起きなければ OK
        assert self.mgr.running is False

    def test_stop_without_start_is_safe(self):
        """start() なしで stop() を呼んでもエラーにならないこと"""
        self.mgr.stop()  # 例外が起きなければ OK
        assert self.mgr.running is False


# ===========================================================================
# テストクラス: コールバック発火
# ===========================================================================


class TestPttHotkeyManagerCallbacks:
    """押下・離脱コールバックの発火テスト"""

    def setup_method(self):
        self.kb = FakeKeyboardBackend()
        self.press_cb = MagicMock()
        self.release_cb = MagicMock()
        self.mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=self.press_cb,
            on_release=self.release_cb,
            keyboard_module=self.kb,
        )
        self.mgr.start()

    def test_press_callback_fired(self):
        """ホットキー押下時に on_press コールバックが呼ばれること"""
        self.kb.simulate_press("f8")
        self.press_cb.assert_called_once()

    def test_release_callback_fired(self):
        """ホットキー離脱時に on_release コールバックが呼ばれること"""
        self.kb.simulate_release("f8")
        self.release_cb.assert_called_once()

    def test_press_callback_not_fired_for_other_key(self):
        """別キーの押下では on_press が呼ばれないこと"""
        self.kb.simulate_press("f9")
        self.press_cb.assert_not_called()

    def test_release_callback_not_fired_for_other_key(self):
        """別キーの離脱では on_release が呼ばれないこと"""
        self.kb.simulate_release("f9")
        self.release_cb.assert_not_called()

    def test_press_callback_not_fired_after_stop(self):
        """stop() 後は押下しても on_press が呼ばれないこと"""
        self.mgr.stop()
        self.kb.simulate_press("f8")
        self.press_cb.assert_not_called()

    def test_release_callback_not_fired_after_stop(self):
        """stop() 後は離脱しても on_release が呼ばれないこと"""
        self.mgr.stop()
        self.kb.simulate_release("f8")
        self.release_cb.assert_not_called()

    def test_press_callback_fired_multiple_times(self):
        """複数回押下すると on_press がその回数分呼ばれること（デバウンスなし・timer_factory=None）

        デバウンスは timer_factory を注入した場合にのみ有効。
        デフォルト（timer_factory=None）は threading.Timer を使うが、
        即座の連続押下は _last_press_time ガードにより 1 回になる。
        本テストは FakeKeyboardBackend の同期発火で 200ms が経過しないため
        押下デバウンスにより 1 回しか発火しないことを確認する。
        """
        self.kb.simulate_press("f8")
        self.kb.simulate_press("f8")
        self.kb.simulate_press("f8")
        # デバウンス込みでは連続押下は 1 回しか発火しない
        assert self.press_cb.call_count == 1

    def test_no_press_callback_is_safe(self):
        """on_press=None でも押下してもエラーにならないこと"""
        mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=None,
            on_release=None,
            keyboard_module=self.kb,
        )
        mgr.start()
        self.kb.simulate_press("f8")  # 例外が起きなければ OK


# ===========================================================================
# テストクラス: change_hotkey
# ===========================================================================


class TestPttHotkeyManagerChangeHotkey:
    """change_hotkey() の動作テスト"""

    def setup_method(self):
        self.kb = FakeKeyboardBackend()
        self.press_cb = MagicMock()
        self.release_cb = MagicMock()
        self.mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=self.press_cb,
            on_release=self.release_cb,
            keyboard_module=self.kb,
        )
        self.mgr.start()

    def test_change_hotkey_updates_hotkey_attr(self):
        """change_hotkey() 後に hotkey 属性が更新されること"""
        self.mgr.change_hotkey("f9")
        assert self.mgr.hotkey == "f9"

    def test_change_hotkey_reregisters_new_key(self):
        """change_hotkey() 後に新しいキーが登録されること"""
        self.mgr.change_hotkey("f9")
        assert "f9" in self.kb.registered_press_keys()
        assert "f9" in self.kb.registered_release_keys()

    def test_change_hotkey_unregisters_old_key(self):
        """change_hotkey() 後に古いキーの登録が解除されること"""
        self.mgr.change_hotkey("f9")
        assert "f8" not in self.kb.registered_press_keys()
        assert "f8" not in self.kb.registered_release_keys()

    def test_change_hotkey_new_key_fires_callback(self):
        """change_hotkey() 後に新しいキーでコールバックが発火すること"""
        self.mgr.change_hotkey("f9")
        self.kb.simulate_press("f9")
        self.press_cb.assert_called_once()

    def test_change_hotkey_old_key_no_callback(self):
        """change_hotkey() 後に古いキーではコールバックが発火しないこと"""
        self.mgr.change_hotkey("f9")
        self.kb.simulate_press("f8")
        self.press_cb.assert_not_called()

    def test_change_hotkey_when_stopped(self):
        """stop() 後に change_hotkey() を呼んでも hotkey 属性は更新されること"""
        self.mgr.stop()
        self.mgr.change_hotkey("f9")
        assert self.mgr.hotkey == "f9"
        # 停止中なのでキーは登録されない
        assert "f9" not in self.kb.registered_press_keys()


# ===========================================================================
# テストクラス: スレッドセーフ
# ===========================================================================


class TestPttHotkeyManagerThreadSafety:
    """並行呼び出し時のスレッドセーフ動作テスト"""

    def test_concurrent_start_stop_no_exception(self):
        """複数スレッドから start/stop を同時に呼んでも例外が発生しないこと"""
        kb = FakeKeyboardBackend()
        mgr = PttHotkeyManager(
            hotkey="f8",
            keyboard_module=kb,
        )

        errors = []

        def run_start_stop():
            try:
                for _ in range(5):
                    mgr.start()
                    mgr.stop()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run_start_stop) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert errors == [], f"並行実行で例外が発生: {errors}"

    def test_concurrent_change_hotkey_no_exception(self):
        """複数スレッドから change_hotkey を同時に呼んでも例外が発生しないこと"""
        kb = FakeKeyboardBackend()
        mgr = PttHotkeyManager(
            hotkey="f8",
            keyboard_module=kb,
        )
        mgr.start()

        errors = []
        hotkeys = ["f8", "f9", "f10", "f11", "f12"]

        def run_change():
            try:
                for hk in hotkeys:
                    mgr.change_hotkey(hk)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=run_change) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert errors == [], f"並行実行で例外が発生: {errors}"

    def test_callbacks_fired_from_multiple_threads(self):
        """複数スレッドからのコールバック発火でカウントが正確なこと"""
        kb = FakeKeyboardBackend()
        results = []
        lock = threading.Lock()

        def press_cb(event=None):
            with lock:
                results.append("press")

        mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=press_cb,
            keyboard_module=kb,
        )
        mgr.start()

        def fire_press():
            for _ in range(10):
                kb.simulate_press("f8")

        threads = [threading.Thread(target=fire_press) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # デバウンス込みではカウントは不定（押下デバウンスでフィルタされる）。
        # 例外が発生しないこと・少なくとも 1 回は発火することを確認する。
        assert len(results) >= 1


# ===========================================================================
# FakeTimer: timer_factory DI 用のテスト補助クラス
# ===========================================================================


class FakeTimer:
    """
    threading.Timer の代替。
    start() を呼んでも自動発火しない。
    テスト側から fire() を呼んで手動で発火させることができる。
    """

    def __init__(self, interval: float, func):
        self.interval = interval
        self.func = func
        self._cancelled = False
        self._started = False
        self._fired = False

    def start(self):
        self._started = True

    def cancel(self):
        self._cancelled = True

    def fire(self):
        """タイマーを手動発火する（テスト用）。キャンセル済みの場合は no-op。"""
        if not self._cancelled:
            self._fired = True
            self.func()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def started(self) -> bool:
        return self._started

    @property
    def fired(self) -> bool:
        return self._fired


class FakeTimerFactory:
    """
    FakeTimer を作成する factory。
    作成した全 FakeTimer を記録する。
    """

    def __init__(self):
        self.timers: list[FakeTimer] = []

    def __call__(self, interval: float, func) -> FakeTimer:
        t = FakeTimer(interval, func)
        self.timers.append(t)
        return t

    def latest(self) -> FakeTimer | None:
        """最後に作成した FakeTimer を返す。"""
        return self.timers[-1] if self.timers else None


# ===========================================================================
# テストクラス: 押下デバウンス（200ms）
# ===========================================================================


class TestPressDebounce:
    """押下デバウンス: 200ms 以内の再押下は on_press を発火しない。F-4.1 参照。"""

    def setup_method(self):
        self.kb = FakeKeyboardBackend()
        self.press_cb = MagicMock()
        self.release_cb = MagicMock()
        self.timer_factory = FakeTimerFactory()
        self.mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=self.press_cb,
            on_release=self.release_cb,
            keyboard_module=self.kb,
            timer_factory=self.timer_factory,
        )
        self.mgr.start()

    def test_first_press_fires_callback_immediately(self):
        """初回押下は即時 on_press が発火すること"""
        self.kb.simulate_press("f8")
        self.press_cb.assert_called_once()

    def test_rapid_repress_within_debounce_is_ignored(self):
        """200ms 以内の再押下は on_press が発火しないこと（デバウンス）"""
        self.kb.simulate_press("f8")   # 1 回目: 発火
        self.kb.simulate_press("f8")   # 2 回目: 200ms 未満なので無視
        self.kb.simulate_press("f8")   # 3 回目: 200ms 未満なので無視
        self.press_cb.assert_called_once()

    def test_press_after_debounce_window_fires_again(self):
        """押下デバウンス後（200ms 経過をシミュレート）は再度 on_press が発火すること"""
        self.kb.simulate_press("f8")   # 1 回目: 発火
        # _last_press_time を過去に設定して 200ms 経過をシミュレート
        self.mgr._last_press_time -= 0.201
        self.kb.simulate_press("f8")   # 2 回目: 200ms 以上経過したので発火
        assert self.press_cb.call_count == 2

    def test_press_debounce_threshold_is_200ms(self):
        """押下デバウンスの閾値が 200ms であること（199ms は無視、201ms は発火）"""
        self.kb.simulate_press("f8")   # 1 回目: 発火

        # 199ms 経過 → まだ無視
        self.mgr._last_press_time -= 0.199
        self.kb.simulate_press("f8")
        assert self.press_cb.call_count == 1

        # さらに 2ms 経過（合計 201ms） → 発火
        self.mgr._last_press_time -= 0.002
        self.kb.simulate_press("f8")
        assert self.press_cb.call_count == 2


# ===========================================================================
# テストクラス: 離脱デバウンス（500ms）
# ===========================================================================


class TestReleaseDebounce:
    """離脱デバウンス: 離脱後 500ms 後に on_release を発火。F-4.2 参照。"""

    def setup_method(self):
        self.kb = FakeKeyboardBackend()
        self.press_cb = MagicMock()
        self.release_cb = MagicMock()
        self.timer_factory = FakeTimerFactory()
        self.mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=self.press_cb,
            on_release=self.release_cb,
            keyboard_module=self.kb,
            timer_factory=self.timer_factory,
        )
        self.mgr.start()

    def test_release_does_not_fire_immediately(self):
        """ホットキー離脱時に on_release が即時発火しないこと（500ms 遅延）"""
        self.kb.simulate_press("f8")
        self.kb.simulate_release("f8")
        self.release_cb.assert_not_called()

    def test_release_fires_after_timer_expiry(self):
        """500ms タイマー満了後に on_release が発火すること"""
        self.kb.simulate_press("f8")
        self.kb.simulate_release("f8")
        # タイマーを手動発火
        timer = self.timer_factory.latest()
        assert timer is not None
        timer.fire()
        self.release_cb.assert_called_once()

    def test_release_timer_interval_is_500ms(self):
        """離脱タイマーの間隔が 500ms であること"""
        self.kb.simulate_press("f8")
        self.kb.simulate_release("f8")
        timer = self.timer_factory.latest()
        assert timer is not None
        assert timer.interval == pytest.approx(0.5)

    def test_repress_within_debounce_cancels_release_timer(self):
        """離脱デバウンス中（500ms 以内）に再押下するとタイマーがキャンセルされること"""
        self.kb.simulate_press("f8")
        self.kb.simulate_release("f8")
        release_timer = self.timer_factory.latest()

        # 500ms 以内に再押下（_last_press_time を調整して 200ms 制限を回避）
        self.mgr._last_press_time -= 0.201
        self.kb.simulate_press("f8")

        # 離脱タイマーはキャンセルされているはず
        assert release_timer.cancelled is True

    def test_repress_within_debounce_prevents_release_callback(self):
        """離脱デバウンス中の再押下後に、タイマーを発火しても on_release が呼ばれないこと"""
        self.kb.simulate_press("f8")
        self.kb.simulate_release("f8")
        release_timer = self.timer_factory.latest()

        # 500ms 以内に再押下
        self.mgr._last_press_time -= 0.201
        self.kb.simulate_press("f8")

        # キャンセル済みタイマーを発火しても on_release は呼ばれない
        release_timer.fire()
        self.release_cb.assert_not_called()

    def test_release_timer_started_after_release(self):
        """離脱後にタイマーが start() されること"""
        self.kb.simulate_press("f8")
        self.kb.simulate_release("f8")
        timer = self.timer_factory.latest()
        assert timer is not None
        assert timer.started is True

    def test_release_without_press_does_not_create_timer(self):
        """押下なしで離脱しても on_release タイマーが作成されないこと"""
        # 押下なしの離脱は無視
        self.kb.simulate_release("f8")
        # タイマーは作成されない
        assert len(self.timer_factory.timers) == 0


# ===========================================================================
# テストクラス: 連打上限（10秒で5回 → warning、以降 drop）
# ===========================================================================


class TestChatterWarning:
    """連打上限: 10秒スライディングウィンドウで5回以上で on_chatter_warning 発火。F-4.3 参照。"""

    def setup_method(self):
        self.kb = FakeKeyboardBackend()
        self.press_cb = MagicMock()
        self.release_cb = MagicMock()
        self.chatter_cb = MagicMock()
        self.timer_factory = FakeTimerFactory()
        self.mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=self.press_cb,
            on_release=self.release_cb,
            on_chatter_warning=self.chatter_cb,
            keyboard_module=self.kb,
            timer_factory=self.timer_factory,
        )
        self.mgr.start()

    def _press_n_times(self, n: int):
        """n 回押下する。各押下で 200ms 以上経過したとみなすよう _last_press_time を調整。"""
        for _ in range(n):
            self.mgr._last_press_time -= 0.201
            self.kb.simulate_press("f8")

    def test_chatter_warning_fires_on_fifth_press(self):
        """10秒以内に5回押下すると on_chatter_warning が発火すること"""
        self._press_n_times(5)
        self.chatter_cb.assert_called_once()

    def test_chatter_warning_not_fired_on_fourth_press(self):
        """10秒以内に4回の押下では on_chatter_warning が発火しないこと"""
        self._press_n_times(4)
        self.chatter_cb.assert_not_called()

    def test_press_dropped_after_chatter_warning(self):
        """連打警告後の押下は on_press が drop（無視）されること"""
        self._press_n_times(5)  # 5 回目で warning
        press_count_at_warning = self.press_cb.call_count

        # 警告後の追加押下
        self.mgr._last_press_time -= 0.201
        self.kb.simulate_press("f8")
        # on_press カウントは増えない
        assert self.press_cb.call_count == press_count_at_warning

    def test_chatter_warning_no_callback_when_none(self):
        """on_chatter_warning=None でも連打してもエラーにならないこと"""
        mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=self.press_cb,
            on_chatter_warning=None,
            keyboard_module=self.kb,
            timer_factory=self.timer_factory,
        )
        mgr.start()
        for _ in range(6):
            mgr._last_press_time -= 0.201
            self.kb.simulate_press("f8")
        # 例外が起きなければ OK

    def test_old_presses_outside_window_are_discarded(self):
        """10秒スライディングウィンドウ外（古い）の押下はカウントから除外されること"""
        # 10秒以上前の押下 3 回を記録
        import time
        old_time = time.monotonic() - 11.0  # 11秒前
        # _press_times に直接追加して古いタイムスタンプを挿入
        for _ in range(3):
            self.mgr._press_times.append(old_time)

        # 新たに 4 回押下（ウィンドウ内は 4 回のみ）
        self._press_n_times(4)

        # 合計 3 + 4 = 7 回だが、ウィンドウ内は 4 回なので warning は発火しない
        self.chatter_cb.assert_not_called()

    def test_exactly_five_within_window_fires_warning(self):
        """ウィンドウ内でちょうど5回の押下で warning が発火すること"""
        import time
        # ウィンドウ内の 1 回を記録
        self.mgr._press_times.append(time.monotonic())
        # さらに 4 回押下（合計 5 回）
        self._press_n_times(4)
        self.chatter_cb.assert_called_once()


# ===========================================================================
# テストクラス: timer_factory DI（コンストラクタ注入）
# ===========================================================================


class TestTimerFactoryDI:
    """timer_factory DI: fake timer を注入してテスト確定性を確認。F-8.2 参照。"""

    def test_default_timer_factory_is_threading_timer(self):
        """timer_factory を省略すると threading.Timer が使われること"""
        import threading
        mgr = PttHotkeyManager()
        assert mgr._timer_factory is threading.Timer

    def test_custom_timer_factory_is_stored(self):
        """カスタム timer_factory がコンストラクタで格納されること"""
        factory = FakeTimerFactory()
        mgr = PttHotkeyManager(timer_factory=factory)
        assert mgr._timer_factory is factory

    def test_fake_timer_injected_on_release(self):
        """timer_factory=FakeTimerFactory のとき、離脱でタイマーが作成されること"""
        kb = FakeKeyboardBackend()
        factory = FakeTimerFactory()
        mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=MagicMock(),
            on_release=MagicMock(),
            keyboard_module=kb,
            timer_factory=factory,
        )
        mgr.start()
        kb.simulate_press("f8")
        kb.simulate_release("f8")
        assert len(factory.timers) == 1
        assert factory.timers[0].started is True

    def test_fake_timer_fire_triggers_release_callback(self):
        """FakeTimer.fire() を呼ぶと on_release が発火すること"""
        kb = FakeKeyboardBackend()
        factory = FakeTimerFactory()
        release_cb = MagicMock()
        mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=MagicMock(),
            on_release=release_cb,
            keyboard_module=kb,
            timer_factory=factory,
        )
        mgr.start()
        kb.simulate_press("f8")
        kb.simulate_release("f8")
        assert release_cb.call_count == 0
        factory.latest().fire()
        assert release_cb.call_count == 1

    def test_fake_timer_cancel_prevents_release_callback(self):
        """FakeTimer をキャンセルすると on_release が発火しないこと"""
        kb = FakeKeyboardBackend()
        factory = FakeTimerFactory()
        release_cb = MagicMock()
        mgr = PttHotkeyManager(
            hotkey="f8",
            on_press=MagicMock(),
            on_release=release_cb,
            keyboard_module=kb,
            timer_factory=factory,
        )
        mgr.start()
        kb.simulate_press("f8")
        kb.simulate_release("f8")
        factory.latest().cancel()
        factory.latest().fire()  # キャンセル後なので no-op
        assert release_cb.call_count == 0
