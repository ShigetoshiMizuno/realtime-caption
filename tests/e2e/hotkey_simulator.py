"""
hotkey_simulator.py — OS キーボードイベントの仮想送信モジュール

keyboard ライブラリを使って F8 等のホットキーを仮想送信する。
管理者権限が必要な場合があるため、実行時に権限チェックも提供する。

Usage:
    press_hotkey("f8")         # F8 押下
    release_hotkey("f8")       # F8 離脱
    press_and_hold("f8", 5.0)  # F8 を 5 秒間押し続ける

発注書: commissions/e2e-full-automation/L1-prg-designer/order.md §3
"""

from __future__ import annotations

import time

# keyboard パッケージは管理者権限が必要な場合がある。
# インポートに失敗してもモジュール自体のロードは成功させ、
# 実行時に RuntimeError を発生させる設計にする。
try:
    import keyboard as _keyboard
    _KEYBOARD_AVAILABLE = True
except ImportError:
    _keyboard = None  # type: ignore[assignment]
    _KEYBOARD_AVAILABLE = False


def check_admin_privileges() -> bool:
    """現在のプロセスが管理者権限を持つかチェックする。

    Windows では ctypes.windll.shell32.IsUserAnAdmin() を使用。

    Returns:
        bool: 管理者権限あり → True、なし → False。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


def press_hotkey(key: str = "f8") -> None:
    """指定キーを OS レベルで押下する（keyboard.press() のラッパー）。

    Args:
        key: キー名（例: "f8", "ctrl", "alt"）。デフォルトは "f8"。

    Raises:
        RuntimeError: keyboard パッケージが未インストールの場合。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


def release_hotkey(key: str = "f8") -> None:
    """指定キーを OS レベルで離脱する（keyboard.release() のラッパー）。

    Args:
        key: キー名（例: "f8"）。デフォルトは "f8"。

    Raises:
        RuntimeError: keyboard パッケージが未インストールの場合。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


def press_and_hold(key: str = "f8", duration: float = 5.0) -> None:
    """指定キーを押下して duration 秒間保持してから離脱する。

    Args:
        key: キー名。デフォルトは "f8"。
        duration: 保持する秒数。デフォルトは 5.0 秒。

    Raises:
        RuntimeError: keyboard パッケージが未インストールの場合。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")
