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

    PowerShell の Get-AudioDevice または AudioDeviceCmdlets で取得する。

    Returns:
        str: デバイス名（例: "Speakers (Realtek High Definition Audio)"）

    Raises:
        RuntimeError: PowerShell が利用できない・コマンド失敗の場合。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


def set_default_playback_device(name: str) -> None:
    """指定したデバイスをデフォルト再生デバイスに設定する。

    PowerShell の Set-AudioDevice または AudioDeviceCmdlets で設定する。

    Args:
        name: デバイス名（例: "CABLE Input (VB-Audio Virtual Cable)"）

    Raises:
        RuntimeError: PowerShell が利用できない・デバイスが見つからない場合。
    """
    raise NotImplementedError("TODO: prg-impl が実装する")


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
        raise NotImplementedError("TODO: prg-impl が実装する")

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """デフォルト再生デバイスを元のデバイスに戻す。

        Returns:
            False（例外を伝播させる）
        """
        raise NotImplementedError("TODO: prg-impl が実装する")

    def speak(self, text: str, lang: str = "en", volume: int = 60) -> None:
        """PowerShell SpeechSynthesizer で TTS を同期再生する。

        Args:
            text: 読み上げるテキスト。
            lang: 言語コード。"en" で英語、"ja" で日本語ボイスを選択。
                  実際には "en-US" / "ja-JP" に変換して SpeechSynthesizer に渡す。
            volume: 音量 (0-100)。デフォルト 60。
        """
        raise NotImplementedError("TODO: prg-impl が実装する")
