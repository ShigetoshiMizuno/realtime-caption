"""
tests/test_e2e_smoke_unit.py — run_e2e_smoke.py 内ロジックのユニットテスト。

実アプリは起動しない。subprocess / RPC 呼び出しはすべて mock で差し替える。
発注書: commissions/e2e-realtime-test-script/L1-prg-designer/order.md §テスト
発注書: commissions/e2e-full-automation/L1-prg-designer/order.md §テスト（新規追加分）
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# run_e2e_smoke をインポートできるようにパスを追加
_PROJECT_ROOT = Path(__file__).parent.parent
_E2E_DIR = _PROJECT_ROOT / "tests" / "e2e"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_E2E_DIR))

from run_e2e_smoke import (
    E2eSmokeRunner,
    ScenarioResult,
    _speak_tts_sync,
    _check_console_log_pattern,
    _check_translate_file_has_translation,
    _assert_lamp_color,
    _assert_route_b_state,
)


# ---------------------------------------------------------------------------
# ScenarioResult
# ---------------------------------------------------------------------------

class TestScenarioResult:
    def test_pass_format(self):
        r = ScenarioResult(name="S1 route_a_solo_eng2jp", passed=True, detail="TTS検出+翻訳記録+ランプA赤")
        line = str(r)
        assert "PASS" in line
        assert "S1 route_a_solo_eng2jp" in line

    def test_fail_format(self):
        r = ScenarioResult(name="S4-S5 ptt_press_release", passed=False, detail="route_b_state != RUNNING")
        line = str(r)
        assert "FAIL" in line
        assert "S4-S5 ptt_press_release" in line

    def test_passed_flag_true(self):
        r = ScenarioResult(name="x", passed=True, detail="")
        assert r.passed is True

    def test_passed_flag_false(self):
        r = ScenarioResult(name="x", passed=False, detail="")
        assert r.passed is False


# ---------------------------------------------------------------------------
# _speak_tts_sync: PowerShell TTS 呼び出し
# ---------------------------------------------------------------------------

class TestSpeakTtsSync:
    def test_calls_powershell(self):
        """PowerShell が呼ばれることを確認（実際には実行しない）。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _speak_tts_sync("Hello world", lang="en-US")
            assert mock_run.called
            args = mock_run.call_args[0][0]
            assert any("powershell" in str(a).lower() for a in args)

    def test_passes_text_to_powershell(self):
        """音声テキストが PowerShell コマンドに含まれることを確認。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _speak_tts_sync("Test sentence for TTS", lang="en-US")
            call_args = " ".join(str(a) for a in mock_run.call_args[0][0])
            assert "Test sentence for TTS" in call_args

    def test_passes_lang_to_powershell(self):
        """言語コードが PowerShell コマンドに含まれることを確認。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _speak_tts_sync("Hello", lang="ja-JP")
            call_args = " ".join(str(a) for a in mock_run.call_args[0][0])
            assert "ja-JP" in call_args

    def test_timeout_does_not_raise(self):
        """subprocess.TimeoutExpired は無視して正常終了する。"""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ps", timeout=5)):
            _speak_tts_sync("Hello", lang="en-US")


# ---------------------------------------------------------------------------
# _check_console_log_pattern: コンソールログのパターン検索
# ---------------------------------------------------------------------------

class TestCheckConsoleLogPattern:
    def test_pattern_found_returns_true(self, tmp_path):
        log_file = tmp_path / "console.log"
        log_file.write_text(
            "12:00:00 [原文(RT)] Hello, world\n12:00:01 [翻訳(RT)] こんにちは\n",
            encoding="utf-8",
        )
        assert _check_console_log_pattern(str(log_file), r"\[原文\(RT\)\]") is True

    def test_pattern_not_found_returns_false(self, tmp_path):
        log_file = tmp_path / "console.log"
        log_file.write_text("12:00:00 INFO app started\n", encoding="utf-8")
        assert _check_console_log_pattern(str(log_file), r"\[原文\(RT\)\]") is False

    def test_translation_pattern_found(self, tmp_path):
        log_file = tmp_path / "console.log"
        log_file.write_text("[翻訳(RT)] This is translation\n", encoding="utf-8")
        assert _check_console_log_pattern(str(log_file), r"\[翻訳\(RT\)\]") is True

    def test_nonexistent_file_returns_false(self, tmp_path):
        assert _check_console_log_pattern(str(tmp_path / "not_exist.log"), r"\[原文\(RT\)\]") is False

    def test_empty_file_returns_false(self, tmp_path):
        log_file = tmp_path / "empty.log"
        log_file.write_text("", encoding="utf-8")
        assert _check_console_log_pattern(str(log_file), r"\[原文\(RT\)\]") is False


# ---------------------------------------------------------------------------
# _check_translate_file_has_translation: translate.txt の翻訳行チェック
# ---------------------------------------------------------------------------

