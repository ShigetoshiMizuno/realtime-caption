"""PTT 機能の E2E テスト（実 keyboard ライブラリ + 仮想キー送出）。

PR #83-86 で実装された PttHotkeyManager / app.py の callback 配線を、
実 keyboard ライブラリの hook 登録と keyboard._listener.direct_callback()
による仮想キーイベント注入で統合動作確認する。

注意: keyboard.send() / keyboard.press() は自身のフックを経由しない
（keyboard._listener.is_replaying=True の間はフックをスキップする仕様）。
そのため本テストでは keyboard._listener.direct_callback() で
直接イベントを注入してコールバック配線を E2E 検証する。

CI 環境では keyboard のフック登録に管理者権限が必要なため、
@pytest.mark.skipif でスキップするマーカーを付ける。
ローカル実行時のみ動作する E2E テストとして整備する。

実行方法:
    KEYBOARD_E2E_ENABLE=1 python3 -m pytest tests/test_ptt_e2e.py -v
"""

import os
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from ptt_hotkey_manager import PttHotkeyManager

# ===========================================================================
# E2E スキップマーカー定義
# ===========================================================================

# keyboard ライブラリは Linux 環境では root 権限、Windows でも管理者権限が必要な場合がある
# CI / 通常ユーザー実行ではスキップ
KEYBOARD_E2E_SKIP_REASON = (
    "PTT E2E は管理者権限と GUI 環境が必要 (KEYBOARD_E2E_ENABLE=1 で実行)"
)

requires_keyboard_e2e = pytest.mark.skipif(
    os.environ.get("KEYBOARD_E2E_ENABLE", "0") != "1",
    reason=KEYBOARD_E2E_SKIP_REASON,
)

# ===========================================================================
# 結合点
# ===========================================================================
#
# | シンボル                     | 呼び出し元           | トリガー条件               |
# |------------------------------|----------------------|----------------------------|
# | requires_keyboard_e2e        | 各 E2E テスト関数    | テストスキップ判定         |
#
# E2E テスト自体は CI ではスキップだが、ローカル実行時には
# KEYBOARD_E2E_ENABLE=1 pytest tests/test_ptt_e2e.py で動作する。


# ===========================================================================
# ヘルパー: keyboard._listener 経由でイベントを注入する
# ===========================================================================

def _inject_key_event(key: str, event_type: str) -> None:
    """実 keyboard ライブラリのリスナーに直接キーイベントを注入する。

    keyboard.send() / keyboard.press() は is_replaying=True の間フックを
    スキップする仕様のため、direct_callback() を使って OS 経由と同等の
    イベント処理パスを通す。

    Parameters
    ----------
    key : str
        keyboard ライブラリのキー名（例: "f8"）
    event_type : str
        "down" または "up"
    """
    import keyboard
    from keyboard import KeyboardEvent, KEY_DOWN, KEY_UP

    scan_codes = keyboard.key_to_scan_codes(key)
    sc = scan_codes[0]
    etype = KEY_DOWN if event_type == "down" else KEY_UP
    event = KeyboardEvent(etype, sc, key)

    listener = keyboard._listener
    listener.start_if_necessary()
    # is_replaying=False の状態で direct_callback を呼ぶと
    # nonblocking_keys ハンドラーが発火してキューにも積まれる
    listener.direct_callback(event)


# ===========================================================================
# テスト 1: 実 keyboard ライブラリでのフック登録
# ===========================================================================


@requires_keyboard_e2e
def test_e2e_real_keyboard_hook_registration():
    """PttHotkeyManager を実 keyboard ライブラリで start/stop できること。

    実 keyboard ライブラリでフック登録 → 例外なく完了 → フック解除。
    """
    import keyboard

    mgr = PttHotkeyManager(hotkey="f8")

    # start() でフック登録が例外なく完了すること
    mgr.start()
    assert mgr.running is True

    # stop() でフック解除が例外なく完了すること
    mgr.stop()
    assert mgr.running is False


