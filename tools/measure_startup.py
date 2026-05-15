"""アプリ起動時間を計測するツール。

[STARTUP] プリフィックスのログから各ステップの所要時間を抽出し、表形式で表示。
起動最適化のリグレッション検出に使用。

使用例:
    python tools/measure_startup.py
    python tools/measure_startup.py --threshold-total 10 --threshold-step 3
    python tools/measure_startup.py --timeout 60 --json
"""

import argparse
import json
import re
import subprocess
import sys
import threading
from pathlib import Path

# Windows cp932 環境でログの日本語ステップ名・⚠ マーカーをエンコードできない問題対策
# (実機検証で文字化け検出)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# プロジェクトルートを sys.path に追加
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# [STARTUP] <ステップ名> 完了 (X.XXs) の正規表現
# [STARTUP][ERROR] 行や 全体起動時間 行は除外する
_RE_STEP = re.compile(
    r"^\[STARTUP\]\s+(?!\[ERROR\])(.+?)\s+完了\s+\((\d+\.\d+)s\)\s*$"
)
# [STARTUP] 全体起動時間: X.XXs
_RE_TOTAL = re.compile(
    r"^\[STARTUP\]\s+全体起動時間:\s+(\d+\.\d+)s\s*$"
)


def parse_startup_log(stdout: str) -> dict[str, float]:
    """ログテキストから各ステップの所要時間を抽出する。

    Args:
        stdout: アプリの標準出力テキスト。

    Returns:
        {step_name: duration_seconds} の dict。
        全体起動時間行・エラー行は含まれない。
    """
    steps: dict[str, float] = {}
    for line in stdout.splitlines():
        m = _RE_STEP.match(line)
        if m:
            step_name = m.group(1)
            duration = float(m.group(2))
            steps[step_name] = duration
    return steps


def parse_total_time(stdout: str) -> float | None:
    """ログテキストから全体起動時間を抽出する。

    Args:
        stdout: アプリの標準出力テキスト。

    Returns:
        全体起動時間（秒）、または行がなければ None。
    """
    for line in stdout.splitlines():
        m = _RE_TOTAL.match(line)
        if m:
            return float(m.group(1))
    return None


def format_report(
    steps: dict[str, float],
    total: float,
    threshold_step: float,
    threshold_total: float,
    output_json: bool = False,
) -> str:
    """計測結果をフォーマットする。

    Args:
        steps: {step_name: duration} の dict。
        total: 全体起動時間（秒）。
        threshold_step: 個別ステップの警告閾値（秒）。
        threshold_total: 全体時間の警告閾値（秒）。
        output_json: True のとき JSON 形式で返す。

    Returns:
        フォーマットされた文字列（テキスト or JSON）。
    """
    threshold_exceeded = any(v > threshold_step for v in steps.values()) or total > threshold_total

    if output_json:
        data = {
            "steps": steps,
            "total_time": total,
            "threshold_exceeded": threshold_exceeded,
            "thresholds": {
                "step": threshold_step,
                "total": threshold_total,
            },
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    # テキスト形式
    lines = []
    lines.append("=== 起動時間計測結果 ===")
    lines.append(f"{'ステップ':<32}  {'時間':>8}")
    lines.append("-" * 45)
    for name, duration in steps.items():
        warn = " ⚠" if duration > threshold_step else ""
        lines.append(f"{name:<32}  {duration:.2f}s{warn}")
    lines.append("-" * 45)
    total_warn = " ⚠" if total > threshold_total else ""
    lines.append(f"{'全体起動時間':<32}  {total:.2f}s{total_warn}")
    return "\n".join(lines)


def run_measurement(timeout: float, no_gui: bool) -> tuple[dict[str, float], float | None]:
    """アプリを subprocess で起動して起動時間を計測する。

    Args:
        timeout: アプリ起動完了を待つタイムアウト（秒）。
        no_gui: True のとき --no-gui フラグをアプリに渡す。

    Returns:
        (steps, total_time) のタプル。total_time は取得できなければ None。
    """
    app_py = _ROOT / "app.py"
    cmd = [sys.executable, str(app_py)]
    if no_gui:
        cmd.append("--no-gui")

    # 子プロセスの stdout を強制的に utf-8 にする（Windows cp932 対策）
    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    stdout_lines: list[str] = []
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_ROOT),
        env=env,
    )

    total_found = threading.Event()

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_lines.append(line)
            if _RE_TOTAL.match(line.rstrip()):
                total_found.set()

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    # 全体起動時間ログが来るか timeout するまで待つ
    total_found.wait(timeout=timeout)

    if not total_found.is_set():
        proc.kill()

    reader_thread.join(timeout=5)
    # PR #112 QA W-1 + 実機検証で再現: kill 後でも子プロセスが 5 秒以内に終了しない
    # ケース (Windows + GUI app) があるため、TimeoutExpired を握って強制 kill する
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass  # 諦め

    stdout_text = "".join(stdout_lines)
    steps = parse_startup_log(stdout_text)
    total = parse_total_time(stdout_text)
    return steps, total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="アプリ起動時間を計測して表形式で表示する",
    )
    parser.add_argument(
        "--threshold-total",
        type=float,
        default=10.0,
        metavar="FLOAT",
        help="全体起動時間の警告閾値（デフォルト: 10.0 秒）",
    )
    parser.add_argument(
        "--threshold-step",
        type=float,
        default=3.0,
        metavar="FLOAT",
        help="個別ステップの警告閾値（デフォルト: 3.0 秒）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        metavar="FLOAT",
        help="アプリ起動完了を待つタイムアウト（デフォルト: 60.0 秒）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="JSON 形式で出力（CI 用）",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="GUI を立ち上げないモード",
    )
    args = parser.parse_args()

    steps, total = run_measurement(timeout=args.timeout, no_gui=args.no_gui)

    if total is None:
        print("[measure_startup] タイムアウト: 全体起動時間ログを取得できませんでした", file=sys.stderr)
        total = sum(steps.values())

    report = format_report(
        steps,
        total,
        threshold_step=args.threshold_step,
        threshold_total=args.threshold_total,
        output_json=args.output_json,
    )
    print(report)

    # 閾値超過があれば exit code 1
    threshold_exceeded = any(v > args.threshold_step for v in steps.values()) or total > args.threshold_total
    return 1 if threshold_exceeded else 0


if __name__ == "__main__":
    sys.exit(main())
