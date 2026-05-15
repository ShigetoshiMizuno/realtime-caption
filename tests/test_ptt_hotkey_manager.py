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
        """複数回押下すると on_press がその回数分呼ばれること"""
        self.kb.simulate_press("f8")
        self.kb.simulate_press("f8")
        self.kb.simulate_press("f8")
        assert self.press_cb.call_count == 3

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

        # 3スレッド × 10回 = 30回
        assert len(results) == 30
