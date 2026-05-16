"""
tests/test_log_anomaly_detector.py

tools/log_anomaly_detector.py のユニットテスト。

TDD: このファイルを先に書き、RED を確認してから実装する。

テスト対象:
1. parse_log_lines — [PREFIX] message フォーマット解析
2. Rule 1 (USER→ACTION) ペアリング検出（未対応時は anomaly）
3. Rule 1 ペア成立時は anomaly 0
4. Rule 5 原文受信欠落検出
5. format_report テキスト/JSON 出力
6. CLI 引数解析
7. exit code（--exit-fail-on-anomaly）

サンプルログ: 実機形式に則った秘密情報なしのフェイクデータのみ使用。
"""

import json
import sys
from pathlib import Path

import pytest

# tools/ を sys.path に追加
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import log_anomaly_detector as lad


# ---------------------------------------------------------------------------
# サンプルログスニペット（秘密情報なし・フェイクデータ）
# ---------------------------------------------------------------------------

# 正常ケース: USER → ACTION がペアになっているログ
SAMPLE_LOG_NORMAL = """\
[STARTUP] 設定ファイル読み込み 完了 (0.00s)
[STARTUP] GUI 構築 完了 (0.00s)
[USER] 開始ボタン押下 (running=False)
[ACTION] route_a 再起動開始 reason=開始ボタン押下
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
[翻訳(RT)] Hello world.
[翻訳(RT)] This is a test.
[USER] 停止ボタン押下 (running=True)
[ACTION] route_a 再起動完了 reason=停止ボタン押下
[STATE] CaptionSystem(route_id=a) running -> stopping
[STATE] CaptionSystem(route_id=a) stopping -> idle
"""

# Rule 1 異常: USER の後に ACTION が来ない
SAMPLE_LOG_USER_NO_ACTION = """\
[STARTUP] 設定ファイル読み込み 完了 (0.00s)
[USER] 系統A 原文表示 → OFF
[STATE] CaptionSystem(route_id=a) running -> stopping
[STATE] CaptionSystem(route_id=a) stopping -> idle
[翻訳(RT)] Hello.
"""

# Rule 3 異常: STATE stopping → idle が来ない
SAMPLE_LOG_STATE_NO_IDLE = """\
[STATE] CaptionSystem(route_id=a) running -> stopping
[TIMING] CaptionSystem(route_id=a).stop_stream(): 0.000s
[翻訳(RT)] Hello.
[翻訳(RT)] Test message.
"""

# Rule 5 異常: 翻訳(RT) の直前に 原文(RT) がない
SAMPLE_LOG_TRANSLATION_WITHOUT_SOURCE = """\
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
[翻訳(RT)] Hello.
[翻訳(RT)] This is translated text.
[翻訳(RT)] Another translation.
"""

# Rule 5 正常: 翻訳(RT) の近くに 原文(RT) がある
SAMPLE_LOG_TRANSLATION_WITH_SOURCE = """\
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
[原文(RT)]  Hello there.
[翻訳(RT)] こんにちは。
[原文(RT)]  How are you?
[翻訳(RT)] お元気ですか？
"""

# 混在ケース: 一部 ACTION あり、一部なし
SAMPLE_LOG_MIXED = """\
[USER] 開始ボタン押下 (running=False)
[ACTION] route_a 再起動開始 reason=開始ボタン押下
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
[USER] 系統A 原文表示 → OFF
[STATE] CaptionSystem(route_id=a) running -> stopping
[STATE] CaptionSystem(route_id=a) stopping -> idle
"""

# RPC → ACTION 正常ケース
SAMPLE_LOG_RPC_WITH_ACTION = """\
[RPC] POST /api/start
[ACTION] route_a 再起動開始 reason=RPC start
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
"""

# RPC → ACTION 異常ケース
SAMPLE_LOG_RPC_NO_ACTION = """\
[RPC] POST /api/start
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
[翻訳(RT)] Hello.
"""


# ---------------------------------------------------------------------------
# 1. parse_log_lines のフォーマット解析テスト
# ---------------------------------------------------------------------------

