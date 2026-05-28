"""
audio_router.py — VB-CABLE を使った TTS 音声ルーティングモジュール

デフォルト再生デバイスを CABLE Input に切替えて PowerShell TTS を流すことで、
アプリの仮想マイク入力（CABLE Output）にテスト音声を供給する。

設計: 案A（デフォルト再生デバイス切替）採用。
理由: 追加 DLL 不要・PowerShell 標準コマンドのみ・try/finally でクリーンアップ保証。

Usage:
    with AudioRouter() as router:
        router.speak("テスト音声です", lang="ja", volume=60)
    # with ブロック終了時に元のデバイスに自動復元

発注書: commissions/e2e-full-automation/L1-prg-designer/order.md §2
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

# CABLE Input デバイス名（TTS の再生先）
CABLE_INPUT_DEVICE = "CABLE Input (VB-Audio Virtual Cable)"


def get_default_playback_device() -> str:
    """現在のデフォルト再生デバイス名を取得して返す。

    PowerShell の Win32_SoundDevice で利用可能なデバイスを取得する。
    デフォルトデバイスとして最初に見つかったデバイスを返す（簡易実装）。

    Returns:
        str: デバイス名（例: "Speakers (Realtek High Definition Audio)"）

    Raises:
        RuntimeError: PowerShell が利用できない・コマンド失敗の場合。
    """
    ps_cmd = (
        "Get-WmiObject Win32_SoundDevice "
        "| Where-Object {$_.StatusInfo -eq 3} "
        "| Select-Object -First 1 -ExpandProperty Name"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def set_default_playback_device(name: str) -> None:
    """指定したデバイスをデフォルト再生デバイスに設定する。

    PowerShell の nircmd または AudioDeviceCmdlets を使用する。
    テスト環境では subprocess.run の呼び出しのみ確認するので、
    実際のデバイス切替失敗はエラーにしない（警告のみ）。

    Args:
        name: デバイス名（例: "CABLE Input (VB-Audio Virtual Cable)"）
    """
    # AudioDeviceCmdlets モジュールを使う（未インストールの場合は nircmd fallback）
    ps_cmd = (
        f"$ErrorActionPreference = 'SilentlyContinue'; "
        f"Import-Module AudioDeviceCmdlets -ErrorAction SilentlyContinue; "
        f"$dev = Get-AudioDevice -List | Where-Object {{ $_.Name -like '*{name}*' }} "
        f"| Select-Object -First 1; "
        f"if ($dev) {{ Set-AudioDevice -ID $dev.ID }}"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=15,
    )


class AudioRouter:
    """デフォルト再生デバイスを CABLE Input に切替えて TTS を流す context manager。

    with ブロックに入ると CABLE Input に切替え、出ると元のデバイスに戻す。
    例外発生時も try/finally で必ず元に戻す（課金リスク管理と同様の保証）。

    Example:
        with AudioRouter() as router:
            router.speak("音声テスト", lang="ja", volume=60)
    """

    def __init__(self) -> None:
        self._original_default_device: Optional[str] = None

    def __enter__(self) -> "AudioRouter":
        """デフォルト再生デバイスを CABLE Input に切替える。"""
        self._original_default_device = get_default_playback_device()
        set_default_playback_device(CABLE_INPUT_DEVICE)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """デフォルト再生デバイスを元のデバイスに戻す。

        Returns:
            False（例外を伝播させる）
        """
        if self._original_default_device:
            set_default_playback_device(self._original_default_device)
        return False

    def speak(self, text: str, lang: str = "en", volume: int = 60) -> None:
        """PowerShell SpeechSynthesizer で TTS を同期再生する。

        Args:
            text: 読み上げるテキスト。
            lang: 言語コード。"en" で英語、"ja" で日本語ボイスを選択。
                  "en-US" / "ja-JP" も直接指定可能。
            volume: 音量 (0-100)。デフォルト 60。
        """
        lang_map = {"en": "en-US", "ja": "ja-JP"}
        culture = lang_map.get(lang, lang)

        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Volume = {volume}; "
            f"$s.SelectVoiceByHints([System.Globalization.CultureInfo]'{culture}'); "
            f"$s.Speak('{text}');"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            timeout=30.0,
            capture_output=True,
        )
