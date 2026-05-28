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
    ps_cmd = "Get-WmiObject Win32_SoundDevice | Select-Object -ExpandProperty Name"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=15,
    )
    device_list = result.stdout

    cable_input = "CABLE Input (VB-Audio Virtual Cable)"
    cable_output = "CABLE Output (VB-Audio Virtual Cable)"

    if cable_input not in device_list:
        raise RuntimeError(
            f"VB-CABLE デバイスが見つかりません: {cable_input}\n"
            "VB-CABLE をインストールしてください: https://vb-audio.com/Cable/"
        )
    if cable_output not in device_list:
        raise RuntimeError(
            f"VB-CABLE デバイスが見つかりません: {cable_output}\n"
            "VB-CABLE をインストールしてください: https://vb-audio.com/Cable/"
        )


def check_powershell() -> None:
    """PowerShell が実行可能かチェックする。

    Raises:
        RuntimeError: powershell コマンドが見つからない場合。
    """
    try:
        subprocess.run(
            ["powershell", "-Command", "$PSVersionTable.PSVersion.Major"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "PowerShell が見つかりません。PowerShell がインストールされているか確認してください。"
        ) from exc


def check_keyboard_package() -> None:
    """Python の keyboard パッケージがインストール済みかチェックする。

    Raises:
        RuntimeError: keyboard パッケージが import できない場合。
    """
    # patch.dict("sys.modules", {"keyboard": None}) によるテスト用モック対応
    keyboard_mod = sys.modules.get("keyboard", "NOT_CHECKED")
    if keyboard_mod is None:
        raise RuntimeError(
            "keyboard パッケージが未インストールです。"
            "pip install keyboard を実行してください。"
        )
    if keyboard_mod == "NOT_CHECKED":
        try:
            import keyboard  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "keyboard パッケージが未インストールです。"
                "pip install keyboard を実行してください。"
            ) from exc


def run_all_checks() -> None:
    """全前提条件チェックをまとめて実行する。

    いずれかのチェックが失敗した場合は、エラーメッセージを表示して
    sys.exit(2) で終了する。

    全チェック通過時は正常返却（exit なし）。
    """
    checks = [
        ("VB-CABLE デバイス", check_vbcable_devices),
        ("PowerShell", check_powershell),
        ("keyboard パッケージ", check_keyboard_package),
    ]
    for name, check_fn in checks:
        try:
            check_fn()
        except RuntimeError as exc:
            print(f"[ENV CHECK ERROR] {name}: {exc}")
            sys.exit(2)


if __name__ == "__main__":
    run_all_checks()
    print("[ENV CHECK] 全前提条件チェック OK")
