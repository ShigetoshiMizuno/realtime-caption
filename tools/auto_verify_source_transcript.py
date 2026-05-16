"""issue #121 問題 A（原文受信バグ）の実機検証を自動化するツール。

**Windows 専用**: TTS 発話に Windows SAPI (System.Speech) を使用する。
Linux / macOS では TTS がスキップされる（検証精度が低下する可能性あり）。

動作の流れ:
1. app.py --verbose --auto-konnyaku=N を subprocess 起動
2. Windows SAPI で英語フレーズを発話（loopback で経路 A が拾う）
3. アプリ終了を待つ
4. 生成された *_verbose.txt を探す
5. [RT_WS_RECV] イベント type 別カウント
6. session.input_transcript.delta 受信数で判定:
   - N > 0 → 原文受信あり → クライアント側 callback 配線問題
   - N == 0 → 原文受信なし → API 側問題 or 設定漏れ
7. log_anomaly_detector で他異常も検出
8. 統合レポート出力

使用例:
    python tools/auto_verify_source_transcript.py
    python tools/auto_verify_source_transcript.py --duration 30 --json
    python tools/auto_verify_source_transcript.py --exit-fail-on-issue
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Windows cp932 環境で ✓ / ✗ / 日本語をエンコードできない問題対策
# (実機実行で UnicodeEncodeError 検出 — Tool A/B/C と同じパターン)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from auto_e2e_route_a import _tts_speak
from log_anomaly_detector import check_translation_without_source, parse_log_lines

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

DEFAULT_DURATION = 30.0
DEFAULT_PHRASES = [
    "Hello world, this is a test of the automatic verification tool.",
    "How are you today? I hope everything is going well.",
    "Today is a sunny day and we are testing the transcription system.",
]
DEFAULT_VERBOSE_LOG_PATTERN = "*_verbose.txt"

# RT_WS_RECV 行のプリフィックス
_RT_WS_RECV_PREFIX = "[RT_WS_RECV] "

# JSON type キーの正規表現（高速マッチ用）
_RE_EVENT_TYPE = re.compile(r'"type"\s*:\s*"([^"]+)"')


# ---------------------------------------------------------------------------
# 純関数
# ---------------------------------------------------------------------------

def count_rt_ws_recv_events(log_text: str) -> dict[str, int]:
    """verbose ログから [RT_WS_RECV] 行を抽出し、event type 別にカウントする。

    純関数（副作用なし）。不正な JSON 行や type キーなし行はスキップする。

    Args:
        log_text: verbose ログファイルの全テキスト

    Returns:
        type 名 → 件数 の dict。例: {"session.input_transcript.delta": 3, ...}
    """
    counts: dict[str, int] = {}
    for line in log_text.splitlines():
        if not line.startswith(_RT_WS_RECV_PREFIX):
            continue
        json_part = line[len(_RT_WS_RECV_PREFIX):]
        # まず正規表現で type を高速抽出（JSON パースより速い）
        m = _RE_EVENT_TYPE.search(json_part)
        if m:
            event_type = m.group(1)
            counts[event_type] = counts.get(event_type, 0) + 1
            continue
        # 正規表現でマッチしなかった場合は JSON パースを試みる
        try:
            data = json.loads(json_part)
            event_type = data.get("type")
            if event_type:
                counts[event_type] = counts.get(event_type, 0) + 1
        except (json.JSONDecodeError, ValueError):
            pass  # 不正 JSON はスキップ
    return counts


def extract_new_verbose_files(before: set, after: set, pattern: str) -> set:
    """after - before の差分（新規生成ファイル）を返す。

    純関数（副作用なし）。

    Args:
        before: 起動前のファイルパス set
        after: 起動後のファイルパス set
        pattern: 使用した glob パターン（現在は参照のみ、将来の拡張用）

    Returns:
        新規生成されたファイルパスの set
    """
    return after - before


def determine_verdict(source_delta: int) -> tuple[str, str]:
    """source_delta_count に基づいて verdict と説明メッセージを返す。

    純関数（副作用なし）。

    Args:
        source_delta: session.input_transcript.delta の受信件数

    Returns:
        (verdict, message) のタプル
        verdict: "client_issue" または "api_issue"
        message: 人間向けの説明文
    """
    if source_delta > 0:
        return (
            "client_issue",
            f"API が原文イベント {source_delta} 件を送信している。"
            "クライアント側の callback 配線を確認推奨"
            "（session.input_transcript.delta の on_event callback が正しく登録されているか）",
        )
    else:
        return (
            "api_issue",
            "API が原文イベントを送信していない。"
            "gpt-realtime-translate の仕様または session.update の設定漏れ。"
            "session.update の audio.input.transcription.model が正しく送信されているか verbose ログで確認",
        )


def env_with_utf8() -> dict:
    """PYTHONIOENCODING=utf-8 を含む環境変数 dict を返す。

    既存の os.environ を引き継ぎ、PYTHONIOENCODING と PYTHONUNBUFFERED を上書きする。

    Returns:
        環境変数 dict
    """
    env = {**os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def format_report(result: dict, output_json: bool = False) -> str:
    """検証結果 dict をレポート文字列に変換する。

    Args:
        result: run_e2e_verification の戻り値 dict
        output_json: True なら JSON 形式、False なら人間向けテキスト

    Returns:
        レポート文字列
    """
    if output_json:
        return json.dumps(result, ensure_ascii=False, indent=2)

    lines: list[str] = []
    lines.append("=== 実機検証 自動化レポート (#121 問題 A) ===")

    status = result.get("status", "unknown")
    if status != "completed":
        lines.append(f"ステータス: {status}")
        error = result.get("error", "")
        if error:
            lines.append(f"エラー: {error}")
        return "\n".join(lines)

    duration = result.get("duration", 0)
    lines.append(f"実行時間: {duration:.1f}s")

    verbose_files = result.get("verbose_files", [])
    for vf in verbose_files:
        try:
            size = Path(vf).stat().st_size
            lines.append(f"生成 verbose ログ: {Path(vf).name} ({size} bytes)")
        except OSError:
            lines.append(f"生成 verbose ログ: {vf}")

    lines.append("")
    lines.append("== RT_WS_RECV イベント別カウント ==")
    event_counts = result.get("event_counts", {})
    if event_counts:
        max_len = max(len(k) for k in event_counts) if event_counts else 10
        for event_type, count in sorted(event_counts.items(), key=lambda x: -x[1]):
            marker = "  ← 原文イベント受信" if "input_transcript.delta" in event_type else ""
            lines.append(f"  {event_type:<{max_len}}: {count:5d} 件{marker}")
    else:
        lines.append("  （イベントなし）")

    lines.append("")
    rule5_count = result.get("rule5_anomalies", 0)
    lines.append("== Rule 5 (Translation without Source) 異常 ==")
    lines.append(f"  検出数: {rule5_count} 件")

    lines.append("")
    lines.append("== 判定 ==")
    verdict = result.get("verdict", "unknown")
    message = result.get("message", "")
    source_count = result.get("source_delta_count", 0)

    if verdict == "client_issue":
        lines.append(f"✓ API が原文イベントを送信している ({source_count} 件)")
        lines.append("  → クライアント側 callback 配線を再確認推奨")
        lines.append("")
        lines.append("issue #121 問題 A の原因特定: クライアント側")
    elif verdict == "api_issue":
        lines.append(f"✗ API が原文イベントを送信していない (0 件)")
        lines.append("  → gpt-realtime-translate の API 仕様または session.update の設定漏れ")
        lines.append("  → session.update の audio.input.transcription.model が正しく送信されているか verbose ログで確認")
        lines.append("")
        lines.append("issue #121 問題 A の原因特定: API / 設定側")
    else:
        lines.append(f"判定不明: {verdict}")
        lines.append(f"  {message}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def run_e2e_verification(
    duration: float,
    phrases: list[str],
    verbose_log_pattern: str,
) -> dict:
    """実機検証を実行し、結果 dict を返す。

    subprocess で app.py を起動し、TTS で英語フレーズを発話して
    verbose ログを解析する。

    Args:
        duration: 自動運転秒数
        phrases: TTS で発話する英語フレーズのリスト
        verbose_log_pattern: verbose ログの glob パターン

    Returns:
        検証結果 dict:
          status: "completed" または "no_verbose_log"
          verbose_files: 生成されたログファイルのリスト
          event_counts: RT_WS_RECV イベント別カウント dict
          source_delta_count: session.input_transcript.delta の件数
          rule5_anomalies: Rule 5 異常件数
          verdict: "client_issue" または "api_issue"
          message: 判定メッセージ
    """
    # Step 1: 起動前のファイル一覧を記録
    before_files = set(glob.glob(verbose_log_pattern))

    # Step 2: app.py を subprocess 起動
    print("[VERIFY] starting app.py --verbose --auto-konnyaku=...", flush=True)
    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "app.py"),
            "--verbose",
            f"--auto-konnyaku={int(duration)}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env_with_utf8(),
    )

    # Step 3: アプリ起動 + konnyaku モード有効化を待ってから TTS 発話
    time.sleep(5)
    for phrase in phrases:
        _tts_speak(phrase)
        time.sleep(2)

    # Step 4: アプリ終了を待つ
    try:
        proc.wait(timeout=duration + 30)
    except subprocess.TimeoutExpired:
        print("[VERIFY] timeout: killing app.py", flush=True)
        proc.kill()

    # Step 5: 新規生成された verbose ログを特定
    after_files = set(glob.glob(verbose_log_pattern))
    new_files = extract_new_verbose_files(before_files, after_files, verbose_log_pattern)

    if not new_files:
        return {
            "status": "no_verbose_log",
            "error": "verbose ログが見つかりません（app.py が正常に起動しなかった可能性があります）",
        }

    # Step 6: verbose ログを読み込んで解析
    log_parts = []
    for f in sorted(new_files):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                log_parts.append(fh.read())
        except OSError as e:
            print(f"[VERIFY] 警告: ログ読み込み失敗 {f}: {e}", flush=True)
    log_text = "".join(log_parts)

    event_counts = count_rt_ws_recv_events(log_text)
    source_delta = event_counts.get("session.input_transcript.delta", 0)

    # Step 7: log_anomaly_detector で Rule 5 異常検出
    events = parse_log_lines(log_text)
    rule5_anomalies = check_translation_without_source(events)

    # Step 8: 判定
    verdict, message = determine_verdict(source_delta)

    return {
        "status": "completed",
        "duration": duration,
        "verbose_files": sorted(new_files),
        "event_counts": event_counts,
        "source_delta_count": source_delta,
        "rule5_anomalies": len(rule5_anomalies),
        "verdict": verdict,
        "message": message,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list = None) -> argparse.Namespace:
    """CLI 引数を解析する。

    Args:
        argv: sys.argv[1:] に相当するリスト（テスト用に注入可能）

    Returns:
        Namespace オブジェクト
    """
    parser = argparse.ArgumentParser(
        description="issue #121 問題 A（原文受信バグ）の実機検証を自動化するツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION,
        help=f"自動運転秒数（デフォルト: {DEFAULT_DURATION}）",
    )
    parser.add_argument(
        "--phrases",
        type=str,
        default=None,
        help="TTS で発話するフレーズ群（カンマ区切り）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="JSON 形式で結果出力",
    )
    parser.add_argument(
        "--exit-fail-on-issue",
        action="store_true",
        default=False,
        help="異常検出時（verdict が api_issue 等）に exit code 1 で終了",
    )
    parser.add_argument(
        "--verbose-log-pattern",
        type=str,
        default=DEFAULT_VERBOSE_LOG_PATTERN,
        help=f"verbose ログ glob パターン（デフォルト: {DEFAULT_VERBOSE_LOG_PATTERN}）",
    )
    return parser.parse_args(argv)


def main(argv: list = None) -> int:
    """エントリーポイント。

    Args:
        argv: sys.argv[1:] に相当するリスト（テスト用）

    Returns:
        exit code (0 または 1)
    """
    args = parse_args(argv)

    if args.phrases:
        phrases = [p.strip() for p in args.phrases.split(",") if p.strip()]
    else:
        phrases = DEFAULT_PHRASES

    result = run_e2e_verification(
        duration=args.duration,
        phrases=phrases,
        verbose_log_pattern=args.verbose_log_pattern,
    )

    report = format_report(result, output_json=args.json)
    print(report)

    if args.exit_fail_on_issue:
        verdict = result.get("verdict", "")
        if verdict == "api_issue" or result.get("status") == "no_verbose_log":
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