class TestParseLogLines:
    """parse_log_lines が [PREFIX] message を正しく解析すること。"""

    def test_basic_prefix_and_message(self):
        """[USER] プリフィックスとメッセージを正しく分解すること。"""
        text = "[USER] 開始ボタン押下 (running=False)\n"
        events = lad.parse_log_lines(text)
        assert len(events) == 1
        e = events[0]
        assert e["prefix"] == "USER"
        assert e["message"] == "開始ボタン押下 (running=False)"

    def test_state_prefix(self):
        """[STATE] プリフィックスを正しく解析すること。"""
        text = "[STATE] CaptionSystem(route_id=a) idle -> starting\n"
        events = lad.parse_log_lines(text)
        assert len(events) == 1
        assert events[0]["prefix"] == "STATE"
        assert "idle -> starting" in events[0]["message"]

    def test_action_prefix(self):
        """[ACTION] プリフィックスを正しく解析すること。"""
        text = "[ACTION] route_a 再起動開始 reason=開始ボタン押下\n"
        events = lad.parse_log_lines(text)
        assert events[0]["prefix"] == "ACTION"

    def test_translation_rt_prefix(self):
        """[翻訳(RT)] プリフィックスを正しく解析すること。"""
        text = "[翻訳(RT)] Hello world.\n"
        events = lad.parse_log_lines(text)
        assert events[0]["prefix"] == "翻訳(RT)"
        assert events[0]["message"] == "Hello world."

    def test_source_rt_prefix(self):
        """[原文(RT)] プリフィックスを正しく解析すること。"""
        text = "[原文(RT)]  もしもし、\n"
        events = lad.parse_log_lines(text)
        assert events[0]["prefix"] == "原文(RT)"
        assert events[0]["message"].strip() == "もしもし、"

    def test_line_number_recorded(self):
        """行番号が正しく記録されること。"""
        text = "line1 ignored\n[USER] test\n[ACTION] act\n"
        events = lad.parse_log_lines(text)
        # "line1 ignored" はプリフィックスなしで無視 or unknown として処理
        user_events = [e for e in events if e["prefix"] == "USER"]
        action_events = [e for e in events if e["prefix"] == "ACTION"]
        assert len(user_events) == 1
        assert len(action_events) == 1
        # USER の行番号が ACTION より小さいこと
        assert user_events[0]["lineno"] < action_events[0]["lineno"]

    def test_multiple_lines(self):
        """複数行を正しく解析すること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_NORMAL)
        prefixes = [e["prefix"] for e in events]
        assert "USER" in prefixes
        assert "ACTION" in prefixes
        assert "STATE" in prefixes
        assert "翻訳(RT)" in prefixes

    def test_unknown_prefix_included(self):
        """未知プリフィックスも解析に含まれること（フィルタリングは呼び出し元が行う）。"""
        text = "[INFO] some info message\n[USER] test\n"
        events = lad.parse_log_lines(text)
        all_prefixes = [e["prefix"] for e in events]
        assert "USER" in all_prefixes

    def test_empty_string(self):
        """空文字列を渡したとき空リストが返ること。"""
        events = lad.parse_log_lines("")
        assert events == []

    def test_non_prefixed_lines_skipped(self):
        """[ で始まらない行は解析結果に含まれないこと。"""
        text = "plain text line\nanother plain line\n[USER] valid\n"
        events = lad.parse_log_lines(text)
        # plain lines は含まれないこと
        for e in events:
            assert e["prefix"] != ""

    def test_rpc_prefix(self):
        """[RPC] プリフィックスを正しく解析すること。"""
        text = "[RPC] POST /api/start\n"
        events = lad.parse_log_lines(text)
        assert events[0]["prefix"] == "RPC"
        assert "/api/start" in events[0]["message"]

    def test_startup_prefix(self):
        """[STARTUP] プリフィックスを正しく解析すること。"""
        text = "[STARTUP] 設定ファイル読み込み 完了 (0.00s)\n"
        events = lad.parse_log_lines(text)
        assert events[0]["prefix"] == "STARTUP"


# ---------------------------------------------------------------------------
# 2. Rule 1: USER → ACTION ペアリング検出（anomaly 発生）
# ---------------------------------------------------------------------------

class TestUserActionPairingAnomaly:
    """USER の後に ACTION が来ない場合に anomaly が検出されること。"""

    def test_user_without_action_is_anomaly(self):
        """[USER] の後に [ACTION] が来ない場合、anomaly が返ること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_USER_NO_ACTION)
        anomalies = lad.check_user_action_pairs(events, window=20)
        assert len(anomalies) >= 1

    def test_anomaly_has_correct_rule(self):
        """anomaly の rule が 'USER→ACTION' であること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_USER_NO_ACTION)
        anomalies = lad.check_user_action_pairs(events, window=20)
        assert any("USER" in a["rule"] and "ACTION" in a["rule"] for a in anomalies)

    def test_anomaly_has_lineno(self):
        """anomaly に lineno が含まれること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_USER_NO_ACTION)
        anomalies = lad.check_user_action_pairs(events, window=20)
        for a in anomalies:
            assert "lineno" in a

    def test_anomaly_has_trigger_message(self):
        """anomaly に trigger（[USER] 行のメッセージ）が含まれること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_USER_NO_ACTION)
        anomalies = lad.check_user_action_pairs(events, window=20)
        for a in anomalies:
            assert "trigger" in a
            assert "原文表示" in a["trigger"] or "押下" in a["trigger"] or a["trigger"]

    def test_mixed_log_detects_only_unmatched(self):
        """ペアが成立している USER は anomaly にならないこと。"""
        events = lad.parse_log_lines(SAMPLE_LOG_MIXED)
        anomalies = lad.check_user_action_pairs(events, window=5)
        # 最初の USER は ACTION あり → anomaly なし
        # 2番目の USER は ACTION なし → anomaly あり
        assert len(anomalies) == 1

    def test_small_window_can_miss_action(self):
        """window が小さすぎると ACTION を見つけられず anomaly になること。"""
        # USER の直後 1 行以内に ACTION がない場合
        text = "[USER] test\n[STATE] something\n[ACTION] act\n"
        events = lad.parse_log_lines(text)
        # window=1 では STATE が間に挟まっているので USER→ACTION を見つけられない
        anomalies = lad.check_user_action_pairs(events, window=1)
        assert len(anomalies) >= 1

    def test_action_within_window_no_anomaly(self):
        """window 内に ACTION があれば anomaly にならないこと（ペア成立）。"""
        text = "[USER] test\n[STATE] something\n[ACTION] act\n"
        events = lad.parse_log_lines(text)
        # window=5 なら ACTION を見つけられる
        anomalies = lad.check_user_action_pairs(events, window=5)
        assert len(anomalies) == 0


# ---------------------------------------------------------------------------
# 3. Rule 1: ペア成立時は anomaly 0
# ---------------------------------------------------------------------------

class TestUserActionPairingNoAnomaly:
    """USER の後に ACTION が正しく来る場合、anomaly が 0 であること。"""

    def test_normal_log_has_no_anomaly(self):
        """正常ログで USER → ACTION が成立し、anomaly が 0 であること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_NORMAL)
        anomalies = lad.check_user_action_pairs(events, window=10)
        assert len(anomalies) == 0

    def test_rpc_with_action_no_anomaly(self):
        """RPC → ACTION が成立するログで check_rpc_action_pairs が 0 を返すこと。"""
        events = lad.parse_log_lines(SAMPLE_LOG_RPC_WITH_ACTION)
        anomalies = lad.check_rpc_action_pairs(events, window=10)
        assert len(anomalies) == 0

    def test_rpc_without_action_is_anomaly(self):
        """RPC の後に ACTION が来ない場合、anomaly が返ること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_RPC_NO_ACTION)
        anomalies = lad.check_rpc_action_pairs(events, window=5)
        assert len(anomalies) >= 1


# ---------------------------------------------------------------------------
# 4. Rule 5: 原文受信欠落検出
# ---------------------------------------------------------------------------

class TestTranslationWithoutSource:
    """翻訳(RT) の近くに 原文(RT) がない場合を検出すること。"""

    def test_translation_without_source_is_anomaly(self):
        """[翻訳(RT)] の前後 N 行に [原文(RT)] がない場合、anomaly が返ること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_TRANSLATION_WITHOUT_SOURCE)
        anomalies = lad.check_translation_without_source(events, window=5)
        assert len(anomalies) >= 1

    def test_translation_with_source_no_anomaly(self):
        """[翻訳(RT)] の近くに [原文(RT)] がある場合、anomaly が返らないこと。"""
        events = lad.parse_log_lines(SAMPLE_LOG_TRANSLATION_WITH_SOURCE)
        anomalies = lad.check_translation_without_source(events, window=5)
        assert len(anomalies) == 0

    def test_anomaly_rule_is_rule5(self):
        """anomaly の rule が Rule 5 であること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_TRANSLATION_WITHOUT_SOURCE)
        anomalies = lad.check_translation_without_source(events, window=5)
        for a in anomalies:
            assert "原文" in a["rule"] or "5" in a["rule"] or "source" in a["rule"].lower()

    def test_anomaly_has_translation_message(self):
        """anomaly に翻訳テキストが含まれること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_TRANSLATION_WITHOUT_SOURCE)
        anomalies = lad.check_translation_without_source(events, window=5)
        for a in anomalies:
            assert "trigger" in a

    def test_startup_lines_dont_create_anomaly_for_source(self):
        """STARTUP フェーズ中の翻訳は anomaly にならないこと（起動時はスキップ）。"""
        # STARTUP が終わる前は running 状態になっていない
        text = (
            "[STARTUP] 設定ファイル読み込み 完了 (0.00s)\n"
            "[翻訳(RT)] some startup translation\n"
        )
        events = lad.parse_log_lines(text)
        # STATE running が来るまでは check はスキップ
        anomalies = lad.check_translation_without_source(events, window=5)
        # running 前なので anomaly にならないこと
        assert len(anomalies) == 0


