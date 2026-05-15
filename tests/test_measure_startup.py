"""
tests/test_measure_startup.py

tools/measure_startup.py の単体テスト。
parse_startup_log / parse_total_time / format_report の純粋関数をテストする。
run_measurement は subprocess を使うため unittest.mock で代替する。
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# tools/ を sys.path に追加して measure_startup をインポートできるようにする
_ROOT = Path(__file__).parent.parent
_TOOLS = _ROOT / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from measure_startup import (
    format_report,
    parse_startup_log,
    parse_total_time,
)


# ---------------------------------------------------------------------------
# テストデータ
# ---------------------------------------------------------------------------

SAMPLE_LOG_NORMAL = """\
[STARTUP] 設定ファイル読み込み 開始 ...
[STARTUP] 設定ファイル読み込み 完了 (0.05s)
[STARTUP] 入力デバイス列挙 開始 ...
[STARTUP] 入力デバイス列挙 完了 (0.34s)
[STARTUP] settings.json 状態復元 開始 ...
[STARTUP] settings.json 状態復元 完了 (0.02s)
[STARTUP] MultiCaptionSystem 常駐生成 開始 ...
[STARTUP] MultiCaptionSystem 常駐生成 完了 (2.18s)
[STARTUP] GUI 構築 開始 ...
[STARTUP] GUI 構築 完了 (0.65s)
[STARTUP] 全体起動時間: 5.55s
"""

SAMPLE_LOG_WITH_ERROR = """\
[STARTUP] 設定ファイル読み込み 開始 ...
[STARTUP][ERROR] 設定ファイル読み込み 失敗 (0.02s): FileNotFoundError
[STARTUP] 入力デバイス列挙 開始 ...
[STARTUP] 入力デバイス列挙 完了 (0.34s)
[STARTUP] 全体起動時間: 3.10s
"""

SAMPLE_LOG_TRUNCATED = """\
[STARTUP] 設定ファイル読み込み 開始 ...
[STARTUP] 設定ファイル読み込み 完了 (0.05s)
[STARTUP] 入力デバイス列挙 開始 ...
"""

SAMPLE_LOG_EMPTY = ""

SAMPLE_LOG_NO_TOTAL = """\
[STARTUP] 設定ファイル読み込み 完了 (0.05s)
[STARTUP] 入力デバイス列挙 完了 (0.34s)
"""


# ---------------------------------------------------------------------------
# parse_startup_log テスト
# ---------------------------------------------------------------------------

class TestParseStartupLog:
    """parse_startup_log(stdout: str) -> dict[str, float] のテスト"""

    def test_normal_log_extracts_all_steps(self):
        """正常ログから全ステップを抽出できる"""
        result = parse_startup_log(SAMPLE_LOG_NORMAL)
        assert "設定ファイル読み込み" in result
        assert "入力デバイス列挙" in result
        assert "settings.json 状態復元" in result
        assert "MultiCaptionSystem 常駐生成" in result
        assert "GUI 構築" in result

    def test_normal_log_step_values(self):
        """正常ログの各ステップの時間が正しく抽出される"""
        result = parse_startup_log(SAMPLE_LOG_NORMAL)
        assert result["設定ファイル読み込み"] == pytest.approx(0.05)
        assert result["入力デバイス列挙"] == pytest.approx(0.34)
        assert result["settings.json 状態復元"] == pytest.approx(0.02)
        assert result["MultiCaptionSystem 常駐生成"] == pytest.approx(2.18)
        assert result["GUI 構築"] == pytest.approx(0.65)

    def test_normal_log_step_count(self):
        """正常ログからステップ数が正しく抽出される（全体起動時間は除く）"""
        result = parse_startup_log(SAMPLE_LOG_NORMAL)
        # 全体起動時間行は含まない
        assert len(result) == 5

    def test_error_line_does_not_crash(self):
        """[STARTUP][ERROR] 行があってもクラッシュしない"""
        result = parse_startup_log(SAMPLE_LOG_WITH_ERROR)
        # エラー行はスキップ or 含む、どちらでもよいがクラッシュしないことを確認
        assert isinstance(result, dict)

    def test_error_line_extracts_successful_steps(self):
        """エラーがあっても成功したステップは抽出される"""
        result = parse_startup_log(SAMPLE_LOG_WITH_ERROR)
        assert "入力デバイス列挙" in result
        assert result["入力デバイス列挙"] == pytest.approx(0.34)

    def test_truncated_log_returns_partial_result(self):
        """途中で切れたログに対して partial な結果を返す（クラッシュしない）"""
        result = parse_startup_log(SAMPLE_LOG_TRUNCATED)
        # 完了行がある 設定ファイル読み込み だけ抽出される
        assert "設定ファイル読み込み" in result
        assert isinstance(result, dict)

    def test_empty_log_returns_empty_dict(self):
        """空文字ログに対して空の dict を返す"""
        result = parse_startup_log(SAMPLE_LOG_EMPTY)
        assert result == {}

    def test_total_time_line_not_included_in_steps(self):
        """'全体起動時間' 行はステップとして含まれない"""
        result = parse_startup_log(SAMPLE_LOG_NORMAL)
        # 「全体起動時間」というキーが steps には含まれない
        assert "全体起動時間" not in result

    def test_returns_dict_type(self):
        """戻り値が dict 型である"""
        result = parse_startup_log(SAMPLE_LOG_NORMAL)
        assert isinstance(result, dict)

    def test_values_are_float(self):
        """dict の値がすべて float 型である"""
        result = parse_startup_log(SAMPLE_LOG_NORMAL)
        for v in result.values():
            assert isinstance(v, float)


# ---------------------------------------------------------------------------
# parse_total_time テスト
# ---------------------------------------------------------------------------

class TestParseTotalTime:
    """parse_total_time(stdout: str) -> float | None のテスト"""

    def test_normal_log_returns_total(self):
        """正常ログから全体起動時間を抽出できる"""
        result = parse_total_time(SAMPLE_LOG_NORMAL)
        assert result == pytest.approx(5.55)

    def test_log_with_error_returns_total(self):
        """エラーがあっても全体起動時間を抽出できる"""
        result = parse_total_time(SAMPLE_LOG_WITH_ERROR)
        assert result == pytest.approx(3.10)

    def test_no_total_line_returns_none(self):
        """全体起動時間の行がなければ None を返す"""
        result = parse_total_time(SAMPLE_LOG_NO_TOTAL)
        assert result is None

    def test_empty_log_returns_none(self):
        """空ログに対して None を返す"""
        result = parse_total_time(SAMPLE_LOG_EMPTY)
        assert result is None

    def test_truncated_log_returns_none(self):
        """全体起動時間行がない途中ログでは None を返す"""
        result = parse_total_time(SAMPLE_LOG_TRUNCATED)
        assert result is None

    def test_returns_float_type(self):
        """戻り値が float 型である"""
        result = parse_total_time(SAMPLE_LOG_NORMAL)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# format_report テキスト形式テスト
# ---------------------------------------------------------------------------

class TestFormatReportText:
    """format_report のテキスト出力テスト"""

    def _make_steps(self):
        return {
            "設定ファイル読み込み": 0.05,
            "入力デバイス列挙": 0.34,
            "GUI 構築": 0.65,
        }

    def test_output_contains_header(self):
        """出力に '=== 起動時間計測結果 ===' が含まれる"""
        report = format_report(self._make_steps(), 1.04, 0.3, 10.0)
        assert "起動時間計測結果" in report

    def test_output_contains_step_names(self):
        """出力に各ステップ名が含まれる"""
        report = format_report(self._make_steps(), 1.04, 0.3, 10.0)
        assert "設定ファイル読み込み" in report
        assert "入力デバイス列挙" in report
        assert "GUI 構築" in report

    def test_output_contains_step_times(self):
        """出力に各ステップの時間が含まれる"""
        report = format_report(self._make_steps(), 1.04, 0.3, 10.0)
        assert "0.05s" in report
        assert "0.34s" in report
        assert "0.65s" in report

    def test_output_contains_total_time(self):
        """出力に全体起動時間が含まれる"""
        report = format_report(self._make_steps(), 1.04, 0.3, 10.0)
        assert "1.04s" in report

    def test_threshold_exceeded_step_shows_warning(self):
        """threshold_step を超えたステップに警告マーカーが付く"""
        # threshold_step=0.3 → 0.34s の「入力デバイス列挙」が超過
        report = format_report(self._make_steps(), 1.04, 0.3, 10.0)
        assert "⚠" in report

    def test_threshold_not_exceeded_no_warning(self):
        """threshold を超えるステップがなければ警告マーカーが出ない"""
        report = format_report(self._make_steps(), 1.04, 1.0, 10.0)
        assert "⚠" not in report

    def test_total_threshold_exceeded_shows_warning(self):
        """全体時間が threshold_total を超えたら警告が出る"""
        # threshold_total=0.5 → 1.04s が超過
        report = format_report(self._make_steps(), 1.04, 0.3, 0.5)
        assert "⚠" in report

    def test_returns_string(self):
        """戻り値が str 型である"""
        report = format_report(self._make_steps(), 1.04, 0.3, 10.0)
        assert isinstance(report, str)

    def test_separator_line_present(self):
        """区切り線（---）が出力に含まれる"""
        report = format_report(self._make_steps(), 1.04, 0.3, 10.0)
        assert "---" in report


# ---------------------------------------------------------------------------
# format_report JSON 形式テスト
# ---------------------------------------------------------------------------

class TestFormatReportJson:
    """format_report の JSON 出力テスト（output_json=True）"""

    def _make_steps(self):
        return {
            "設定ファイル読み込み": 0.05,
            "入力デバイス列挙": 0.34,
        }

    def test_json_output_is_valid_json(self):
        """JSON モードで valid な JSON 文字列を返す"""
        report = format_report(self._make_steps(), 0.39, 0.3, 10.0, output_json=True)
        data = json.loads(report)
        assert isinstance(data, dict)

    def test_json_output_contains_steps(self):
        """JSON に steps キーが含まれる"""
        report = format_report(self._make_steps(), 0.39, 0.3, 10.0, output_json=True)
        data = json.loads(report)
        assert "steps" in data

    def test_json_output_steps_values(self):
        """JSON の steps 値が正しい"""
        report = format_report(self._make_steps(), 0.39, 0.3, 10.0, output_json=True)
        data = json.loads(report)
        assert data["steps"]["設定ファイル読み込み"] == pytest.approx(0.05)
        assert data["steps"]["入力デバイス列挙"] == pytest.approx(0.34)

    def test_json_output_contains_total(self):
        """JSON に total_time キーが含まれる"""
        report = format_report(self._make_steps(), 0.39, 0.3, 10.0, output_json=True)
        data = json.loads(report)
        assert "total_time" in data
        assert data["total_time"] == pytest.approx(0.39)

    def test_json_output_threshold_exceeded(self):
        """閾値超過情報が JSON に含まれる"""
        report = format_report(self._make_steps(), 0.39, 0.3, 10.0, output_json=True)
        data = json.loads(report)
        assert "threshold_exceeded" in data

    def test_json_threshold_exceeded_true_when_step_over(self):
        """ステップが threshold_step を超えたとき threshold_exceeded が True"""
        # 0.34s > 0.3 → 超過
        report = format_report(self._make_steps(), 0.39, 0.3, 10.0, output_json=True)
        data = json.loads(report)
        assert data["threshold_exceeded"] is True

    def test_json_threshold_exceeded_false_when_all_within(self):
        """すべてのステップが閾値以内のとき threshold_exceeded が False"""
        report = format_report(self._make_steps(), 0.39, 1.0, 10.0, output_json=True)
        data = json.loads(report)
        assert data["threshold_exceeded"] is False


# ---------------------------------------------------------------------------
# 閾値超過の判定テスト
# ---------------------------------------------------------------------------

class TestThresholdCheck:
    """閾値超過の判定ロジックテスト"""

    def test_step_exceeds_threshold(self):
        """ステップ時間が threshold_step を超えたとき警告マーカーが付く"""
        steps = {"遅いステップ": 5.0, "普通のステップ": 0.5}
        report = format_report(steps, 5.5, 3.0, 10.0)
        # 5.0s は 3.0 超過 → ⚠
        assert "⚠" in report

    def test_step_equals_threshold_no_warning(self):
        """ステップ時間が threshold_step ちょうどのとき警告なし（境界値）"""
        steps = {"境界ステップ": 3.0}
        report = format_report(steps, 3.0, 3.0, 10.0)
        # 3.0 == 3.0 は超過ではない（strictly greater than）
        assert "⚠" not in report

    def test_total_exceeds_threshold(self):
        """全体時間が threshold_total を超えたとき警告が出る"""
        steps = {"ステップ": 0.5}
        report = format_report(steps, 15.0, 3.0, 10.0)
        assert "⚠" in report

    def test_total_equals_threshold_no_warning(self):
        """全体時間が threshold_total ちょうどのとき警告なし（境界値）"""
        steps = {"ステップ": 0.5}
        report = format_report(steps, 10.0, 3.0, 10.0)
        assert "⚠" not in report

    def test_no_threshold_exceeded(self):
        """すべて閾値以内なら警告なし"""
        steps = {"ステップ1": 0.1, "ステップ2": 0.2}
        report = format_report(steps, 0.3, 3.0, 10.0)
        assert "⚠" not in report
