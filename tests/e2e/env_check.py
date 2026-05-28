"""
env_check.py — E2E 完全自動化の実行前提条件チェックモジュール

VB-CABLE デバイス・PowerShell・keyboard パッケージの有無を確認し、
不足があれば明確なエラーメッセージで exit code 2 で終了する。

Usage:
    python tests/e2e/env_check.py
    # または run_e2e_smoke.py の冒頭で run_all_checks() を呼ぶ

発注書: commissions/e2e-full-automation/L1-prg-designer/order.md §1
"""

from __future__ import annotations

import subprocess
import sys


def check_vbcable_devices() -> None:
    """VB-CABLE 仮想オーディオデバイスがインストール済みかチェックする。

    以下の 2 デバイスが両方存在することを確認:
    - CABLE Input (VB-Audio Virtual Cable)  ... 出力デバイス（TTS 先）
    - CABLE Output (VB-Audio Virtual Cable) ... 入力デバイス（マイク代替）

    Raises:
        RuntimeError: いずれかのデバイスが見つからない場合。
                      メッセージに不足デバイス名を含む。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


def check_powershell() -> None:
    """PowerShell が実行可能かチェックする。

    Raises:
        RuntimeError: powershell コマンドが見つからない場合。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


def check_keyboard_package() -> None:
    """Python の keyboard パッケージがインストール済みかチェックする。

    Raises:
        RuntimeError: keyboard パッケージが import できない場合。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


def run_all_checks() -> None:
    """全前提条件チェックをまとめて実行する。

    いずれかのチェックが失敗した場合は、エラーメッセージを表示して
    sys.exit(2) で終了する。

    全チェック通過時は正常返却（exit なし）。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


if __name__ == "__main__":
    run_all_checks()
    print("[ENV CHECK] 全前提条件チェック OK")