class TestCheckTranslateFileHasTranslation:
    def test_translation_line_found(self, tmp_path):
        tfile = tmp_path / "2026-05-28-1_translate.txt"
        tfile.write_text("翻訳(RT): This is a translation\n", encoding="utf-8")
        assert _check_translate_file_has_translation(str(tmp_path), date_prefix="2026-05-28") is True

    def test_no_translation_line(self, tmp_path):
        tfile = tmp_path / "2026-05-28-1_translate.txt"
        tfile.write_text("原文(RT): Hello\n", encoding="utf-8")
        assert _check_translate_file_has_translation(str(tmp_path), date_prefix="2026-05-28") is False

    def test_no_file_for_today(self, tmp_path):
        assert _check_translate_file_has_translation(str(tmp_path), date_prefix="2026-05-28") is False

    def test_multiple_files_any_translation(self, tmp_path):
        """複数ファイルのうちどれかに翻訳行があれば True。"""
        f1 = tmp_path / "2026-05-28-1_translate.txt"
        f1.write_text("原文(RT): Hello\n", encoding="utf-8")
        f2 = tmp_path / "2026-05-28-2_translate.txt"
        f2.write_text("翻訳(RT): こんにちは\n", encoding="utf-8")
        assert _check_translate_file_has_translation(str(tmp_path), date_prefix="2026-05-28") is True

    def test_route_b_translate_file(self, tmp_path):
        """route-b サフィックスのファイルも対象。"""
        tfile = tmp_path / "2026-05-28-1-route-b_translate.txt"
        tfile.write_text("翻訳(RT): Good morning\n", encoding="utf-8")
        assert _check_translate_file_has_translation(str(tmp_path), date_prefix="2026-05-28") is True


# ---------------------------------------------------------------------------
# _assert_lamp_color: ランプ色アサーション（発注書指定 RGBA 値を含む）
# ---------------------------------------------------------------------------

class TestAssertLampColor:
    def _make_client(self, color_value):
        client = MagicMock()
        client.get_config.return_value = {"value": color_value}
        return client

    def test_red_lamp_passes_with_spec_rgba(self):
        """発注書指定 (220, 0, 0, 255) で赤判定が通ること。"""
        client = self._make_client([220, 0, 0, 255])
        assert _assert_lamp_color(client, "billing_lamp_a", expected="red") is True

    def test_red_lamp_passes_with_high_r(self):
        """R=255 の純赤でも通ること。"""
        client = self._make_client([255, 0, 0, 255])
        assert _assert_lamp_color(client, "billing_lamp_a", expected="red") is True

    def test_yellow_lamp_passes(self):
        client = self._make_client([255, 255, 0, 255])
        assert _assert_lamp_color(client, "billing_lamp_b", expected="yellow") is True

    def test_wrong_color_fails(self):
        client = self._make_client([0, 255, 0, 255])  # 緑
        assert _assert_lamp_color(client, "billing_lamp_a", expected="red") is False

    def test_none_color_fails(self):
        client = self._make_client(None)
        assert _assert_lamp_color(client, "billing_lamp_a", expected="red") is False

    def test_string_color_red_passes(self):
        """色が文字列で返ってきた場合も "red" を含めば True。"""
        client = self._make_client("red")
        assert _assert_lamp_color(client, "billing_lamp_a", expected="red") is True

    def test_border_r_200_passes(self):
        """R=200 ちょうどは赤と判定される（境界値）。"""
        client = self._make_client([200, 0, 0, 255])
        assert _assert_lamp_color(client, "billing_lamp_a", expected="red") is True

    def test_border_r_199_fails(self):
        """R=199 は赤と判定されない（境界値）。"""
        client = self._make_client([199, 0, 0, 255])
        assert _assert_lamp_color(client, "billing_lamp_a", expected="red") is False


# ---------------------------------------------------------------------------
# _assert_route_b_state: route_b_state アサーション（mock RPC）
# ---------------------------------------------------------------------------

class TestAssertRouteBState:
    def _make_client(self, route_b_state, route_b_audio_gate=None):
        client = MagicMock()
        client.get_status.return_value = {
            "konnyaku_running": True,
            "route_b_state": route_b_state,
            "route_b_audio_gate": route_b_audio_gate,
        }
        return client

    def test_running_state_passes(self):
        client = self._make_client("RUNNING")
        assert _assert_route_b_state(client, expected_state="RUNNING") is True

    def test_idle_state_passes(self):
        client = self._make_client("IDLE")
        assert _assert_route_b_state(client, expected_state="IDLE") is True

    def test_wrong_state_fails(self):
        client = self._make_client("IDLE")
        assert _assert_route_b_state(client, expected_state="RUNNING") is False

    def test_gate_true_passes(self):
        client = self._make_client("RUNNING", route_b_audio_gate=True)
        assert _assert_route_b_state(client, expected_state="RUNNING", expected_gate=True) is True

    def test_gate_false_passes(self):
        client = self._make_client("RUNNING", route_b_audio_gate=False)
        assert _assert_route_b_state(client, expected_state="RUNNING", expected_gate=False) is True

    def test_gate_mismatch_fails(self):
        client = self._make_client("RUNNING", route_b_audio_gate=False)
        assert _assert_route_b_state(client, expected_state="RUNNING", expected_gate=True) is False

    def test_none_state_fails(self):
        client = self._make_client(None)
        assert _assert_route_b_state(client, expected_state="RUNNING") is False


# ---------------------------------------------------------------------------
# E2eSmokeRunner: アプリ起動・停止・クリーンアップ
# ---------------------------------------------------------------------------

