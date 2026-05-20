"""
auto_e2e_route_b.py

経路B（マイク → 英語翻訳）の動作を自動検証する E2E スクリプト。
PTT の代わりに経路B を直接起動し、日本語 TTS で発話して英語翻訳を確認する。

仕組み:
  1. app.py --auto-konnyaku=N --verbose を起動（route_b.enabled=True, ptt_enabled=False が前提）
  2. 日本語フレーズを Windows SAPI（日本語音声）で発話
  3. verbose / translate ログを読んで経路B の翻訳結果を確認
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _tts_speak_ja(text: str) -> None:
    """Windows PowerShell System.Speech で日本語フレーズを発話。日本語音声を優先選択。"""
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
    except subprocess.CalledProcessError as e:
        print(f"[TTS-JA] ERROR (rc={e.returncode}): {e.stderr.decode('utf-8', errors='replace')}", flush=True)
    except Exception as e:
        print(f"[TTS-JA] ERROR: {e}", flush=True)


def _list_translate_logs() -> list[Path]:
    today = datetime.now().strftime("%Y-%m-%d")
    return sorted(ROOT.glob(f"{today}-*_translate.txt"))


def _list_verbose_logs() -> list[Path]:
    today = datetime.now().strftime("%Y-%m-%d")
    return sorted(ROOT.glob(f"{today}-*_verbose.txt"))


def _read_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="経路B E2E 検証（日本語→英語翻訳）")
    parser.add_argument("--duration", type=int, default=60, help="app.py 実行時間（秒）")
    parser.add_argument(
        "--phrases", nargs="*",
        default=[
            "おはようございます、本日の会議を始めます。",
            "本日の議題は四半期の業績についてです。",
            "ご質問がある方は手を挙げてください。",
            "ご清聴ありがとうございました。",
        ],
        help="TTS で発話する日本語フレーズ",
    )
    parser.add_argument("--no-app", action="store_true", help="app.py を起動しない（TTS のみ）")
    args = parser.parse_args()

    pre_translate_logs = set(_list_translate_logs())
    pre_verbose_logs = set(_list_verbose_logs())

    if not args.no_app:
        env = {**os.environ, "PYTHONFAULTHANDLER": "1", "PYTHONUNBUFFERED": "1"}
        console_log = ROOT / f"console_e2e_route_b_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        with console_log.open("w", encoding="utf-8") as out:
            print(f"[E2E-B] starting app.py --auto-konnyaku={args.duration} --verbose", flush=True)
            print(f"[E2E-B] console log: {console_log}", flush=True)
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

        print(f"[E2E-B] waiting 12s for app to start...", flush=True)
        time.sleep(12)

    for phrase in args.phrases:
        _tts_speak_ja(phrase)
        time.sleep(1.5)

    if not args.no_app:
        print(f"[E2E-B] waiting for app.py to exit...", flush=True)
        rc = app_proc.wait(timeout=args.duration + 30)
        print(f"[E2E-B] app.py exited with code {rc}", flush=True)

    new_translate = sorted(set(_list_translate_logs()) - pre_translate_logs)
    new_verbose = sorted(set(_list_verbose_logs()) - pre_verbose_logs)

    print(f"\n[E2E-B] === 新規ログ ===", flush=True)
    for p in new_translate:
        print(f"  translate: {p.name} ({p.stat().st_size} bytes)", flush=True)
    for p in new_verbose:
        print(f"  verbose:   {p.name} ({p.stat().st_size} bytes)", flush=True)

    route_b_translate = [p for p in new_translate if "-route-b" in p.name]
    route_b_verbose = [p for p in new_verbose if "-route-b" in p.name]

    print(f"  route_b translate: {[p.name for p in route_b_translate]}", flush=True)
    print(f"  route_b verbose:   {[p.name for p in route_b_verbose]}", flush=True)

    print(f"\n[E2E-B] === 経路B の解析 ===", flush=True)
    b_translated = []
    for p in route_b_translate:
        text = _read_log(p)
        b_translated.extend(re.findall(r"翻訳\(RT\):\s*(.+)", text))
        b_original = re.findall(r"原文\(RT\):\s*(.+)", text)
        print(f"  {p.name}: 原文={len(b_original)} 翻訳={len(b_translated)}件", flush=True)
        for o in b_original[:3]:
            print(f"    原文: {o}", flush=True)
        for t in b_translated[:3]:
            print(f"    翻訳: {t}", flush=True)

    print(f"\n[E2E-B] === 自動判定 ===", flush=True)
    if len(b_translated) > 0:
        print(f"  [OK] 経路B 翻訳成功: {len(b_translated)} 件の翻訳を確認", flush=True)
        verdict = 0
    else:
        print(f"  [NG] 経路B 翻訳が出ていない（継続調査）", flush=True)
        for p in route_b_verbose:
            text = _read_log(p)
            n_connect = text.count("RT_CONNECT")
            n_delta = text.count("RT_DELTA")
            n_done = text.count("RT_DONE")
            n_error = text.count("RT_ERROR")
            print(f"  {p.name}: RT_CONNECT={n_connect}, RT_DELTA={n_delta}, RT_DONE={n_done}, RT_ERROR={n_error}", flush=True)
        verdict = 1

    return verdict


if __name__ == "__main__":
    sys.exit(main())