# ===========================================================================
# テスト 2: 仮想キー注入で on_press / on_release が呼ばれる
# ===========================================================================


@requires_keyboard_e2e
def test_e2e_simulate_press_and_release():
    """_inject_key_event() で仮想キーを注入すると on_press / on_release が呼ばれること。

    実 keyboard ライブラリのフック登録 + direct_callback() によるイベント注入で
    PttHotkeyManager の callback 配線を E2E 検証する。
    on_release は離脱デバウンス（500ms タイマー）があるため、タイマー満了後に確認する。
    """
    press_calls = []
    release_calls = []

    def on_press(event):
        press_calls.append(event)

    def on_release(event):
        release_calls.append(event)

    mgr = PttHotkeyManager(
        hotkey="f8",
        on_press=on_press,
        on_release=on_release,
    )
    mgr.start()

    try:
        # キー押下をシミュレート
        _inject_key_event("f8", "down")
        # 100ms 待機して press イベントの処理を確認
        time.sleep(0.1)
        assert len(press_calls) == 1, (
            f"on_press が呼ばれなかった (press_calls={press_calls})"
        )

        # キー離脱をシミュレート
        _inject_key_event("f8", "up")
        # 離脱デバウンス（500ms）満了を待つ（余裕を持って 700ms 待機）
        time.sleep(0.7)
        assert len(release_calls) == 1, (
            f"on_release が呼ばれなかった (release_calls={release_calls})"
        )

    finally:
        mgr.stop()


# ===========================================================================
# テスト 3: デバウンスが実 keyboard でも機能する
# ===========================================================================


@requires_keyboard_e2e
def test_e2e_debounce_under_real_keyboard():
    """PR #84 のデバウンス（押下 200ms / 離脱 500ms）が実 keyboard でも機能すること。

    高速連打（200ms 未満）では on_press が 1 回しか発火しないことを確認する。
    """
    press_calls = []

    def on_press(event):
        press_calls.append(event)

    mgr = PttHotkeyManager(
        hotkey="f8",
        on_press=on_press,
        on_release=None,
    )
    mgr.start()

    try:
        # 押下デバウンス 200ms 以内の高速連打（5 回）
        for _ in range(5):
            _inject_key_event("f8", "down")
            time.sleep(0.01)  # 10ms 間隔（デバウンス閾値 200ms より短い）

        # イベント処理待機
        time.sleep(0.1)

        # デバウンスにより 1 回しか発火しないこと
        assert len(press_calls) == 1, (
            f"デバウンスが機能していない: on_press が {len(press_calls)} 回発火した"
        )

    finally:
        mgr.stop()


# ===========================================================================
# テスト 4: app.py の callback 配線の E2E 検証
# ===========================================================================


@requires_keyboard_e2e
def test_e2e_app_callback_wired():
    """app._on_ptt_press 相当のコールバックが実 keyboard フック経由で呼ばれること。

    実 keyboard フックを登録した PttHotkeyManager に _on_ptt_press 相当の
    コールバックを設定し、_inject_key_event() で押下後に呼ばれたことを確認する。
    app.py の _init_ptt_manager が行う配線（hotkey + on_press + on_release）を
    同等条件で再現して E2E 検証する。
    """
    press_calls = []

    def mock_on_ptt_press(event):
        """app._on_ptt_press 相当のコールバック（GUI 呼び出しなし）。"""
        press_calls.append(event)

    mgr = PttHotkeyManager(
        hotkey="f8",
        on_press=mock_on_ptt_press,
        on_release=None,
    )
    mgr.start()

    try:
        _inject_key_event("f8", "down")
        time.sleep(0.1)
        assert len(press_calls) == 1, (
            f"app._on_ptt_press 相当のコールバックが呼ばれなかった (calls={press_calls})"
        )
    finally:
        mgr.stop()