class TestE2eSmokeRunnerStartStop:
    def test_start_app_calls_subprocess_popen(self, tmp_path):
        """_start_app が subprocess.Popen を呼ぶことを確認。"""
        runner = E2eSmokeRunner(port=18788, log_dir=str(tmp_path))
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc
            runner._start_app()
            assert mock_popen.called
            args = mock_popen.call_args[0][0]
            assert any("app.py" in str(a) for a in args)

    def test_stop_app_terminates_process(self, tmp_path):
        """_stop_app が proc.terminate() を呼ぶことを確認。"""
        runner = E2eSmokeRunner(port=18788, log_dir=str(tmp_path))
        mock_proc = MagicMock()
        runner._proc = mock_proc
        runner._log_file = MagicMock()
        with patch("subprocess.run"):
            runner._stop_app()
        mock_proc.terminate.assert_called_once()

    def test_stop_app_calls_taskkill_when_proc_existed(self, tmp_path):
        """_stop_app がアプリを起動した場合（proc あり）に taskkill を呼ぶことを確認（課金リスク管理）。"""
        runner = E2eSmokeRunner(port=18788, log_dir=str(tmp_path))
        mock_proc = MagicMock()
        runner._proc = mock_proc
        runner._log_file = MagicMock()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            runner._stop_app()
            # taskkill が呼ばれたことを確認
            taskkill_calls = [
                c for c in mock_run.call_args_list
                if any("taskkill" in str(a).lower() for a in (c[0][0] if c[0] else []))
            ]
            assert len(taskkill_calls) >= 1, "taskkill が呼ばれていない"

    def test_stop_app_does_not_call_taskkill_when_no_proc(self, tmp_path):
        """_stop_app がプロセスを持たない場合（dry-run 等）は taskkill を呼ばないこと。"""
        runner = E2eSmokeRunner(port=18788, log_dir=str(tmp_path))
        runner._proc = None
        runner._log_file = None
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            runner._stop_app()
            # taskkill が呼ばれていないことを確認
            taskkill_calls = [
                c for c in mock_run.call_args_list
                if any("taskkill" in str(a).lower() for a in (c[0][0] if c[0] else []))
            ]
            assert len(taskkill_calls) == 0, "proc=None なのに taskkill が呼ばれた"

    def test_stop_app_safe_when_no_proc(self, tmp_path):
        """プロセスが存在しない場合も stop_app はエラーを出さない。"""
        runner = E2eSmokeRunner(port=18788, log_dir=str(tmp_path))
        runner._proc = None
        with patch("subprocess.run"):
            runner._stop_app()

    def test_start_app_passes_test_mode_and_rpc_port(self, tmp_path):
        """--test-mode と --rpc-port が引数に含まれることを確認。"""
        runner = E2eSmokeRunner(port=18789, log_dir=str(tmp_path))
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc
            runner._start_app()
            args_list = mock_popen.call_args[0][0]
            cmd_str = " ".join(str(a) for a in args_list)
            assert "--test-mode" in cmd_str
            assert "18789" in cmd_str


# ---------------------------------------------------------------------------
# E2eSmokeRunner: dry_run モード
# ---------------------------------------------------------------------------

class TestE2eSmokeRunnerDryRun:
    def test_dry_run_returns_zero(self, tmp_path, capsys):
        """--dry-run は実シナリオを実行せず 0 を返す。"""
        runner = E2eSmokeRunner(port=18790, log_dir=str(tmp_path), dry_run=True)
        with patch("subprocess.run"):
            result = runner.run_all()
        assert result == 0

    def test_dry_run_prints_scenario_list(self, tmp_path, capsys):
        """--dry-run はシナリオ一覧を表示する。"""
        runner = E2eSmokeRunner(port=18790, log_dir=str(tmp_path), dry_run=True)
        with patch("subprocess.run"):
            runner.run_all()
        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert "S1" in captured.out

    def test_dry_run_skip_s6_shows_skip(self, tmp_path, capsys):
        """--dry-run + --skip-s6 では S6 が SKIP 表示される。"""
        runner = E2eSmokeRunner(port=18790, log_dir=str(tmp_path), dry_run=True, skip_s6=True)
        with patch("subprocess.run"):
            runner.run_all()
        captured = capsys.readouterr()
        assert "SKIP" in captured.out

    def test_dry_run_calls_stop_app_for_cleanup_check(self, tmp_path):
        """--dry-run でも _stop_app() （taskkill + netstat）が呼ばれる。"""
        runner = E2eSmokeRunner(port=18790, log_dir=str(tmp_path), dry_run=True)
        with patch.object(runner, "_stop_app") as mock_stop:
            with patch("subprocess.run"):
                runner.run_all()
        mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# E2eSmokeRunner.run_all: 統合的な PASS/FAIL 集計
# ---------------------------------------------------------------------------

