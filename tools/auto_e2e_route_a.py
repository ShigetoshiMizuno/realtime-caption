"""
auto_e2e_route_a.py

経路A（loopback → ja 翻訳）の動作を AI で自動検証する E2E スクリプト。

仕組み:
  1. subprocess で app.py --auto-konnyaku=N --verbose を起動
  2. モデルロード完了待ち
  3. Windows SAPI で英語フレーズを発話
     → スピーカー（loopback）から音声が出る → 経路A が拾う
  4. app.py の終了を待つ
  5. verbose / translate ログを読んで経路A の翻訳結果を確認
  6. 真因の自動判定

依存: Windows のみ（SAPI 利用）

使い方:
    python tools/auto_e2e_route_a.py
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _tts_speak(text: str) -> None:
    """Windows PowerShell System.Speech で英語フレーズを発話。デフォルトの出力デバイスに流れる。"""
    # PowerShell コマンドで TTS、英語音声を選んで発話
    # 引用符のエスケープに注意（text 内のシングルクォートを２連にする）
    escaped = text.replace("'", "''")
    ps_command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        # 英語音声を選択（無ければ default のまま）
        "$en = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en*' } | Select-Object -First 1; "
        "if ($en) { $s.SelectVoice($en.VoiceInfo.Name) }; "
        f"$s.Speak('{escaped}')"
    )
    print(f"[TTS] '{text}'", flush=True)
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as e:
        print(f"[TTS] ERROR (rc={e.returncode}): {e.stderr.decode('utf-8', errors='replace')}", flush=True)
    except Exception as e:
        print(f"[TTS] ERROR: {e}", flush=True)


def _list_translate_logs() -> list[Path]:
    """ログディレクトリ内の今日の translate ログを返す（経路A・B 別々）。"""
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
    parser = argparse.ArgumentParser(description="経路A E2E 検証")
    parser.add_argument("--duration", type=int, default=40, help="app.py 実行時間（秒）")
    parser.add_argument(
        "--phrases", nargs="*",
        default=[
            "Good morning everyone, welcome to today's meeting.",
            "We will discuss the quarterly results.",
            "Please raise your hand if you have any questions.",
            "Thank you for your attention.",
        ],
        help="TTS で発話する英語フレーズ",
    )
    parser.add_argument("--no-app", action="store_true", help="app.py を起動しない（TTS のみ）")
    args = parser.parse_args()

    pre_translate_logs = set(_list_translate_logs())
    pre_verbose_logs = set(_list_verbose_logs())

    # app.py 起動
    if not args.no_app:
        env = {**os.environ, "PYTHONFAULTHANDLER": "1", "PYTHONUNBUFFERED": "1"}
        console_log = ROOT / f"console_e2e_route_a_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        with console_log.open("w", encoding="utf-8") as out:
            print(f"[E2E] starting app.py --auto-konnyaku={args.duration} --verbose", flush=True)
            print(f"[E2E] console log: {console_log}", flush=True)
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

        # モデルロード + プリセット適用 + 起動の待機
        print(f"[E2E] waiting 12s for app to start and konnyaku mode to activate...", flush=True)
        time.sleep(12)

    # TTS で英語フレーズ発話（speakers に流れる → loopback で拾われる）
    for phrase in args.phrases:
        _tts_speak(phrase)
        time.sleep(1.5)

    # app.py 終了待ち
    if not args.no_app:
        print(f"[E2E] waiting for app.py to exit...", flush=True)
        rc = app_proc.wait(timeout=args.duration + 30)
        print(f"[E2E] app.py exited with code {rc}", flush=True)

    # 新規ログを特定
    new_translate = sorted(set(_list_translate_logs()) - pre_translate_logs)
    new_verbose = sorted(set(_list_verbose_logs()) - pre_verbose_logs)
    print(f"\n[E2E] === 新規ログ ===", flush=True)
    for p in new_translate:
        print(f"  translate: {p.name} ({p.stat().st_size} bytes)", flush=True)
    for p in new_verbose:
        print(f"  verbose:   {p.name} ({p.stat().st_size} bytes)", flush=True)

    # 経路A / 経路B 判定: ファイル名に "-route-b" が含まれていない方を経路A とする
    route_a_translate = [p for p in new_translate if "-route-b" not in p.name]
    route_b_translate = [p for p in new_translate if "-route-b" in p.name]
    route_a_verbose = [p for p in new_verbose if "-route-b" not in p.name]
    route_b_verbose = [p for p in new_verbose if "-route-b" in p.name]
    print(f"  route_a translate: {[p.name for p in route_a_translate]}", flush=True)
    print(f"  route_a verbose:   {[p.name for p in route_a_verbose]}", flush=True)
    print(f"  route_b translate: {[p.name for p in route_b_translate]}", flush=True)
    print(f"  route_b verbose:   {[p.name for p in route_b_verbose]}", flush=True)

    print(f"\n[E2E] === 経路A の解析 ===", flush=True)
    a_translated = []
    for p in route_a_translate:
        text = _read_log(p)
        a_translated.extend(re.findall(r"翻訳\(RT\):\s*(.+)", text))
        a_original = re.findall(r"原文\(RT\):\s*(.+)", text)
        print(f"  {p.name}: 原文={len(a_original)} 翻訳={len(a_translated)}件", flush=True)
        for o in a_original[:3]:
            print(f"    原文: {o}", flush=True)
        for t in a_translated[:3]:
            print(f"    翻訳: {t}", flush=True)

    print(f"\n[E2E] === 経路B の解析 ===", flush=True)
    b_translated = []
    for p in route_b_translate:
        text = _read_log(p)
        b_translated.extend(re.findall(r"翻訳\(RT\):\s*(.+)", text))
        b_original = re.findall(r"原文\(RT\):\s*(.+)", text)
        print(f"  {p.name}: 原文={len(b_original)} 翻訳={len(b_translated)}件", flush=True)

    # 自動判定
    print(f"\n[E2E] === 自動判定 ===", flush=True)
    if len(a_translated) > 0:
        print(f"  [OK] 経路A 翻訳成功: {len(a_translated)} 件の翻訳を確認", flush=True)
        verdict = 0
    else:
        print(f"  [NG] 経路A 翻訳が出ていない（真因継続）", flush=True)
        # verbose ログから RT_DELTA / RT_SOURCE_DELTA を確認
        for p in route_a_verbose:
            text = _read_log(p)
            n_connect = text.count("RT_CONNECT")
            n_source = text.count("RT_SOURCE_DELTA")
            n_delta = text.count("RT_DELTA")
            n_done = text.count("RT_DONE")
            n_error = text.count("RT_ERROR")
            print(f"  {p.name}: RT_CONNECT={n_connect}, RT_SOURCE_DELTA={n_source}, "
                  f"RT_DELTA={n_delta}, RT_DONE={n_done}, RT_ERROR={n_error}", flush=True)
        verdict = 1

    return verdict


if __name__ == "__main__":
    sys.exit(main())
