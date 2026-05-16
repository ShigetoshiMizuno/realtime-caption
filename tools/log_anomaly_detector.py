"""ログファイルから「押したのに処理が走らない」等の異常パターンを検出するツール。

使用例:
    python tools/log_anomaly_detector.py console_20260516-115732.log
    python tools/log_anomaly_detector.py console_*.log --threshold 5.0
    python tools/log_anomaly_detector.py --stdin --json  # パイプ入力

検出ルール:
  Rule 1: [USER] → 次の N 行以内に [ACTION] がなければ異常
  Rule 2: [STATE] stopping → 次の N 行以内に -> idle がなければ異常
  Rule 4: [RPC] → 次の N 行以内に [ACTION] がなければ異常
  Rule 5: [翻訳(RT)] → 前後 N 行以内に [原文(RT)] がなければ異常
  Rule 6: [GUI_CALLBACK] start=X → 対応する end=X または error=X がなければ異常
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# ログ解析
# ---------------------------------------------------------------------------

# ログ行フォーマット: [PREFIX] message
_PREFIX_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)")


def parse_log_lines(text: str) -> list:
    """ログテキストを構造化イベントのリストに変換する。

    各イベントは以下のキーを持つ dict:
      - lineno: int  行番号（1始まり）
      - prefix: str  例 "USER", "ACTION", "STATE", "翻訳(RT)"
      - message: str プリフィックス以降のメッセージ
      - raw: str     元の行テキスト（末尾改行なし）

    プリフィックスを持たない行は結果に含まれない。

    Args:
        text: ログファイルの全テキスト

    Returns:
        解析済みイベントのリスト
    """
    events = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = _PREFIX_RE.match(line)
        if m:
            events.append({
                "lineno": lineno,
                "prefix": m.group(1),
                "message": m.group(2),
                "raw": line,
            })
    return events


# ---------------------------------------------------------------------------
# 汎用ペアリング検証
# ---------------------------------------------------------------------------

def find_pair_anomalies(
    events: list,
    trigger_prefix: str,
    expected_prefix: str,
    window: int,
    rule_name: str = None,
) -> list:
    """trigger_prefix のイベントの後 window 行以内に expected_prefix が来ない場合を検出。

    Args:
        events: parse_log_lines の結果
        trigger_prefix: トリガーとなるプリフィックス（例: "USER"）
        expected_prefix: 期待されるプリフィックス（例: "ACTION"）
        window: トリガーから期待イベントまでの最大行数
        rule_name: anomaly の rule フィールドに使う名前

    Returns:
        anomaly の list。各要素は dict:
          - rule: str
          - lineno: int (trigger の行番号)
          - trigger: str (trigger のメッセージ)
    """
    if rule_name is None:
        rule_name = f"{trigger_prefix}→{expected_prefix} 欠落"

    anomalies = []
    trigger_indices = [
        i for i, e in enumerate(events) if e["prefix"] == trigger_prefix
    ]

    for idx in trigger_indices:
        trigger_event = events[idx]
        trigger_lineno = trigger_event["lineno"]

        # idx+1 以降で window 行以内に expected_prefix を探す
        found = False
        for j in range(idx + 1, len(events)):
            candidate = events[j]
            if candidate["lineno"] - trigger_lineno > window:
                break
            if candidate["prefix"] == expected_prefix:
                found = True
                break

        if not found:
            anomalies.append({
                "rule": rule_name,
                "lineno": trigger_lineno,
                "trigger": f"[{trigger_prefix}] {trigger_event['message']}",
            })

    return anomalies


# ---------------------------------------------------------------------------
# Rule 1: USER → ACTION ペアリング
# ---------------------------------------------------------------------------

def check_user_action_pairs(events: list, window: int = 20) -> list:
    """Rule 1: [USER] の後 window 行以内に [ACTION] がなければ異常。

    Args:
        events: parse_log_lines の結果
        window: 許容行数（デフォルト 20）

    Returns:
        anomaly リスト
    """
    return find_pair_anomalies(
        events,
        trigger_prefix="USER",
        expected_prefix="ACTION",
        window=window,
        rule_name="USER→ACTION 欠落",
    )


# ---------------------------------------------------------------------------
# Rule 4: RPC → ACTION ペアリング
# ---------------------------------------------------------------------------

def check_rpc_action_pairs(events: list, window: int = 20) -> list:
    """Rule 4: [RPC] の後 window 行以内に [ACTION] がなければ異常。

    Args:
        events: parse_log_lines の結果
        window: 許容行数（デフォルト 20）

    Returns:
        anomaly リスト
    """
    return find_pair_anomalies(
        events,
        trigger_prefix="RPC",
        expected_prefix="ACTION",
        window=window,
        rule_name="RPC→ACTION 欠落",
    )


# ---------------------------------------------------------------------------
# Rule 2/3: STATE 遷移タイムアウト
# ---------------------------------------------------------------------------

def check_state_transitions(events: list, window: int = 20) -> list:
    """Rule 3: [STATE] stopping → idle が window 行以内に来なければ異常。

    Args:
        events: parse_log_lines の結果
        window: 許容行数（デフォルト 20）

    Returns:
        anomaly リスト
    """
    anomalies = []
    state_events = [e for e in events if e["prefix"] == "STATE"]

    # "stopping" 状態のイベントを探し、対応する "-> idle" を探す
    for i, ev in enumerate(state_events):
        msg = ev["message"]
        # "CaptionSystem(route_id=X) ... -> stopping" を検出
        if "-> stopping" not in msg:
            continue

        # route_id を抽出
        route_match = re.search(r"route_id=(\w+)", msg)
        route_id = route_match.group(1) if route_match else "?"

        trigger_lineno = ev["lineno"]
        found = False

        # events 全体から、trigger 以降で window 行以内の
        # 同 route_id の "stopping -> idle" を探す
        for j, other in enumerate(events):
            if other["lineno"] <= trigger_lineno:
                continue
            if other["lineno"] - trigger_lineno > window:
                break
            if other["prefix"] == "STATE":
                other_route = re.search(r"route_id=(\w+)", other["message"])
                other_route_id = other_route.group(1) if other_route else "?"
                if other_route_id == route_id and "stopping -> idle" in other["message"]:
                    found = True
                    break

        if not found:
            anomalies.append({
                "rule": "STATE stopping→idle タイムアウト",
                "lineno": trigger_lineno,
                "trigger": f"[STATE] {msg}",
            })

    return anomalies


# ---------------------------------------------------------------------------
# Rule 6: GUI_CALLBACK ペアリング検出
# ---------------------------------------------------------------------------

_RE_GUI_CALLBACK_START = re.compile(r"start=(\S+)")
_RE_GUI_CALLBACK_END = re.compile(r"(?:end|error)=(\S+)")


def check_gui_callback_pairs(events: list, window: int = 50) -> list:
    """Rule 6: GUI_CALLBACK start=X に対応する end=X または error=X がなければ異常。

    対応しない場合は「コールバックがハング・タイムアウトした」と判定。

    Args:
        events: parse_log_lines の結果
        window: 未使用（将来の行数ウィンドウ制限用に予約。現在は全ログを対象とする）

    Returns:
        anomaly リスト。各要素は dict:
          - rule: str
          - lineno: int (start の行番号)
          - name: str (コールバック名)
          - description: str
    """
    anomalies = []
    # 未マッチの start スタック: {"name": str, "lineno": int, "idx": int}
    starts: list[dict] = []

    for i, ev in enumerate(events):
        if ev["prefix"] != "GUI_CALLBACK":
            continue
        msg = ev["message"]

        m_start = _RE_GUI_CALLBACK_START.match(msg)
        if m_start:
            starts.append({"name": m_start.group(1), "lineno": ev["lineno"], "idx": i})
            continue

        m_end = _RE_GUI_CALLBACK_END.match(msg)
        if m_end:
            name = m_end.group(1)
            # 同名の start を後ろから探して取り除く（最も近い start を消費）
            for j in range(len(starts) - 1, -1, -1):
                if starts[j]["name"] == name:
                    starts.pop(j)
                    break

    # 残った starts はすべて anomaly
    for s in starts:
        anomalies.append({
            "rule": "Rule 6: GUI_CALLBACK ペアリング欠落",
            "lineno": s["lineno"],
            "name": s["name"],
            "description": (
                f"GUI_CALLBACK start={s['name']} に対応する end/error が"
                f" window 行以内に出ていない"
            ),
        })

    return anomalies


# ---------------------------------------------------------------------------
# Rule 5: 翻訳(RT) ← 原文(RT) 欠落
# ---------------------------------------------------------------------------

def check_translation_without_source(events: list, window: int = 10) -> list:
    """Rule 5: [翻訳(RT)] の前後 window 行以内に [原文(RT)] がなければ異常。

    running 状態になる前（STARTUP フェーズ）の翻訳はスキップする。

    Args:
        events: parse_log_lines の結果
        window: 前後の許容行数（デフォルト 10）

    Returns:
        anomaly リスト
    """
    anomalies = []

    # running 状態になった行番号を取得（それ以前はスキップ）
    running_lineno = None
    for ev in events:
        if ev["prefix"] == "STATE" and "starting -> running" in ev["message"]:
            running_lineno = ev["lineno"]
            break

    # [原文(RT)] の行番号セットを収集
    source_linenos = {e["lineno"] for e in events if e["prefix"] == "原文(RT)"}

    # [翻訳(RT)] イベントを走査
    translation_events = [e for e in events if e["prefix"] == "翻訳(RT)"]

    for ev in translation_events:
        # STARTUP フェーズ前はスキップ
        if running_lineno is not None and ev["lineno"] < running_lineno:
            continue
        if running_lineno is None:
            continue

        # 前後 window 行以内に [原文(RT)] があるか確認
        found = any(
            abs(src_lineno - ev["lineno"]) <= window
            for src_lineno in source_linenos
        )

        if not found:
            anomalies.append({
                "rule": "原文受信欠落 (Rule 5)",
                "lineno": ev["lineno"],
                "trigger": f"[翻訳(RT)] {ev['message']}",
            })

    return anomalies


# ---------------------------------------------------------------------------
# レポート生成
# ---------------------------------------------------------------------------

def format_report(anomalies: list, output_json: bool = False) -> str:
    """anomaly リストをレポート文字列に変換する。

    Args:
        anomalies: check_* 関数の結果リスト
        output_json: True なら JSON 形式、False なら人間向けテキスト

    Returns:
        レポート文字列
    """
    if output_json:
        data = {
            "count": len(anomalies),
            "anomalies": anomalies,
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    lines = []
    lines.append("=== ログ異常検出結果 ===")
    lines.append(f"検出された異常: {len(anomalies)} 件")
    lines.append("")

    if not anomalies:
        lines.append("異常は検出されませんでした。")
        return "\n".join(lines)

    for i, a in enumerate(anomalies, start=1):
        lines.append(f"[ANOMALY {i}] {a['rule']}")
        lines.append(f"  行番号: {a['lineno']}")
        detail = a.get("trigger") or a.get("description", "")
        lines.append(f"  {detail}")
        lines.append("")

    return "\n".join(lines)


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
        description="ログファイルから異常パターンを検出するツール",
    )
    parser.add_argument(
        "log_files",
        nargs="*",
        help="解析対象のログファイル（複数可）",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        default=False,
        help="標準入力からログを読み込む",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=2.0,
        help="ペアリングのウィンドウサイズ（秒換算の目安、デフォルト 2.0）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="JSON 形式で出力する",
    )
    parser.add_argument(
        "--rule",
        default="all",
        help="検証するルール（カンマ区切り、例: 1,5 / デフォルト: all）",
    )
    parser.add_argument(
        "--exit-fail-on-anomaly",
        action="store_true",
        default=False,
        help="異常検出時に exit code 1 で終了する",
    )
    return parser.parse_args(argv)


def determine_exit_code(anomalies: list, exit_fail_on_anomaly: bool) -> int:
    """exit code を決定する。

    Args:
        anomalies: 検出された anomaly リスト
        exit_fail_on_anomaly: True のとき anomaly があれば 1 を返す

    Returns:
        0 または 1
    """
    if exit_fail_on_anomaly and anomalies:
        return 1
    return 0


def _run_checks(text: str, args: argparse.Namespace) -> list:
    """ログテキストに対して全チェックを実行し、anomaly リストを返す。

    Args:
        text: ログ全テキスト
        args: parse_args の結果

    Returns:
        全 anomaly リスト
    """
    # threshold を行数ウィンドウに変換（1秒 ≒ 5行 を基準とする）
    # 例: threshold=2.0 → window=10
    window = max(1, int(args.threshold * 5))

    events = parse_log_lines(text)

    rules = args.rule.lower()
    all_rules = rules == "all"
    anomalies = []

    if all_rules or "1" in rules:
        anomalies.extend(check_user_action_pairs(events, window=window))

    if all_rules or "2" in rules or "3" in rules:
        anomalies.extend(check_state_transitions(events, window=window * 2))

    if all_rules or "4" in rules:
        anomalies.extend(check_rpc_action_pairs(events, window=window))

    if all_rules or "5" in rules:
        anomalies.extend(check_translation_without_source(events, window=window * 3))

    if all_rules or "6" in rules:
        anomalies.extend(check_gui_callback_pairs(events, window=window))

    # 行番号順にソート
    anomalies.sort(key=lambda a: a["lineno"])

    return anomalies


def main(argv: list = None) -> int:
    """エントリーポイント。

    Args:
        argv: sys.argv[1:] に相当するリスト（テスト用）

    Returns:
        exit code (0 or 1)
    """
    args = parse_args(argv)
    all_anomalies = []

    if args.stdin:
        text = sys.stdin.read()
        anomalies = _run_checks(text, args)
        all_anomalies.extend(anomalies)
        print(f"[stdin]")
    else:
        for pattern in args.log_files:
            paths = sorted(Path(".").glob(pattern)) if "*" in pattern else [Path(pattern)]
            for path in paths:
                if not path.exists():
                    print(f"[警告] ファイルが見つかりません: {path}", file=sys.stderr)
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                anomalies = _run_checks(text, args)
                all_anomalies.extend(anomalies)
                line_count = text.count("\n")
                print(f"ファイル: {path} ({line_count} 行)")

    report = format_report(all_anomalies, output_json=args.json)
    print(report)

    return determine_exit_code(all_anomalies, args.exit_fail_on_anomaly)


if __name__ == "__main__":
    sys.exit(main())