class TestE2eSmokeRunnerRunAll:
    def _make_runner_with_mocked_scenarios(self, tmp_path, results):
        """シナリオメソッドをモックした runner を返す。
        新シナリオ（S_MIC_F8, S_LATCH_TOGGLE）も含む全 5 件をモックする。
        results は全 5 件分のリストを渡す。
        """
        runner = E2eSmokeRunner(port=18791, log_dir=str(tmp_path))
        scenario_names = [
            "_scenario_s1_route_a_solo",
            "_scenario_s4_s5_ptt",
            "_scenario_s6_latch",
            "_scenario_s_mic_f8",
            "_scenario_s_latch_toggle",
        ]
        for name, result in zip(scenario_names, results):
            setattr(runner, name, MagicMock(return_value=result))
        return runner

    def test_all_pass_returns_zero_failures(self, tmp_path):
        runner = self._make_runner_with_mocked_scenarios(
            tmp_path,
            [
                ScenarioResult("S1 route_a_solo_eng2jp", True, "ok"),
                ScenarioResult("S4-S5 ptt_press_release", True, "ok"),
                ScenarioResult("S6 latch_on_off", True, "ok"),
                ScenarioResult("S_MIC_F8 bug8_repro", True, "ok"),
                ScenarioResult("S_LATCH_TOGGLE", True, "ok"),
            ],
        )
        with patch.object(runner, "_start_app"), patch.object(runner, "_stop_app"):
            with patch.object(runner._client, "wait_ready"):
                fail_count = runner.run_all()
        assert fail_count == 0

    def test_one_fail_returns_one_failure(self, tmp_path):
        runner = self._make_runner_with_mocked_scenarios(
            tmp_path,
            [
                ScenarioResult("S1 route_a_solo_eng2jp", False, "ランプ赤にならず"),
                ScenarioResult("S4-S5 ptt_press_release", True, "ok"),
                ScenarioResult("S6 latch_on_off", True, "ok"),
                ScenarioResult("S_MIC_F8 bug8_repro", True, "ok"),
                ScenarioResult("S_LATCH_TOGGLE", True, "ok"),
            ],
        )
        with patch.object(runner, "_start_app"), patch.object(runner, "_stop_app"):
            with patch.object(runner._client, "wait_ready"):
                fail_count = runner.run_all()
        assert fail_count == 1

    def test_all_fail_returns_three_failures(self, tmp_path):
        """既存 3 シナリオが全 FAIL の場合（新シナリオ 2 件は PASS）。"""
        runner = self._make_runner_with_mocked_scenarios(
            tmp_path,
            [
                ScenarioResult("S1 route_a_solo_eng2jp", False, "ng"),
                ScenarioResult("S4-S5 ptt_press_release", False, "ng"),
                ScenarioResult("S6 latch_on_off", False, "ng"),
                ScenarioResult("S_MIC_F8 bug8_repro", True, "ok"),
                ScenarioResult("S_LATCH_TOGGLE", True, "ok"),
            ],
        )
        with patch.object(runner, "_start_app"), patch.object(runner, "_stop_app"):
            with patch.object(runner._client, "wait_ready"):
                fail_count = runner.run_all()
        assert fail_count == 3

    def test_skip_s6_not_counted_as_fail(self, tmp_path):
        """S6 を SKIP した場合、失敗数にカウントされないこと。"""
        runner = E2eSmokeRunner(port=18791, log_dir=str(tmp_path), skip_s6=True)
        setattr(runner, "_scenario_s1_route_a_solo",
                MagicMock(return_value=ScenarioResult("S1 route_a_solo_eng2jp", True, "ok")))
        setattr(runner, "_scenario_s4_s5_ptt",
                MagicMock(return_value=ScenarioResult("S4-S5 ptt_press_release", True, "ok")))
        setattr(runner, "_scenario_s_mic_f8",
                MagicMock(return_value=ScenarioResult("S_MIC_F8 bug8_repro", True, "ok")))
        setattr(runner, "_scenario_s_latch_toggle",
                MagicMock(return_value=ScenarioResult("S_LATCH_TOGGLE", True, "ok")))
        with patch.object(runner, "_start_app"), patch.object(runner, "_stop_app"):
            with patch.object(runner._client, "wait_ready"):
                fail_count = runner.run_all()
        assert fail_count == 0


# ===========================================================================
# 以下: e2e-full-automation/L1-prg-designer 追加分
# ===========================================================================


# ---------------------------------------------------------------------------
# env_check モジュールのユニットテスト
# ---------------------------------------------------------------------------

