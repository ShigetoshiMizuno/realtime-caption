"""
ptt_hotkey_manager.py

Push-to-Talk ホットキー管理モジュール。
グローバルホットキーの登録・解除と押下/離脱コールバックの発火を担う。

issue #82 / docs/spec/ptt-mode-design.md F-1 / F-4 / F-8 参照。

注意事項 (Windows):
    - keyboard ライブラリは Windows で Raw Input フックを使用するため、
      UAC 昇格アプリがフォアグラウンドにある場合にフックが効かないケースがある。
    - 一部のセキュリティソフトが誤検知する場合がある。
    - これらの制約は仕様制約であり README に明記する。
    - keyboard ライブラリはグローバルフックを使用するため、
      通常ユーザー権限で動作するが、昇格プロセスには反応しない場合がある。
"""

import threading
import time
from collections import deque
from typing import Callable


# デバウンス・チャタリング防御の定数
_PRESS_DEBOUNCE_SEC = 0.200   # 押下デバウンス: 200ms (F-4.1)
_RELEASE_DEBOUNCE_SEC = 0.500  # 離脱デバウンス: 500ms (F-4.2, TBD-1 確定値)
_CHATTER_WINDOW_SEC = 10.0     # 連打カウント窓: 10秒 (F-4.3)
_CHATTER_LIMIT = 5             # 連打上限: 5回 (F-4.3)