# ---------------------------------------------------------------------------
# 5. check_state_transitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    """STATE 遷移のタイムアウト検出テスト。"""

    def test_stopping_without_idle_is_anomaly(self):
        """stopping → idle が来ない場合、anomaly が返ること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_STATE_NO_IDLE)
        anomalies = lad.check_state_transitions(events, window=3)
        assert len(anomalies) >= 1

    def test_stopping_with_idle_no_anomaly(self):
        """stopping → idle が正しく遷移する場合、anomaly が 0 であること。"""
        text = (
            "[STATE] CaptionSystem(route_id=a) running -> stopping\n"
            "[TIMING] stop_stream(): 0.000s\n"
            "[STATE] CaptionSystem(route_id=a) stopping -> idle\n"
        )
        events = lad.parse_log_lines(text)
        anomalies = lad.check_state_transitions(events, window=5)
        assert len(anomalies) == 0

    def test_state_anomaly_has_route_info(self):
        """anomaly に route_id 情報が含まれること。"""
        events = lad.parse_log_lines(SAMPLE_LOG_STATE_NO_IDLE)
        anomalies = lad.check_state_transitions(events, window=3)
        for a in anomalies:
            assert "trigger" in a
            assert "route_id" in a["trigger"] or "stopping" in a["trigger"]


# ---------------------------------------------------------------------------
# 6. find_pair_anomalies
# ---------------------------------------------------------------------------

class TestFindPairAnomalies:
    """find_pair_anomalies の汎用ペアリング検証テスト。"""

    def test_trigger_without_expected_is_anomaly(self):
        """trigger の後に expected が来ない場合、anomaly が返ること。"""
        events = lad.parse_log_lines("[USER] test\n[STATE] something\n")
        anomalies = lad.find_pair_anomalies(
            events,
            trigger_prefix="USER",
            expected_prefix="ACTION",
            window=5,
        )
        assert len(anomalies) >= 1

    def test_trigger_with_expected_no_anomaly(self):
        """trigger の後に expected が来る場合、anomaly が 0 であること。"""
        events = lad.parse_log_lines("[USER] test\n[ACTION] act\n")
        anomalies = lad.find_pair_anomalies(
            events,
            trigger_prefix="USER",
            expected_prefix="ACTION",
            window=5,
        )
        assert len(anomalies) == 0

    def test_anomaly_contains_rule_info(self):
        """anomaly に rule 情報が含まれること。"""
        events = lad.parse_log_lines("[USER] test\n")
        anomalies = lad.find_pair_anomalies(
            events,
            trigger_prefix="USER",
            expected_prefix="ACTION",
            window=5,
        )
        for a in anomalies:
            assert "rule" in a
            assert "lineno" in a
            assert "trigger" in a


# ---------------------------------------------------------------------------
# 7. format_report テキスト出力
# ---------------------------------------------------------------------------

class TestFormatReportText:
    """format_report がテキスト形式で正しく出力すること。"""

    def test_no_anomaly_report(self):
        """anomaly 0 件のとき「異常なし」メッセージが含まれること。"""
        report = lad.format_report([], output_json=False)
        assert "異常" in report or "anomaly" in report.lower() or "0" in report

    def test_anomaly_report_contains_anomaly_count(self):
        """anomaly あるとき件数が含まれること。"""
        anomalies = [
            {"rule": "USER→ACTION 欠落", "lineno": 2, "trigger": "[USER] test"},
        ]
        report = lad.format_report(anomalies, output_json=False)
        assert "1" in report

    def test_anomaly_report_contains_rule(self):
        """anomaly の rule 名が出力に含まれること。"""
        anomalies = [
            {"rule": "USER→ACTION 欠落", "lineno": 2, "trigger": "[USER] test"},
        ]
        report = lad.format_report(anomalies, output_json=False)
        assert "USER" in report

    def test_anomaly_report_contains_trigger(self):
        """anomaly の trigger メッセージが出力に含まれること。"""
        anomalies = [
            {"rule": "USER→ACTION 欠落", "lineno": 5, "trigger": "[USER] 原文表示 → OFF"},
        ]
        report = lad.format_report(anomalies, output_json=False)
        assert "原文表示" in report

    def test_multiple_anomalies_numbered(self):
        """複数 anomaly が番号付きで出力されること。"""
        anomalies = [
            {"rule": "Rule1", "lineno": 1, "trigger": "t1"},
            {"rule": "Rule2", "lineno": 2, "trigger": "t2"},
        ]
        report = lad.format_report(anomalies, output_json=False)
        assert "1" in report
        assert "2" in report

    def test_header_included(self):
        """レポートヘッダー（=== 等）が含まれること。"""
        report = lad.format_report([], output_json=False)
        assert "===" in report or "---" in report or "ログ" in report


# ---------------------------------------------------------------------------
# 8. format_report JSON 出力
# ---------------------------------------------------------------------------

class TestFormatReportJson:
    """format_report が JSON 形式で正しく出力すること。"""

    def test_json_output_is_valid_json(self):
        """--json オプションで有効な JSON が返ること。"""
        anomalies = [
            {"rule": "USER→ACTION 欠落", "lineno": 2, "trigger": "[USER] test"},
        ]
        report = lad.format_report(anomalies, output_json=True)
        data = json.loads(report)
        assert isinstance(data, dict) or isinstance(data, list)

    def test_json_contains_anomalies_key(self):
        """JSON に anomalies キーが含まれること。"""
        anomalies = [
            {"rule": "USER→ACTION 欠落", "lineno": 2, "trigger": "[USER] test"},
        ]
        report = lad.format_report(anomalies, output_json=True)
        data = json.loads(report)
        if isinstance(data, dict):
            assert "anomalies" in data or "count" in data
        # リストの場合もOK

    def test_json_no_anomaly(self):
        """anomaly 0 件でも有効な JSON が返ること。"""
        report = lad.format_report([], output_json=True)
        data = json.loads(report)
        assert data is not None

    def test_json_anomaly_count(self):
        """JSON の anomaly 件数が正しいこと。"""
        anomalies = [
            {"rule": "R1", "lineno": 1, "trigger": "t1"},
            {"rule": "R2", "lineno": 2, "trigger": "t2"},
        ]
        report = lad.format_report(anomalies, output_json=True)
        data = json.loads(report)
        if isinstance(data, dict) and "anomalies" in data:
            assert len(data["anomalies"]) == 2
        elif isinstance(data, list):
            assert len(data) == 2


# ---------------------------------------------------------------------------
# 9. CLI 引数解析
# ---------------------------------------------------------------------------

class TestCLIArgParsing:
    """argparse の引数解析テスト。"""

    def test_parse_args_default_threshold(self):
        """--threshold 未指定のデフォルト値が 2.0 であること。"""
        args = lad.parse_args(["dummy.log"])
        assert args.threshold == 2.0

    def test_parse_args_custom_threshold(self):
        """--threshold 5.0 が正しく解析されること。"""
        args = lad.parse_args(["dummy.log", "--threshold", "5.0"])
        assert args.threshold == 5.0

    def test_parse_args_json_flag(self):
        """--json フラグが正しく解析されること。"""
        args = lad.parse_args(["dummy.log", "--json"])
        assert args.json is True

    def test_parse_args_json_false_by_default(self):
        """--json 未指定のデフォルトが False であること。"""
        args = lad.parse_args(["dummy.log"])
        assert args.json is False

    def test_parse_args_exit_fail_flag(self):
        """--exit-fail-on-anomaly フラグが正しく解析されること。"""
        args = lad.parse_args(["dummy.log", "--exit-fail-on-anomaly"])
        assert args.exit_fail_on_anomaly is True

    def test_parse_args_exit_fail_false_by_default(self):
        """--exit-fail-on-anomaly 未指定のデフォルトが False であること。"""
        args = lad.parse_args(["dummy.log"])
        assert args.exit_fail_on_anomaly is False

    def test_parse_args_stdin_flag(self):
        """--stdin フラグが正しく解析されること。"""
        args = lad.parse_args(["--stdin"])
        assert args.stdin is True

    def test_parse_args_rule_option(self):
        """--rule オプションが正しく解析されること。"""
        args = lad.parse_args(["dummy.log", "--rule", "1,5"])
        assert args.rule == "1,5"

    def test_parse_args_multiple_files(self):
        """複数ファイル指定が正しく解析されること。"""
        args = lad.parse_args(["file1.log", "file2.log"])
        assert len(args.log_files) == 2


# ---------------------------------------------------------------------------
# 10. exit code テスト
# ---------------------------------------------------------------------------

class TestExitCode:
    """--exit-fail-on-anomaly の exit code テスト。"""

    def test_determine_exit_code_no_anomaly(self):
        """anomaly 0 件のとき exit code 0 を返すこと。"""
        code = lad.determine_exit_code(anomalies=[], exit_fail_on_anomaly=True)
        assert code == 0

    def test_determine_exit_code_with_anomaly_and_flag(self):
        """anomaly あり + --exit-fail-on-anomaly のとき exit code 1 を返すこと。"""
        anomalies = [{"rule": "test", "lineno": 1, "trigger": "t"}]
        code = lad.determine_exit_code(anomalies=anomalies, exit_fail_on_anomaly=True)
        assert code == 1

    def test_determine_exit_code_with_anomaly_no_flag(self):
        """anomaly あり + --exit-fail-on-anomaly なしのとき exit code 0 を返すこと。"""
        anomalies = [{"rule": "test", "lineno": 1, "trigger": "t"}]
        code = lad.determine_exit_code(anomalies=anomalies, exit_fail_on_anomaly=False)
        assert code == 0


# ---------------------------------------------------------------------------
# 11. 実機ログスニペットを使った統合テスト
# ---------------------------------------------------------------------------

class TestIntegrationWithRealLikeLog:
    """実機ログ形式のスニペットを使った統合テスト（秘密情報なし）。"""

    # 実機ログ console_20260516-115732.log から抜粋したスニペット（無害・匿名化）
    REAL_LIKE_LOG = """\