class TestEnvCheck:
    """env_check.py の各チェック関数のテスト。
    VB-CABLE 不在・PowerShell 不在・keyboard パッケージ不在の各ケースを確認する。
    """

    def test_check_vbcable_devices_passes_when_both_present(self):
        """CABLE Input / Output 両方が存在する場合はエラーを起こさない。"""
        from env_check import check_vbcable_devices
        mock_output = (
            "CABLE Input (VB-Audio Virtual Cable)\n"
            "CABLE Output (VB-Audio Virtual Cable)\n"
            "Speakers (Realtek)\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            check_vbcable_devices()  # 例外が起きなければ OK

    def test_check_vbcable_devices_raises_when_cable_input_missing(self):
        """CABLE Input が存在しない場合は RuntimeError が発生する。"""
        from env_check import check_vbcable_devices
        mock_output = "CABLE Output (VB-Audio Virtual Cable)\nSpeakers (Realtek)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            with pytest.raises(RuntimeError, match="CABLE Input"):
                check_vbcable_devices()

    def test_check_vbcable_devices_raises_when_cable_output_missing(self):
        """CABLE Output が存在しない場合は RuntimeError が発生する。"""
        from env_check import check_vbcable_devices
        mock_output = "CABLE Input (VB-Audio Virtual Cable)\nSpeakers (Realtek)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            with pytest.raises(RuntimeError, match="CABLE Output"):
                check_vbcable_devices()

    def test_check_vbcable_devices_raises_when_both_missing(self):
        """VB-CABLE が全くない場合は RuntimeError が発生する。"""
        from env_check import check_vbcable_devices
        mock_output = "Speakers (Realtek)\nMicrophone (Realtek)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            with pytest.raises(RuntimeError):
                check_vbcable_devices()

    def test_check_powershell_passes_when_available(self):
        """PowerShell が利用可能な場合はエラーを起こさない。"""
        from env_check import check_powershell
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="5.1.0.0\n", stderr="")
            check_powershell()  # 例外が起きなければ OK

    def test_check_powershell_raises_when_unavailable(self):
        """PowerShell が見つからない場合は RuntimeError が発生する。"""
        from env_check import check_powershell
        with patch("subprocess.run", side_effect=FileNotFoundError("powershell not found")):
            with pytest.raises(RuntimeError, match="[Pp]ower[Ss]hell"):
                check_powershell()

    def test_check_keyboard_package_passes_when_installed(self):
        """keyboard パッケージがインストール済みの場合はエラーを起こさない。"""
        from env_check import check_keyboard_package
        fake_keyboard = MagicMock()
        with patch.dict("sys.modules", {"keyboard": fake_keyboard}):
            check_keyboard_package()  # 例外が起きなければ OK

    def test_check_keyboard_package_raises_when_missing(self):
        """keyboard パッケージが未インストールの場合は RuntimeError が発生する。"""
        from env_check import check_keyboard_package
        with patch.dict("sys.modules", {"keyboard": None}):
            with pytest.raises(RuntimeError, match="keyboard"):
                check_keyboard_package()

    def test_run_all_checks_exits_with_code_2_on_failure(self):
        """run_all_checks は前提条件不足時に SystemExit(2) で終了する。"""
        from env_check import run_all_checks
        with patch("env_check.check_vbcable_devices", side_effect=RuntimeError("no VB-CABLE")):
            with pytest.raises(SystemExit) as exc_info:
                run_all_checks()
            assert exc_info.value.code == 2

    def test_run_all_checks_passes_when_all_ok(self):
        """全チェックが通れば run_all_checks は正常終了する。"""
        from env_check import run_all_checks
        with patch("env_check.check_vbcable_devices"), \
             patch("env_check.check_powershell"), \
             patch("env_check.check_keyboard_package"):
            run_all_checks()  # 例外が起きなければ OK


# ---------------------------------------------------------------------------
# audio_router モジュールのユニットテスト
# ---------------------------------------------------------------------------

