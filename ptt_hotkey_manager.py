"""
ptt_hotkey_manager.py

Push-to-Talk ホットキー管理モジュール。
グローバルホットキーの登録・解除と押下/離脱コールバックの発火を担う。

issue #82 / docs/spec/ptt-mode-design.md F-1 / F-8.1 参照。

注意事項 (Windows):
    - keyboard ライブラリは Windows で Raw Input フックを使用するため、
      UAC 昇格アプリがフォアグラウンドにある場合にフックが効かないケースがある。
    - 一部のセキュリティソフトが誤検知する場合がある。
    - これらの制約は仕様制約であり README に明記する。
    - keyboard ライブラリはグローバルフックを使用するため、
      通常ユーザー権限で動作するが、昇格プロセスには反応しない場合がある。
"""

import threading
from typing import Callable


class PttHotkeyManager:
    """
    Push-to-Talk ホットキーマネージャー。

    keyboard ライブラリを使ってグローバルホットキーを登録し、
    押下 (key down) と離脱 (key up) を個別コールバックとして通知する。

    テスト時は keyboard_module 引数に FakeKeyboardBackend を渡すことで
    実キーボードなしに動作を検証できる。

    Parameters
    ----------
    hotkey : str
        監視するキー名。keyboard ライブラリのキー名形式（例: "f8", "f9"）。
        デフォルト: "f8"
    on_press : Callable | None
        ホットキー押下時に呼ばれるコールバック。引数は keyboard のイベントオブジェクト。
    on_release : Callable | None
        ホットキー離脱時に呼ばれるコールバック。引数は keyboard のイベントオブジェクト。
    keyboard_module : object | None
        keyboard モジュールの代替実装。None の場合は import keyboard を遅延実行する。
        テスト時に FakeKeyboardBackend を注入するために使用する。
    """

    def __init__(
        self,
        hotkey: str = "f8",
        on_press: Callable | None = None,
        on_release: Callable | None = None,
        *,
        keyboard_module=None,
    ) -> None:
        self.hotkey = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self._keyboard_module = keyboard_module
        self._lock = threading.Lock()
        self._running = False
        self._press_hook = None
        self._release_hook = None

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
        kb = self._get_keyboard()
        kb.unhook_all()
        self._press_hook = None
        self._release_hook = None
        self._running = False

    def _handle_press(self, event) -> None:
        """keyboard ライブラリの press イベントハンドラー。"""
        if self.on_press is not None:
            self.on_press(event)

    def _handle_release(self, event) -> None:
        """keyboard ライブラリの release イベントハンドラー。"""
        if self.on_release is not None:
            self.on_release(event)