class PttHotkeyManager:
    """
    Push-to-Talk ホットキーマネージャー。

    keyboard ライブラリを使ってグローバルホットキーを登録し、
    押下 (key down) と離脱 (key up) を個別コールバックとして通知する。

    デバウンス・チャタリング防御 (F-4):
    - 押下デバウンス 200ms: 前回押下から 200ms 未満の再押下は on_press を発火しない
    - 離脱デバウンス 500ms: 離脱後 500ms のタイマー満了で on_release を発火する
      (500ms 以内の再押下でタイマーをキャンセルし on_release を発火しない)
    - 連打上限 10s/5回: 10秒スライディングウィンドウ内で 5 回以上の押下で
      on_chatter_warning を発火し、以降の押下を drop する

    テスト時は keyboard_module 引数に FakeKeyboardBackend を渡すことで
    実キーボードなしに動作を検証できる。

    Parameters
    ----------
    hotkey : str
        監視するキー名。keyboard ライブラリのキー名形式（例: "f8", "f9"）。
        デフォルト: "f8"
    on_press : Callable | None
        ホットキー押下時に呼ばれるコールバック。引数は keyboard のイベントオブジェクト。
        押下デバウンス通過後に発火する。
    on_release : Callable | None
        ホットキー離脱時に呼ばれるコールバック。引数は keyboard のイベントオブジェクト。
        離脱デバウンス（500ms タイマー満了後）に発火する。
    on_chatter_warning : Callable | None
        連打上限（10秒内に 5 回以上）検出時に呼ばれるコールバック。引数なし。
    keyboard_module : object | None
        keyboard モジュールの代替実装。None の場合は import keyboard を遅延実行する。
        テスト時に FakeKeyboardBackend を注入するために使用する。
    timer_factory : Callable | None
        threading.Timer 互換のタイマー生成関数。(interval, func) を受け取り
        タイマーオブジェクトを返す。None の場合は threading.Timer を使用する。
        テスト時に FakeTimerFactory を注入することで時間進行を制御できる。
    """

    def __init__(
        self,
        hotkey: str = "f8",
        on_press: Callable | None = None,
        on_release: Callable | None = None,
        on_chatter_warning: Callable | None = None,
        *,
        keyboard_module=None,
        timer_factory: Callable | None = None,
    ) -> None:
        self.hotkey = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self.on_chatter_warning = on_chatter_warning
        self._keyboard_module = keyboard_module
        self._timer_factory = timer_factory if timer_factory is not None else threading.Timer
        self._lock = threading.Lock()
        self._running = False
        self._press_hook = None
        self._release_hook = None

        # --- デバウンス状態 ---
        # 押下デバウンス: 直前の on_press 発火時刻 (monotonic)
        # 初期値は十分過去 (0.0) にして初回押下を確実に通過させる
        self._last_press_time: float = 0.0

        # 離脱デバウンス: 現在の離脱タイマー
        self._release_timer: threading.Timer | None = None

        # 連打カウント: 直近 _CHATTER_WINDOW_SEC 内の押下タイムスタンプ
        self._press_times: deque = deque()

        # 連打警告フラグ: True の間は新規押下を drop する
        self._chatter_triggered: bool = False

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """ホットキーが登録済み（リスニング中）かどうか。"""
        return self._running

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        ホットキーを登録してリスニングを開始する。

        既に start() 済みの場合は no-op（冪等性保証）。
        スレッドセーフ。
        """
        with self._lock:
            if self._running:
                return
            kb = self._get_keyboard()
            self._press_hook = kb.on_press_key(
                self.hotkey, self._handle_press
            )
            self._release_hook = kb.on_release_key(
                self.hotkey, self._handle_release
            )
            self._running = True

    def stop(self) -> None:
        """
        ホットキーの登録を解除してリスニングを停止する。

        start() を呼んでいない場合・既に stop() 済みの場合は no-op（冪等性保証）。
        スレッドセーフ。
        """
        with self._lock:
            if not self._running:
                return
            self._cancel_release_timer_locked()
            kb = self._get_keyboard()
            kb.unhook_all()
            self._press_hook = None
            self._release_hook = None
            self._running = False

    def change_hotkey(self, new_hotkey: str) -> None:
        """
        監視するホットキーを変更する。

        リスニング中の場合は一度停止してから新しいキーで再登録する。
        停止中の場合は hotkey 属性の更新のみ行う。
        スレッドセーフ。

        Parameters
        ----------
        new_hotkey : str
            新しいキー名（keyboard ライブラリのキー名形式）。
        """
        with self._lock:
            was_running = self._running
            if was_running:
                # ロック保持中なので _stop_locked を直接呼ぶ
                self._stop_locked()
            self.hotkey = new_hotkey
            if was_running:
                self._start_locked()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_keyboard(self):
        """keyboard モジュールを返す。未注入の場合は遅延 import する。"""
        if self._keyboard_module is not None:
            return self._keyboard_module
        # 遅延 import: テスト環境で keyboard がインストールされていなくても
        # PttHotkeyManager のインポート時点でエラーにならないようにする。
        import keyboard as kb  # noqa: PLC0415
        return kb

    def _start_locked(self) -> None:
        """ロック取得済み状態での start 処理。"""
        kb = self._get_keyboard()
        self._press_hook = kb.on_press_key(
            self.hotkey, self._handle_press
        )
        self._release_hook = kb.on_release_key(
            self.hotkey, self._handle_release
        )
        self._running = True

    def _stop_locked(self) -> None:
        """ロック取得済み状態での stop 処理。"""
        self._cancel_release_timer_locked()
        kb = self._get_keyboard()
        kb.unhook_all()
        self._press_hook = None
        self._release_hook = None
        self._running = False

    def _cancel_release_timer_locked(self) -> None:
        """現在の離脱タイマーをキャンセルする（ロック保持前提）。"""
        if self._release_timer is not None:
            self._release_timer.cancel()
            self._release_timer = None

    def _handle_press(self, event) -> None:
        """keyboard ライブラリの press イベントハンドラー。

        押下デバウンス（200ms）と連打上限チェックを行い、
        通過した場合のみ on_press を発火する。
        離脱デバウンス中（タイマー動作中）なら離脱タイマーをキャンセルする。
        """
        now = time.monotonic()

        with self._lock:
            # 連打警告中は drop
            if self._chatter_triggered:
                return

            # 押下デバウンス: 前回押下から 200ms 未満なら無視 (F-4.1)
            if now - self._last_press_time < _PRESS_DEBOUNCE_SEC:
                return

            # 離脱デバウンス中に再押下: タイマーキャンセル (F-4.2)
            self._cancel_release_timer_locked()

            # 連打カウントを更新: ウィンドウ外の古いタイムスタンプを除去
            cutoff = now - _CHATTER_WINDOW_SEC
            while self._press_times and self._press_times[0] <= cutoff:
                self._press_times.popleft()
            self._press_times.append(now)

            # 連打上限チェック (F-4.3)
            if len(self._press_times) >= _CHATTER_LIMIT:
                self._chatter_triggered = True
                # warning コールバックはロック外で発火（デッドロック防止）
                warning_cb = self.on_chatter_warning
            else:
                warning_cb = None

            self._last_press_time = now
            press_cb = self.on_press if not self._chatter_triggered else None

        # ロック外でコールバック発火
        if warning_cb is not None:
            warning_cb()
        if press_cb is not None:
            press_cb(event)

    def _handle_release(self, event) -> None:
        """keyboard ライブラリの release イベントハンドラー。

        離脱デバウンス（500ms タイマー）を開始する。
        押下がない状態での離脱（押下後に stop() した場合など）は無視する。
        """
        with self._lock:
            # 一度も押下されていない（_last_press_time が初期値）なら無視
            if self._last_press_time == 0.0:
                return

            # 既存の離脱タイマーが動いていれば更新（二重離脱対策）
            self._cancel_release_timer_locked()

            release_cb = self.on_release
            timer = self._timer_factory(
                _RELEASE_DEBOUNCE_SEC,
                lambda: self._fire_release(release_cb, event),
            )
            self._release_timer = timer

        timer.start()

    def _fire_release(self, release_cb: Callable | None, event) -> None:
        """離脱タイマー満了時に呼ばれる。on_release を発火する。"""
        with self._lock:
            # タイマーが既にクリアされていれば（cancel() 済み）no-op
            if self._release_timer is None:
                return
            self._release_timer = None

        if release_cb is not None:
            release_cb(event)