class TestAudioRouter:
    """audio_router.py の AudioRouter クラスのテスト。"""

    def test_enter_saves_original_device(self):
        """__enter__ が元のデフォルトデバイスを保存することを確認。"""
        from audio_router import AudioRouter
        with patch("audio_router.get_default_playback_device", return_value="Speakers (Realtek)") as mock_get, \
             patch("audio_router.set_default_playback_device") as mock_set:
            router = AudioRouter()
            router.__enter__()
            assert router._original_default_device == "Speakers (Realtek)"
            mock_set.assert_called_once_with("CABLE Input (VB-Audio Virtual Cable)")

    def test_exit_restores_original_device(self):
        """__exit__ が元のデバイスに戻すことを確認。"""
        from audio_router import AudioRouter
        with patch("audio_router.get_default_playback_device", return_value="Speakers (Realtek)"), \
             patch("audio_router.set_default_playback_device") as mock_set:
            router = AudioRouter()
            router.__enter__()
            mock_set.reset_mock()
            router.__exit__(None, None, None)
            mock_set.assert_called_once_with("Speakers (Realtek)")

    def test_exit_restores_device_even_on_exception(self):
        """例外が発生してもデバイスが元に戻ることを確認（try/finally 保証）。"""
        from audio_router import AudioRouter
        with patch("audio_router.get_default_playback_device", return_value="Speakers (Realtek)"), \
             patch("audio_router.set_default_playback_device") as mock_set:
            router = AudioRouter()
            try:
                with router:
                    raise ValueError("test error")
            except ValueError:
                pass
            # __exit__ が呼ばれて元に戻す呼び出しが発生しているはず
            restore_calls = [c for c in mock_set.call_args_list
                             if c.args and c.args[0] == "Speakers (Realtek)"]
            assert len(restore_calls) >= 1

    def test_context_manager_sets_cable_input(self):
        """with AudioRouter() ブロック内でデバイスが CABLE Input に切替わる。"""
        from audio_router import AudioRouter
        set_calls = []

        def fake_set(name):
            set_calls.append(name)

        with patch("audio_router.get_default_playback_device", return_value="Speakers"), \
             patch("audio_router.set_default_playback_device", side_effect=fake_set):
            with AudioRouter():
                pass

        assert "CABLE Input (VB-Audio Virtual Cable)" in set_calls
        # 最終的に元に戻す呼び出しがある
        assert "Speakers" in set_calls

    def test_speak_calls_powershell(self):
        """speak() が PowerShell を呼び出すことを確認。"""
        from audio_router import AudioRouter
        with patch("audio_router.get_default_playback_device", return_value="Speakers"), \
             patch("audio_router.set_default_playback_device"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with AudioRouter() as router:
                router.speak("Hello world", lang="en", volume=60)
            assert mock_run.called
            args = mock_run.call_args[0][0]
            assert any("powershell" in str(a).lower() for a in args)

    def test_speak_includes_text_in_powershell_command(self):
        """speak() の text 引数が PowerShell コマンドに含まれる。"""
        from audio_router import AudioRouter
        with patch("audio_router.get_default_playback_device", return_value="Speakers"), \
             patch("audio_router.set_default_playback_device"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with AudioRouter() as router:
                router.speak("テストテキスト", lang="ja", volume=60)
            call_str = " ".join(str(a) for a in mock_run.call_args[0][0])
            assert "テストテキスト" in call_str

    def test_speak_includes_lang_in_powershell_command(self):
        """speak() の lang 引数が PowerShell コマンドに反映される。"""
        from audio_router import AudioRouter
        with patch("audio_router.get_default_playback_device", return_value="Speakers"), \
             patch("audio_router.set_default_playback_device"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with AudioRouter() as router:
                router.speak("Hello", lang="ja", volume=60)
            call_str = " ".join(str(a) for a in mock_run.call_args[0][0])
            assert "ja" in call_str

    def test_get_default_playback_device_returns_device_name(self):
        """get_default_playback_device() が文字列を返す。"""
        from audio_router import get_default_playback_device
        mock_output = "Speakers (Realtek High Definition Audio)\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            result = get_default_playback_device()
            assert isinstance(result, str)
            assert len(result) > 0

    def test_set_default_playback_device_calls_powershell(self):
        """set_default_playback_device() が PowerShell を呼び出す。"""
        from audio_router import set_default_playback_device
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            set_default_playback_device("CABLE Input (VB-Audio Virtual Cable)")
            assert mock_run.called
            args = mock_run.call_args[0][0]
            assert any("powershell" in str(a).lower() for a in args)


# ---------------------------------------------------------------------------
# hotkey_simulator モジュールのユニットテスト
# ---------------------------------------------------------------------------

class TestHotkeySimulator:
    """hotkey_simulator.py の各関数のテスト。"""

    def test_press_hotkey_calls_keyboard_press(self):
        """press_hotkey() が keyboard.press() を呼び出す。"""
        mock_keyboard = MagicMock()
        with patch.dict("sys.modules", {"keyboard": mock_keyboard}):
            import importlib
            import hotkey_simulator
            importlib.reload(hotkey_simulator)
            from hotkey_simulator import press_hotkey as fresh_press
            fresh_press("f8")
            mock_keyboard.press.assert_called_once_with("f8")

    def test_release_hotkey_calls_keyboard_release(self):
        """release_hotkey() が keyboard.release() を呼び出す。"""
        mock_keyboard = MagicMock()
        with patch.dict("sys.modules", {"keyboard": mock_keyboard}):
            import importlib
            import hotkey_simulator
            importlib.reload(hotkey_simulator)
            from hotkey_simulator import release_hotkey as fresh_release
            fresh_release("f8")
            mock_keyboard.release.assert_called_once_with("f8")

    def test_press_hotkey_default_key_is_f8(self):
        """press_hotkey() のデフォルトキーは 'f8' であること。"""
        mock_keyboard = MagicMock()
        with patch.dict("sys.modules", {"keyboard": mock_keyboard}):
            import importlib
            import hotkey_simulator
            importlib.reload(hotkey_simulator)
            from hotkey_simulator import press_hotkey as fresh_press
            fresh_press()
            mock_keyboard.press.assert_called_once_with("f8")

    def test_release_hotkey_default_key_is_f8(self):
        """release_hotkey() のデフォルトキーは 'f8' であること。"""
        mock_keyboard = MagicMock()
        with patch.dict("sys.modules", {"keyboard": mock_keyboard}):
            import importlib
            import hotkey_simulator
            importlib.reload(hotkey_simulator)
            from hotkey_simulator import release_hotkey as fresh_release
            fresh_release()
            mock_keyboard.release.assert_called_once_with("f8")

    def test_press_and_hold_calls_press_sleep_release(self):
        """press_and_hold() が press → sleep → release の順で呼ぶ。"""
        mock_keyboard = MagicMock()
        with patch.dict("sys.modules", {"keyboard": mock_keyboard}), \
             patch("time.sleep") as mock_sleep:
            import importlib
            import hotkey_simulator
            importlib.reload(hotkey_simulator)
            from hotkey_simulator import press_and_hold as fresh_pah
            fresh_pah("f8", duration=0.1)
            mock_keyboard.press.assert_called_once_with("f8")
            mock_keyboard.release.assert_called_once_with("f8")
            mock_sleep.assert_called()

    def test_press_and_hold_duration_is_passed_to_sleep(self):
        """press_and_hold() の duration 引数が time.sleep に渡される。"""
        mock_keyboard = MagicMock()
        with patch.dict("sys.modules", {"keyboard": mock_keyboard}), \
             patch("time.sleep") as mock_sleep:
            import importlib
            import hotkey_simulator
            importlib.reload(hotkey_simulator)
            from hotkey_simulator import press_and_hold as fresh_pah
            fresh_pah("f8", duration=3.0)
            mock_sleep.assert_called_with(3.0)

    def test_check_admin_privileges_returns_bool(self):
        """check_admin_privileges() が bool 値を返す。"""
        from hotkey_simulator import check_admin_privileges
        result = check_admin_privileges()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# S_MIC_F8 シナリオのユニットテスト
# ---------------------------------------------------------------------------

class TestScenarioMicF8:
    """S_MIC_F8 シナリオ（バグ#8 再現）のテスト。
    実アプリは起動しない。RPC/AudioRouter/keyboard はすべて mock。
    """

    def _make_runner(self, tmp_path):
        runner = E2eSmokeRunner(port=18792, log_dir=str(tmp_path))
        return runner

    def test_s_mic_f8_scenario_method_exists(self, tmp_path):
        """_scenario_s_mic_f8 メソッドが E2eSmokeRunner に存在する。"""
        runner = self._make_runner(tmp_path)
        assert hasattr(runner, "_scenario_s_mic_f8")

    def test_s_mic_f8_returns_scenario_result(self, tmp_path):
        """_scenario_s_mic_f8 が ScenarioResult を返す。"""
        runner = self._make_runner(tmp_path)
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
            "route_b_peak_level": 2000,
        }
        mock_client.poll_until.return_value = True
        runner._client = mock_client
        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls, \
             patch("hotkey_simulator.press_hotkey"), \
             patch("hotkey_simulator.release_hotkey"):
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar_cls.return_value = mock_ar
            result = runner._scenario_s_mic_f8()
        assert isinstance(result, ScenarioResult)

    def test_s_mic_f8_calls_press_before_speak(self, tmp_path):
        """F8 press が AudioRouter.speak より先に呼ばれる。"""
        runner = self._make_runner(tmp_path)
        call_order = []
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
            "route_b_peak_level": 2000,
        }
        mock_client.poll_until.return_value = True
        runner._client = mock_client

        def fake_press(key="f8"):
            call_order.append(f"press:{key}")

        def fake_speak(*args, **kwargs):
            call_order.append("speak")

        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls, \
             patch("hotkey_simulator.press_hotkey", side_effect=fake_press), \
             patch("hotkey_simulator.release_hotkey"):
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar.speak = MagicMock(side_effect=fake_speak)
            mock_ar_cls.return_value = mock_ar
            runner._scenario_s_mic_f8()

        press_idx = next((i for i, c in enumerate(call_order) if c.startswith("press")), None)
        speak_idx = next((i for i, c in enumerate(call_order) if c == "speak"), None)
        if press_idx is not None and speak_idx is not None:
            assert press_idx < speak_idx, f"press が speak より後になっている: {call_order}"

    def test_s_mic_f8_calls_release_after_speak(self, tmp_path):
        """F8 release が AudioRouter.speak の後に呼ばれる。"""
        runner = self._make_runner(tmp_path)
        call_order = []
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
            "route_b_peak_level": 2000,
        }
        mock_client.poll_until.return_value = True
        runner._client = mock_client

        def fake_release(key="f8"):
            call_order.append(f"release:{key}")

        def fake_speak(*args, **kwargs):
            call_order.append("speak")

        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls, \
             patch("hotkey_simulator.press_hotkey"), \
             patch("hotkey_simulator.release_hotkey", side_effect=fake_release):
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar.speak = MagicMock(side_effect=fake_speak)
            mock_ar_cls.return_value = mock_ar
            runner._scenario_s_mic_f8()

        speak_idx = next((i for i, c in enumerate(call_order) if c == "speak"), None)
        release_idx = next((i for i, c in enumerate(call_order) if c.startswith("release")), None)
        if speak_idx is not None and release_idx is not None:
            assert speak_idx < release_idx, f"speak が release より後になっている: {call_order}"

    def test_s_mic_f8_fail_when_peak_zero(self, tmp_path):
        """バグ#8 発生時（peak=0）は FAIL を返す。"""
        runner = self._make_runner(tmp_path)
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        # peak_level=0 を返す（バグ#8 の状態）
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
            "route_b_peak_level": 0,
        }
        mock_client.poll_until.return_value = True
        runner._client = mock_client
        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls, \
             patch("hotkey_simulator.press_hotkey"), \
             patch("hotkey_simulator.release_hotkey"):
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar_cls.return_value = mock_ar
            result = runner._scenario_s_mic_f8()
        assert result.passed is False

    def test_s_mic_f8_uses_cable_output_device(self, tmp_path):
        """S_MIC_F8 では CABLE Output (VB-Audio Virtual Cable) をデバイスに使う。"""
        runner = self._make_runner(tmp_path)
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
            "route_b_peak_level": 2000,
        }
        mock_client.poll_until.return_value = True
        runner._client = mock_client
        device_used = []

        def fake_set_value(tag, value):
            if tag == "route_b_device":
                device_used.append(value)
            return {}

        mock_client.set_value = MagicMock(side_effect=fake_set_value)
        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls, \
             patch("hotkey_simulator.press_hotkey"), \
             patch("hotkey_simulator.release_hotkey"):
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar_cls.return_value = mock_ar
            runner._scenario_s_mic_f8()
        assert any("CABLE Output" in d for d in device_used), \
            f"CABLE Output が設定されていない。実際: {device_used}"


