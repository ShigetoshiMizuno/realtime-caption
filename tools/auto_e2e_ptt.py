"""
auto_e2e_ptt.py

PTT（Push-To-Talk）動作の自動検証スクリプト。
keyboard モジュールで F8 キーを擬似押下し、route_b の start/stop サイクルを確認する。

前提:
  - settings.json: route_b.ptt_enabled=True, route_b.ptt_hotkey="f8"
  - app.py が起動済みであること、もしくは --no-app フラグで外部起動

使い方:
    python tools/auto_e2e_ptt.py --duration 60
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False


def _simulate_ptt(hotkey: str, hold_secs: float, speak_fn=None, post_speak_secs: float = 4.0):
    """指定キーを press → (hold_secs 秒間待機) → release でシミュレート。

    post_speak_secs: 発話後に PTT を離すまでの待機時間。
    VAD(0.6s) + OpenAI API 処理(~2s) を考慮して 4.0s がデフォルト。
    """
    if not KEYBOARD_AVAILABLE:
        print(f"[PTT-SIM] keyboard モジュール未インストール。シミュレーション不可", flush=True)
        return
    print(f"[PTT-SIM] {hotkey} press", flush=True)
    keyboard.press(hotkey)
    time.sleep(0.3)  # アプリが PTT を検知する猶予
    if speak_fn:
        speak_fn()
        time.sleep(post_speak_secs)  # VAD + API 処理を待つ
    else:
        time.sleep(hold_secs)
    print(f"[PTT-SIM] {hotkey} release", flush=True)
    keyboard.release(hotkey)


def _tts_speak_ja(text: str) -> None:
    """日本語 TTS 発話。"""
    escaped = text.replace("'", "''")
    ps_command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$ja = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'ja*' } | Select-Object -First 1; "
        "if ($ja) { $s.SelectVoice($ja.VoiceInfo.Name) }; "
        f"$s.Speak('{escaped}')"
    )
    print(f"[TTS-JA] '{text}'", flush=True)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        print(f"[TTS-JA] ERROR: {e}", flush=True)


def _tts_speak_en(text: str) -> None:
    """英語 TTS 発話。"""
    escaped = text.replace("'", "''")
    ps_command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$en = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en*' } | Select-Object -First 1; "
        "if ($en) { $s.SelectVoice($en.VoiceInfo.Name) }; "
        f"$s.Speak('{escaped}')"
    )
    print(f"[TTS-EN] '{text}'", flush=True)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        print(f"[TTS-EN] ERROR: {e}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="PTT 動作 E2E テスト")
    parser.add_argument("--duration", type=int, default=40, help="app.py 実行時間（秒）")
    parser.add_argument("--hotkey", default="f8", help="PTT ホットキー（デフォルト: f8）")
    parser.add_argument("--no-app", action="store_true", help="app.py を起動しない")
    args = parser.parse_args()

    if not KEYBOARD_AVAILABLE:
        print("[ERROR] keyboard モジュールが未インストールです: pip install keyboard", flush=True)
        return 2

    if not args.no_app:
        env = {**os.environ, "PYTHONFAULTHANDLER": "1", "PYTHONUNBUFFERED": "1"}
        console_log = ROOT / f"console_e2e_ptt_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        with console_log.open("w", encoding="utf-8") as out:
            print(f"[E2E-PTT] starting app.py --auto-konnyaku={args.duration} --verbose", flush=True)
            print(f"[E2E-PTT] console log: {console_log}", flush=True)
            app_proc = subprocess.Popen(
                [
                    str(ROOT / "python" / "python.exe"),
                    "-X", "faulthandler",
                    str(ROOT / "app.py"),
                    "--auto-konnyaku", str(args.duration),
                    "--verbose",
                ],
                stdout=out,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(ROOT),
            )

        print(f"[E2E-PTT] waiting 12s for app to start...", flush=True)
        time.sleep(12)

    # Route A テスト用: スピーカーから流す英語フレーズ（ループバックに乗る）
    en_phrases = [
        "Hello, how is the meeting going today?",
        "Let's discuss the progress of the product development.",
    ]
    # Route B テスト用: PTT で発話する日本語フレーズ
    phrases = [
        "本日の会議を始めます。よろしくお願いします。",
        "今日の議題は製品の開発状況についてです。",
    ]

    for en_phrase, phrase in zip(en_phrases, phrases):
        # Route A テスト用: 英語をスピーカーから再生（ループバックに乗る）
        _tts_speak_en(en_phrase)
        time.sleep(1.0)

        # Route B テスト用: F8 を押しながら日本語 TTS
        _simulate_ptt(
            args.hotkey,
            hold_secs=5.0,
            speak_fn=lambda p=phrase: _tts_speak_ja(p),
        )
        time.sleep(2.0)

    if not args.no_app:
        print(f"[E2E-PTT] waiting for app.py to exit...", flush=True)
        rc = app_proc.wait(timeout=args.duration + 30)
        print(f"[E2E-PTT] app.py exited with code {rc}", flush=True)

        # コンソールログの PTT 関連イベントを確認
        log_text = console_log.read_text(encoding="utf-8", errors="replace")
        ptt_press = log_text.count("[PTT] press: route_b 起動")
        ptt_release = log_text.count("[PTT] release: route_b 停止")
        b_started = log_text.count("CaptionSystem(route_id=b) idle -> starting")
        b_stopped = log_text.count("CaptionSystem(route_id=b) running -> stopping")
        translation_count = log_text.count("[翻訳(RT)]")

        print(f"\n[E2E-PTT] === 結果 ===", flush=True)
        print(f"  PTT press イベント: {ptt_press}", flush=True)
        print(f"  PTT release イベント: {ptt_release}", flush=True)
        print(f"  route_b start サイクル: {b_started}", flush=True)
        print(f"  route_b stop サイクル: {b_stopped}", flush=True)
        print(f"  翻訳出力ログ ([翻訳(RT)]): {translation_count}", flush=True)

        if ptt_press >= 2 and ptt_release >= 2 and b_started >= 2 and b_stopped >= 2:
            print(f"  [OK] PTT start/stop サイクルが {ptt_press} 回確認できました", flush=True)
            return 0
        else:
            print(f"  [NG] PTT サイクルが期待通りでない", flush=True)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