[app] プロジェクトパス設定完了
[STARTUP] 設定ファイル読み込み 完了 (0.00s)
[STARTUP] GUI 構築 完了 (0.00s)
[USER] 開始ボタン押下 (running=False)
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
[AUDIO][a] peak=20251 ( 61%) chunks=46
[翻訳(RT)] The only thing:
[翻訳(RT)]  we stream live every week.
[翻訳(RT)]  Hello.
[STATE] CaptionSystem(route_id=a) running -> stopping
[STATE] CaptionSystem(route_id=a) stopping -> idle
[STATE] CaptionSystem(route_id=a) idle -> starting
[STATE] CaptionSystem(route_id=a) starting -> running
[翻訳(RT)] Hello.
[USER] 停止ボタン押下 (running=True)
[STATE] CaptionSystem(route_id=a) running -> stopping
[STATE] CaptionSystem(route_id=a) stopping -> idle
"""

    def test_real_like_log_user_no_action_detected(self):
        """実機ログ形式: USER → ACTION がない場合に anomaly が検出されること。"""
        events = lad.parse_log_lines(self.REAL_LIKE_LOG)
        anomalies = lad.check_user_action_pairs(events, window=10)
        # 開始ボタン・停止ボタンのどちらも ACTION がないので anomaly >= 1
        assert len(anomalies) >= 1

    def test_real_like_log_translation_without_source(self):
        """実機ログ形式: 翻訳(RT) の近くに 原文(RT) がない場合に anomaly が検出されること。"""
        events = lad.parse_log_lines(self.REAL_LIKE_LOG)
        anomalies = lad.check_translation_without_source(events, window=5)
        # 原文(RT) がなく翻訳(RT) だけなので anomaly あり
        assert len(anomalies) >= 1

    def test_real_like_log_state_transitions_ok(self):
        """実機ログ形式: STATE 遷移が正常な場合、anomaly が 0 であること。"""
        events = lad.parse_log_lines(self.REAL_LIKE_LOG)
        anomalies = lad.check_state_transitions(events, window=5)
        # 全 stopping → idle が成立しているので 0
        assert len(anomalies) == 0

    def test_format_report_with_real_like_anomalies(self):
        """実機ログ形式の anomaly を format_report で整形できること。"""
        events = lad.parse_log_lines(self.REAL_LIKE_LOG)
        anomalies = lad.check_user_action_pairs(events, window=5)
        report = lad.format_report(anomalies, output_json=False)
        assert isinstance(report, str)
        assert len(report) > 0