# ---------------------------------------------------------------------------
# S_LATCH_TOGGLE シナリオのユニットテスト
# ---------------------------------------------------------------------------

class TestScenarioLatchToggle:
    """S_LATCH_TOGGLE シナリオ（ラッチ ON/OFF 繰り返し）のテスト。"""

    def _make_runner(self, tmp_path):
        return E2eSmokeRunner(port=18793, log_dir=str(tmp_path))

    def test_s_latch_toggle_method_exists(self, tmp_path):
        """_scenario_s_latch_toggle メソッドが E2eSmokeRunner に存在する。"""
        runner = self._make_runner(tmp_path)
        assert hasattr(runner, "_scenario_s_latch_toggle")

    def test_s_latch_toggle_returns_scenario_result(self, tmp_path):
        """_scenario_s_latch_toggle が ScenarioResult を返す。"""
        runner = self._make_runner(tmp_path)
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
        }
        mock_client.poll_until.return_value = True
        runner._client = mock_client
        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls:
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar_cls.return_value = mock_ar
            result = runner._scenario_s_latch_toggle()
        assert isinstance(result, ScenarioResult)

    def test_s_latch_toggle_sets_latch_on_then_off(self, tmp_path):
        """ラッチ ON → OFF の順で ptt_latch_check が切替わる。"""
        runner = self._make_runner(tmp_path)
        latch_values = []
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
        }
        mock_client.poll_until.return_value = True

        def fake_set_value(tag, value):
            if tag == "ptt_latch_check":
                latch_values.append(value)
            return {}

        mock_client.set_value = MagicMock(side_effect=fake_set_value)
        runner._client = mock_client
        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls:
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar_cls.return_value = mock_ar
            runner._scenario_s_latch_toggle()
        # True が少なくとも 1 回、False が少なくとも 1 回ある
        assert True in latch_values, f"latch ON が呼ばれていない: {latch_values}"
        assert False in latch_values, f"latch OFF が呼ばれていない: {latch_values}"

    def test_s_latch_toggle_verifies_route_b_restart(self, tmp_path):
        """ラッチ再 ON 後に route_b_state RUNNING を確認する。"""
        runner = self._make_runner(tmp_path)
        mock_client = MagicMock()
        mock_client.wait_ready.return_value = None
        mock_client.get_status.return_value = {
            "route_b_state": "RUNNING",
            "route_b_audio_gate": True,
        }
        mock_client.poll_until.return_value = True
        runner._client = mock_client
        with patch.object(runner, "_start_app"), \
             patch.object(runner, "_stop_app"), \
             patch("audio_router.AudioRouter") as mock_ar_cls:
            mock_ar = MagicMock()
            mock_ar.__enter__ = MagicMock(return_value=mock_ar)
            mock_ar.__exit__ = MagicMock(return_value=False)
            mock_ar_cls.return_value = mock_ar
            result = runner._scenario_s_latch_toggle()
        # poll_until が複数回呼ばれているはず（各 ON/OFF ステップで確認）
        assert mock_client.poll_until.call_count >= 2, \
            f"poll_until の呼び出し回数が少ない: {mock_client.poll_until.call_count}"


