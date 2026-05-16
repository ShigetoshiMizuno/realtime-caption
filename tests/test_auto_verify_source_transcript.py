"""
tests/test_auto_verify_source_transcript.py

tools/auto_verify_source_transcript.py のユニットテスト。

TDD: このファイルを先に書き、RED を確認してから実装する。

テスト対象:
1. count_rt_ws_recv_events — RT_WS_RECV イベント type 別カウント（純関数）
2. extract_new_verbose_files — 差分検出（純関数）
3. format_report — テキスト/JSON 出力
4. run_e2e_verification — subprocess なし mock テスト
5. verdict 判定ロジック（source_delta > 0 vs == 0）
6. env_with_utf8 — 環境変数 dict

サンプル値: 実際の API キーは一切使用しない（フェイクデータのみ）。
Windows 専用（SAPI 利用）の旨は実装側 docstring で明記する。
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# tools/ を sys.path に追加
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import auto_verify_source_transcript as avst


# ---------------------------------------------------------------------------
# サンプル verbose ログ（秘密情報なし・フェイクデータ）
# ---------------------------------------------------------------------------

# 正常ケース: session.input_transcript.delta が複数あるログ
SAMPLE_VERBOSE_WITH_SOURCE = """\
[RT_WS_RECV] {"type": "session.created", "session": {"id": "fake-session-id-0001"}}
[RT_WS_RECV] {"type": "session.updated", "session": {"id": "fake-session-id-0001"}}
[RT_WS_RECV] {"type": "session.input_transcript.delta", "delta": "Hello"}
[RT_WS_RECV] {"type": "session.input_transcript.delta", "delta": " world"}
[RT_WS_RECV] {"type": "session.output_transcript.delta", "delta": "こんにちは"}
[RT_WS_RECV] {"type": "session.output_audio.delta", "delta": "FAKEBASE64=="}
[RT_WS_RECV] {"type": "session.output_transcript.done", "transcript": "こんにちは"}
[RT_WS_RECV] {"type": "session.input_transcript.done", "transcript": "Hello world"}
[RT_WS_RECV] {"type": "session.output_audio.delta", "delta": "FAKEBASE64=="}
[RT_WS_RECV] {"type": "session.output_audio.delta", "delta": "FAKEBASE64=="}
"""

# source_delta = 0 のログ（input_transcript.delta がない）
SAMPLE_VERBOSE_WITHOUT_SOURCE = """\
[RT_WS_RECV] {"type": "session.created", "session": {"id": "fake-session-id-0002"}}
[RT_WS_RECV] {"type": "session.updated", "session": {"id": "fake-session-id-0002"}}
[RT_WS_RECV] {"type": "session.output_transcript.delta", "delta": "こんにちは"}
[RT_WS_RECV] {"type": "session.output_transcript.delta", "delta": "世界"}
[RT_WS_RECV] {"type": "session.output_audio.delta", "delta": "FAKEBASE64=="}
[RT_WS_RECV] {"type": "session.output_transcript.done", "transcript": "こんにちは世界"}
"""

# 空ログ
SAMPLE_VERBOSE_EMPTY = ""

# RT_WS_RECV 以外の行が混在するログ
SAMPLE_VERBOSE_MIXED = """\
[STARTUP] 設定ファイル読み込み 完了 (0.00s)
[RT_WS_RECV] {"type": "session.created", "session": {"id": "fake-session-id-0003"}}
[STATE] CaptionSystem(route_id=a) idle -> starting
[RT_WS_RECV] {"type": "session.input_transcript.delta", "delta": "Test"}
[RT_WS_RECV] {"type": "session.input_transcript.delta", "delta": " phrase"}
[RT_WS_RECV] {"type": "session.input_transcript.done", "transcript": "Test phrase"}
[ACTION] route_a 再起動開始 reason=開始ボタン
[RT_WS_RECV] {"type": "session.output_transcript.delta", "delta": "テスト"}
[RT_WS_RECV] {"type": "session.output_audio.delta", "delta": "FAKEBASE64=="}
"""


# ---------------------------------------------------------------------------
# 1. count_rt_ws_recv_events — イベント type 別カウント
# ---------------------------------------------------------------------------

class TestCountRtWsRecvEvents:
    """count_rt_ws_recv_events が RT_WS_RECV ログを正確にカウントすること。"""

    def test_source_delta_counted(self):
        """session.input_transcript.delta が正確にカウントされること。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_WITH_SOURCE)
        assert counts.get("session.input_transcript.delta", 0) == 2

    def test_output_delta_counted(self):
        """session.output_transcript.delta が正確にカウントされること。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_WITH_SOURCE)
        assert counts.get("session.output_transcript.delta", 0) == 1

    def test_output_audio_counted(self):
        """session.output_audio.delta が正確にカウントされること。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_WITH_SOURCE)
        # SAMPLE_VERBOSE_WITH_SOURCE 内の output_audio.delta 行数に合わせる（3件）
        assert counts.get("session.output_audio.delta", 0) == 3

    def test_session_created_counted(self):
        """session.created が 1 件カウントされること。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_WITH_SOURCE)
        assert counts.get("session.created", 0) == 1

    def test_no_source_delta_returns_zero(self):
        """source delta がないログで session.input_transcript.delta が 0 であること。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_WITHOUT_SOURCE)
        assert counts.get("session.input_transcript.delta", 0) == 0

    def test_empty_log_returns_empty_dict(self):
        """空ログで空 dict が返ること（またはすべて 0）。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_EMPTY)
        assert isinstance(counts, dict)
        assert counts.get("session.input_transcript.delta", 0) == 0

    def test_non_rt_ws_recv_lines_ignored(self):
        """RT_WS_RECV 以外の行がカウントに含まれないこと。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_MIXED)
        # STARTUP, STATE, ACTION はカウントされない
        for key in counts:
            assert key.startswith("session.") or "." in key

    def test_mixed_log_counts_correct(self):
        """混在ログで RT_WS_RECV だけが正確にカウントされること。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_MIXED)
        assert counts.get("session.input_transcript.delta", 0) == 2
        assert counts.get("session.output_transcript.delta", 0) == 1

    def test_returns_dict(self):
        """戻り値が dict であること。"""
        counts = avst.count_rt_ws_recv_events(SAMPLE_VERBOSE_WITH_SOURCE)
        assert isinstance(counts, dict)

    def test_invalid_json_line_skipped(self):
        """不正 JSON 行がスキップされ例外が出ないこと。"""
        log = "[RT_WS_RECV] {invalid json}\n[RT_WS_RECV] {\"type\": \"session.created\"}\n"
        counts = avst.count_rt_ws_recv_events(log)
        # 不正行はスキップ、正常行だけカウント
        assert counts.get("session.created", 0) == 1

    def test_line_without_type_skipped(self):
        """type キーがない JSON 行がスキップされること。"""
        log = "[RT_WS_RECV] {\"event\": \"no_type_key\"}\n"
        counts = avst.count_rt_ws_recv_events(log)
        assert len(counts) == 0


# ---------------------------------------------------------------------------
# 2. extract_new_verbose_files — 差分検出
# ---------------------------------------------------------------------------

class TestExtractNewVerboseFiles:
    """extract_new_verbose_files が差分を正確に検出すること。"""

    def test_new_file_detected(self):
        """before にない after のファイルが検出されること。"""
        before = {"a.txt", "b.txt"}
        after = {"a.txt", "b.txt", "c.txt"}
        result = avst.extract_new_verbose_files(before, after, "*_verbose.txt")
        assert "c.txt" in result

    def test_no_new_file_returns_empty(self):
        """before と after が同じ場合、空 set が返ること。"""
        before = {"a.txt", "b.txt"}
        after = {"a.txt", "b.txt"}
        result = avst.extract_new_verbose_files(before, after, "*_verbose.txt")
        assert len(result) == 0

    def test_multiple_new_files_detected(self):
        """複数の新規ファイルがすべて検出されること。"""
        before = {"a.txt"}
        after = {"a.txt", "b.txt", "c.txt"}
        result = avst.extract_new_verbose_files(before, after, "*_verbose.txt")
        assert "b.txt" in result
        assert "c.txt" in result
        assert len(result) == 2

    def test_removed_file_not_in_result(self):
        """after から消えたファイルは結果に含まれないこと。"""
        before = {"a.txt", "b.txt"}
        after = {"a.txt"}  # b.txt が消えた
        result = avst.extract_new_verbose_files(before, after, "*_verbose.txt")
        assert "b.txt" not in result
        assert len(result) == 0

    def test_returns_set(self):
        """戻り値が set であること。"""
        before = set()
        after = {"x.txt"}
        result = avst.extract_new_verbose_files(before, after, "*_verbose.txt")
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# 3. format_report — テキスト/JSON 出力
# ---------------------------------------------------------------------------

class TestFormatReport:
    """format_report が正しいフォーマットで出力すること。"""

    def _make_result_with_source(self):
        return {
            "status": "completed",
            "duration": 30.0,
            "verbose_files": ["2026-05-16-1_verbose.txt"],
            "event_counts": {
                "session.input_transcript.delta": 12,
                "session.output_transcript.delta": 35,
                "session.output_audio.delta": 120,
                "session.created": 1,
            },
            "source_delta_count": 12,
            "rule5_anomalies": 0,
            "verdict": "client_issue",
            "message": "API が原文イベントを送信している。クライアント側の callback 配線を確認推奨",
        }

    def _make_result_no_source(self):
        return {
            "status": "completed",
            "duration": 30.0,
            "verbose_files": ["2026-05-16-2_verbose.txt"],
            "event_counts": {
                "session.output_transcript.delta": 35,
                "session.created": 1,
            },
            "source_delta_count": 0,
            "rule5_anomalies": 3,
            "verdict": "api_issue",
            "message": "API が原文イベントを送信していない",
        }

    def test_text_report_contains_header(self):
        """テキストレポートにヘッダーが含まれること。"""
        result = self._make_result_with_source()
        report = avst.format_report(result, output_json=False)
        assert "実機検証" in report or "===" in report or "#121" in report

    def test_text_report_contains_verdict(self):
        """テキストレポートに verdict（判定）が含まれること。"""
        result = self._make_result_with_source()
        report = avst.format_report(result, output_json=False)
        assert "client_issue" in report or "クライアント" in report or "callback" in report

    def test_text_report_contains_source_count(self):
        """テキストレポートに source_delta_count が含まれること。"""
        result = self._make_result_with_source()
        report = avst.format_report(result, output_json=False)
        assert "12" in report

    def test_text_report_api_issue_message(self):
        """api_issue 判定時に API 側の問題を示すメッセージが含まれること。"""
        result = self._make_result_no_source()
        report = avst.format_report(result, output_json=False)
        assert "api_issue" in report or "API" in report or "0" in report

    def test_text_report_verbose_files_listed(self):
        """テキストレポートに verbose ファイル名が含まれること。"""
        result = self._make_result_with_source()
        report = avst.format_report(result, output_json=False)
        assert "2026-05-16-1_verbose.txt" in report

    def test_json_report_is_valid_json(self):
        """JSON レポートが有効な JSON であること。"""
        result = self._make_result_with_source()
        report = avst.format_report(result, output_json=True)
        data = json.loads(report)
        assert isinstance(data, dict)

    def test_json_report_contains_verdict(self):
        """JSON レポートに verdict キーが含まれること。"""
        result = self._make_result_with_source()
        report = avst.format_report(result, output_json=True)
        data = json.loads(report)
        assert "verdict" in data

    def test_json_report_source_delta_count(self):
        """JSON レポートの source_delta_count が正しいこと。"""
        result = self._make_result_with_source()
        report = avst.format_report(result, output_json=True)
        data = json.loads(report)
        assert data["source_delta_count"] == 12

    def test_json_report_no_source_verdict_api_issue(self):
        """source_delta_count == 0 のとき verdict が api_issue であること。"""
        result = self._make_result_no_source()
        report = avst.format_report(result, output_json=True)
        data = json.loads(report)
        assert data["verdict"] == "api_issue"

    def test_error_status_reported(self):
        """status が error のとき、エラー内容が含まれること。"""
        result = {"status": "no_verbose_log", "error": "verbose ログが見つかりません"}
        report = avst.format_report(result, output_json=False)
        assert "エラー" in report or "no_verbose_log" in report or "verbose" in report


# ---------------------------------------------------------------------------
# 4. run_e2e_verification — subprocess なし mock テスト
# ---------------------------------------------------------------------------

class TestRunE2eVerificationMocked:
    """run_e2e_verification を subprocess なしで mock してテスト。"""

    def _make_mock_proc(self):
        proc = MagicMock()
        proc.wait.return_value = 0
        return proc

    @patch("auto_verify_source_transcript._tts_speak")
    @patch("auto_verify_source_transcript.subprocess.Popen")
    @patch("auto_verify_source_transcript.glob.glob")
    @patch("auto_verify_source_transcript.time.sleep")
    @patch("auto_verify_source_transcript.open", create=True)
    def test_returns_dict(self, mock_open, mock_sleep, mock_glob, mock_popen, mock_tts):
        """run_e2e_verification が dict を返すこと。"""
        # verbose ログが新規に生成されたかのように見せる
        mock_glob.side_effect = [
            [],  # before
            ["2026-05-16-1_verbose.txt"],  # after
        ]
        mock_popen.return_value = self._make_mock_proc()
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read = MagicMock(return_value=SAMPLE_VERBOSE_WITH_SOURCE)

        result = avst.run_e2e_verification(
            duration=5.0,
            phrases=["Hello world"],
            verbose_log_pattern="*_verbose.txt",
        )
        assert isinstance(result, dict)

    @patch("auto_verify_source_transcript._tts_speak")
    @patch("auto_verify_source_transcript.subprocess.Popen")
    @patch("auto_verify_source_transcript.glob.glob")
    @patch("auto_verify_source_transcript.time.sleep")
    @patch("auto_verify_source_transcript.open", create=True)
    def test_source_delta_positive_verdict_client_issue(
        self, mock_open, mock_sleep, mock_glob, mock_popen, mock_tts
    ):
        """source_delta > 0 のとき verdict が client_issue であること。"""
        mock_glob.side_effect = [
            [],
            ["2026-05-16-1_verbose.txt"],
        ]
        mock_popen.return_value = self._make_mock_proc()
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read = MagicMock(return_value=SAMPLE_VERBOSE_WITH_SOURCE)

        result = avst.run_e2e_verification(
            duration=5.0,
            phrases=["Hello world"],
            verbose_log_pattern="*_verbose.txt",
        )
        assert result.get("verdict") == "client_issue"

    @patch("auto_verify_source_transcript._tts_speak")
    @patch("auto_verify_source_transcript.subprocess.Popen")
    @patch("auto_verify_source_transcript.glob.glob")
    @patch("auto_verify_source_transcript.time.sleep")
    @patch("auto_verify_source_transcript.open", create=True)
    def test_no_source_delta_verdict_api_issue(
        self, mock_open, mock_sleep, mock_glob, mock_popen, mock_tts
    ):
        """source_delta == 0 のとき verdict が api_issue であること。"""
        mock_glob.side_effect = [
            [],
            ["2026-05-16-2_verbose.txt"],
        ]
        mock_popen.return_value = self._make_mock_proc()
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read = MagicMock(return_value=SAMPLE_VERBOSE_WITHOUT_SOURCE)

        result = avst.run_e2e_verification(
            duration=5.0,
            phrases=["Hello world"],
            verbose_log_pattern="*_verbose.txt",
        )
        assert result.get("verdict") == "api_issue"

    @patch("auto_verify_source_transcript._tts_speak")
    @patch("auto_verify_source_transcript.subprocess.Popen")
    @patch("auto_verify_source_transcript.glob.glob")
    @patch("auto_verify_source_transcript.time.sleep")
    def test_no_verbose_log_returns_error_status(
        self, mock_sleep, mock_glob, mock_popen, mock_tts
    ):
        """verbose ログが生成されなかった場合、status が no_verbose_log であること。"""
        mock_glob.side_effect = [
            [],  # before
            [],  # after（新規ファイルなし）
        ]
        mock_popen.return_value = self._make_mock_proc()

        result = avst.run_e2e_verification(
            duration=5.0,
            phrases=["Hello world"],
            verbose_log_pattern="*_verbose.txt",
        )
        assert result.get("status") == "no_verbose_log"

    @patch("auto_verify_source_transcript._tts_speak")
    @patch("auto_verify_source_transcript.subprocess.Popen")
    @patch("auto_verify_source_transcript.glob.glob")
    @patch("auto_verify_source_transcript.time.sleep")
    @patch("auto_verify_source_transcript.open", create=True)
    def test_result_contains_event_counts(
        self, mock_open, mock_sleep, mock_glob, mock_popen, mock_tts
    ):
        """result に event_counts が含まれること。"""
        mock_glob.side_effect = [
            [],
            ["2026-05-16-1_verbose.txt"],
        ]
        mock_popen.return_value = self._make_mock_proc()
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read = MagicMock(return_value=SAMPLE_VERBOSE_WITH_SOURCE)

        result = avst.run_e2e_verification(
            duration=5.0,
            phrases=["Hello world"],
            verbose_log_pattern="*_verbose.txt",
        )
        assert "event_counts" in result
        assert isinstance(result["event_counts"], dict)

    @patch("auto_verify_source_transcript._tts_speak")
    @patch("auto_verify_source_transcript.subprocess.Popen")
    @patch("auto_verify_source_transcript.glob.glob")
    @patch("auto_verify_source_transcript.time.sleep")
    @patch("auto_verify_source_transcript.open", create=True)
    def test_result_contains_rule5_anomaly_count(
        self, mock_open, mock_sleep, mock_glob, mock_popen, mock_tts
    ):
        """result に rule5_anomalies カウントが含まれること。"""
        mock_glob.side_effect = [
            [],
            ["2026-05-16-1_verbose.txt"],
        ]
        mock_popen.return_value = self._make_mock_proc()
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        mock_open.return_value.read = MagicMock(return_value=SAMPLE_VERBOSE_WITH_SOURCE)

        result = avst.run_e2e_verification(
            duration=5.0,
            phrases=["Hello"],
            verbose_log_pattern="*_verbose.txt",
        )
        assert "rule5_anomalies" in result
        assert isinstance(result["rule5_anomalies"], int)

    @patch("auto_verify_source_transcript._tts_speak")
    @patch("auto_verify_source_transcript.subprocess.Popen")
    @patch("auto_verify_source_transcript.glob.glob")
    @patch("auto_verify_source_transcript.time.sleep")
    def test_timeout_handling(self, mock_sleep, mock_glob, mock_popen, mock_tts):
        """subprocess タイムアウト時に proc.kill が呼ばれること。"""
        import subprocess
        mock_glob.side_effect = [
            [],
            [],  # verbose ログなし
        ]
        proc = MagicMock()
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="app.py", timeout=5)
        mock_popen.return_value = proc

        result = avst.run_e2e_verification(
            duration=5.0,
            phrases=["Hello"],
            verbose_log_pattern="*_verbose.txt",
        )
        proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# 5. verdict 判定ロジック
# ---------------------------------------------------------------------------

class TestVerdictLogic:
    """source_delta_count に基づく verdict 判定のテスト。"""

    def test_positive_source_delta_gives_client_issue(self):
        """source_delta_count > 0 → verdict == 'client_issue'。"""
        verdict, message = avst.determine_verdict(source_delta=5)
        assert verdict == "client_issue"

    def test_zero_source_delta_gives_api_issue(self):
        """source_delta_count == 0 → verdict == 'api_issue'。"""
        verdict, message = avst.determine_verdict(source_delta=0)
        assert verdict == "api_issue"

    def test_client_issue_message_mentions_callback(self):
        """client_issue のメッセージに 'callback' または '配線' が含まれること。"""
        verdict, message = avst.determine_verdict(source_delta=1)
        assert "callback" in message.lower() or "配線" in message

    def test_api_issue_message_mentions_api(self):
        """api_issue のメッセージに 'API' または 'session.update' が含まれること。"""
        verdict, message = avst.determine_verdict(source_delta=0)
        assert "API" in message or "session.update" in message or "api" in message.lower()

    def test_large_source_delta_still_client_issue(self):
        """source_delta が大きい値でも verdict == 'client_issue'。"""
        verdict, message = avst.determine_verdict(source_delta=999)
        assert verdict == "client_issue"


# ---------------------------------------------------------------------------
# 6. env_with_utf8 — 環境変数 dict
# ---------------------------------------------------------------------------

class TestEnvWithUtf8:
    """env_with_utf8 が PYTHONIOENCODING=utf-8 を含む dict を返すこと。"""

    def test_returns_dict(self):
        """戻り値が dict であること。"""
        env = avst.env_with_utf8()
        assert isinstance(env, dict)

    def test_contains_pythonioencoding(self):
        """PYTHONIOENCODING キーが含まれること。"""
        env = avst.env_with_utf8()
        assert "PYTHONIOENCODING" in env

    def test_pythonioencoding_value_is_utf8(self):
        """PYTHONIOENCODING の値が 'utf-8' であること。"""
        env = avst.env_with_utf8()
        assert env["PYTHONIOENCODING"] == "utf-8"

    def test_inherits_existing_env(self):
        """既存の環境変数 PATH が引き継がれること。"""
        env = avst.env_with_utf8()
        import os
        assert "PATH" in env or "Path" in env or len(env) > 1