# ---------------------------------------------------------------------------
# run_all: 新シナリオが dry-run に含まれるかのテスト
# ---------------------------------------------------------------------------

class TestDryRunNewScenarios:
    """--dry-run で S_MIC_F8, S_LATCH_TOGGLE が一覧表示されることを確認。"""

    def test_dry_run_shows_s_mic_f8(self, tmp_path, capsys):
        """--dry-run 出力に S_MIC_F8 が含まれる。"""
        runner = E2eSmokeRunner(port=18794, log_dir=str(tmp_path), dry_run=True)
        with patch("subprocess.run"):
            runner.run_all()
        captured = capsys.readouterr()
        assert "S_MIC_F8" in captured.out or "MIC_F8" in captured.out or "mic_f8" in captured.out

    def test_dry_run_shows_s_latch_toggle(self, tmp_path, capsys):
        """--dry-run 出力に S_LATCH_TOGGLE が含まれる。"""
        runner = E2eSmokeRunner(port=18794, log_dir=str(tmp_path), dry_run=True)
        with patch("subprocess.run"):
            runner.run_all()
        captured = capsys.readouterr()
        assert "LATCH_TOGGLE" in captured.out or "latch_toggle" in captured.out

    def test_run_all_includes_new_scenarios_in_count(self, tmp_path):
        """run_all が新シナリオを含む 5 件以上のシナリオを実行する。"""
        runner = E2eSmokeRunner(port=18794, log_dir=str(tmp_path))
        # 全シナリオをモック
        for attr in ["_scenario_s1_route_a_solo", "_scenario_s4_s5_ptt",
                     "_scenario_s6_latch", "_scenario_s_mic_f8", "_scenario_s_latch_toggle"]:
            setattr(runner, attr,
                    MagicMock(return_value=ScenarioResult(attr, True, "ok")))
        with patch("subprocess.run"):
            fail_count = runner.run_all()
        assert fail_count == 0
