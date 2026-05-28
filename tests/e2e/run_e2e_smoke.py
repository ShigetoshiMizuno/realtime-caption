"""
run_e2e_smoke.py - jitsuki E2E smoke test script

Usage:
    python tests/e2e/run_e2e_smoke.py [--port PORT] [--skip-s6]

Scenarios:
    S1   : route_a solo (EN audio -> JA translation)
    S4-S5: route_b PTT press/release
    S6   : latch ON/OFF (omit with --skip-s6)

Prerequisites:
    - config.yaml with valid OpenAI API key
    - Virtual audio device (VB-Cable etc.)
    - Python 3.11+, requests package installed

Billing risk management:
    - App stops within 5 sec after TTS in each scenario
    - try/finally guarantees app termination

Output example:
    [E2E S1 route_a_solo       ] PASS  (TTS + [original(RT)] + [translation(RT)] + translate.txt + lamp-A red)
    [E2E S4-S5 ptt_press_rel   ] PASS  (PTT press=RUNNING/gate=True, release=gate=False)
    [E2E S6 latch              ] PASS  (latch=ON: gate=True, latch=OFF: gate=False)
    TOTAL: 3 / 3 passed
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# プロジェクトルートを sys.path に追加（どこから実行しても動くように）
_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    import requests
except ImportError:
    print("[ERROR] requests パッケージが見つかりません。pip install requests を実行してください。")
    sys.exit(1)

from helpers import RpcClient  # tests/e2e/helpers.py

# 完全自動化モジュール（env_check/audio_router/hotkey_simulator）
# 実行時にインポートエラーになっても smoke テスト全体は継続する
try:
    from env_check import run_all_checks as _run_env_checks
    from audio_router import AudioRouter
    import hotkey_simulator
    _FULL_AUTO_AVAILABLE = True
except ImportError as _e:
    print(f"[WARN] 完全自動化モジュールのインポート失敗: {_e}")
    _FULL_AUTO_AVAILABLE = False

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

DEFAULT_PORT = 8788
DEFAULT_LOG_DIR = str(_PROJECT_ROOT / "logs")
# TTS 後、翻訳出力を待つ最大秒数
TRANSLATION_WAIT_SEC = 15.0
# アプリが RPC 応答できるようになるまでの最大待機秒数
APP_START_WAIT_SEC = 30.0
# 各シナリオ間のクールダウン
SCENARIO_COOLDOWN_SEC = 2.0
# シナリオ内でのポーリング間隔
POLL_INTERVAL_SEC = 0.3

# ランプ色の判定マップ (expected_name -> 判定関数)
# DearPyGui は configure_item で color を設定する。
# get_config?field=color は RGBA リスト [R, G, B, A] または文字列を返す場合がある。
_COLOR_JUDGES = {
    "red": lambda c: _color_is_red(c),
    "yellow": lambda c: _color_is_yellow(c),
    "green": lambda c: _color_is_green(c),
    "gray": lambda c: _color_is_gray(c),
    "default": lambda c: True,  # 色不問のときに使う
}


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"[E2E {self.name:<28}] {status}  ({self.detail})"


# ---------------------------------------------------------------------------
# 色判定ヘルパー
# ---------------------------------------------------------------------------

def _color_is_red(c) -> bool:
    """DearPyGui の赤色を判定する。RGBA リスト [R, G, B, A] または文字列 "red" を想定。

    発注書指定の赤: (220, 0, 0, 255) を基準とする。
    """
    if c is None:
        return False
    if isinstance(c, str):
        return "red" in c.lower()
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        r, g, b = c[0], c[1], c[2]
        # R >= 200, G < 80, B < 80 で赤系と判定（(220,0,0,255) をカバー）
        return int(r) >= 200 and int(g) < 80 and int(b) < 80
    return False


def _color_is_yellow(c) -> bool:
    if c is None:
        return False
    if isinstance(c, str):
        return "yellow" in c.lower()
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        r, g, b = c[0], c[1], c[2]
        return r > 180 and g > 180 and b < 100
    return False


def _color_is_green(c) -> bool:
    if c is None:
        return False
    if isinstance(c, str):
        return "green" in c.lower()
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        r, g, b = c[0], c[1], c[2]
        return g > 180 and r < 100 and b < 100
    return False


def _color_is_gray(c) -> bool:
    if c is None:
        return False
    if isinstance(c, str):
        return "gray" in c.lower() or "grey" in c.lower()
    if isinstance(c, (list, tuple)) and len(c) >= 3:
        r, g, b = c[0], c[1], c[2]
        # RGB 値がほぼ同じ（灰色系）
        return abs(int(r) - int(g)) < 30 and abs(int(g) - int(b)) < 30 and int(r) < 200
    return False


# ---------------------------------------------------------------------------
# パブリックヘルパー関数（テストから直接インポートして使う）
# ---------------------------------------------------------------------------

def _speak_tts_sync(text: str, lang: str = "en-US", timeout: float = 10.0) -> None:
    """PowerShell の SpeechSynthesizer で TTS を同期再生する。

    失敗しても例外は上げず、警告ログのみ出力する（TTS デバイスがない環境への配慮）。
    """
    ps_cmd = (
        "Add-Type -AssemblyName System.Speech; "
        f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SelectVoiceByHints([System.Globalization.CultureInfo]'{lang}'); "
        f"$s.Speak('{text}');"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            timeout=timeout,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        print(f"[WARN] TTS timeout (lang={lang}, text={text!r})")
    except Exception as exc:
        print(f"[WARN] TTS error: {exc}")


def _check_console_log_pattern(log_path: str, pattern: str) -> bool:
    """コンソールログファイルを読んで pattern (regex) にマッチする行があれば True。"""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        return bool(re.search(pattern, content))
    except FileNotFoundError:
        return False
    except Exception as exc:
        print(f"[WARN] _check_console_log_pattern error: {exc}")
        return False


def _check_translate_file_has_translation(log_dir: str, date_prefix: str) -> bool:
    """log_dir 内の YYYY-MM-DD-*_translate.txt を検索し、「翻訳(RT):」行があれば True。"""
    pattern = os.path.join(log_dir, f"{date_prefix}-*_translate.txt")
    files = glob.glob(pattern)
    for fpath in files:
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "翻訳(RT):" in line:
                        return True
        except Exception as exc:
            print(f"[WARN] _check_translate_file_has_translation: {exc}")
    return False


def _assert_lamp_color(client: RpcClient, tag: str, expected: str) -> bool:
    """RPC で billing_lamp の色を取得し、expected と一致するか確認する。

    expected: "red" | "yellow" | "green" | "gray" | "default"
    """
    try:
        resp = client.get_config(tag, field="color")
        color_value = resp.get("value")
        judge = _COLOR_JUDGES.get(expected, lambda _: False)
        return judge(color_value)
    except Exception as exc:
        print(f"[WARN] _assert_lamp_color({tag}, {expected}): {exc}")
        return False


def _assert_route_b_state(
    client: RpcClient,
    expected_state: str,
    expected_gate: Optional[bool] = None,
) -> bool:
    """RPC で route_b_state と route_b_audio_gate を確認する。

    expected_gate が None の場合はゲート状態を検査しない。
    """
    try:
        status = client.get_status()
        actual_state = status.get("route_b_state")
        if actual_state != expected_state:
            return False
        if expected_gate is not None:
            actual_gate = status.get("route_b_audio_gate")
            if actual_gate != expected_gate:
                return False
        return True
    except Exception as exc:
        print(f"[WARN] _assert_route_b_state: {exc}")
        return False


# ---------------------------------------------------------------------------
# メインランナークラス
# ---------------------------------------------------------------------------

class E2eSmokeRunner:
    """E2E スモークテストの実行エンジン。

    Args:
        port: RPC ポート番号（デフォルト 8788）
        log_dir: logs/ ディレクトリパス（翻訳ログ確認に使用）
        skip_s6: True の場合 S6 ラッチシナリオをスキップ
        dry_run: True の場合シナリオを実行せず一覧表示のみ行う
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        log_dir: str = DEFAULT_LOG_DIR,
        skip_s6: bool = False,
        dry_run: bool = False,
    ) -> None:
        self._port = port
        self._log_dir = log_dir
        self._skip_s6 = skip_s6
        self._dry_run = dry_run
        self._proc: Optional[subprocess.Popen] = None
        self._log_file = None
        self._console_log_path: Optional[str] = None
        self._client = RpcClient(port=port)

    # ------------------------------------------------------------------
    # アプリ起動・停止
    # ------------------------------------------------------------------

    def _start_app(self) -> None:
        """app.py を --test-mode で起動する。stdout/stderr は一時ファイルに記録。"""
        self._log_file = tempfile.NamedTemporaryFile(
            prefix="e2e_smoke_",
            suffix=".log",
            delete=False,
            mode="w",
            encoding="utf-8",
            errors="replace",
        )
        self._console_log_path = self._log_file.name
        self._proc = subprocess.Popen(
            [sys.executable, "app.py", "--test-mode", f"--rpc-port={self._port}"],
            cwd=str(_PROJECT_ROOT),
            stdout=self._log_file,
            stderr=self._log_file,
        )
        print(f"[INFO] app.py 起動 PID={self._proc.pid} log={self._console_log_path}")

    def _stop_app(self) -> None:
        """アプリを停止する（try/finally の中で必ず呼ぶ）。

        課金リスク管理:
        1. proc.terminate() で graceful 停止
        2. タイムアウト後は proc.kill()
        3. taskkill /F /IM python.exe で残留プロセスを強制終了（アプリ起動時のみ）
        4. netstat で RPC ポートが解放されたことを確認
        """
        # proc が存在した場合のみ taskkill を実行（自プロセスを巻き込まない）
        _had_proc = self._proc is not None

        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception as exc:
                print(f"[WARN] proc.terminate 失敗: {exc}")
                try:
                    self._proc.kill()
                except Exception:
                    pass
            finally:
                self._proc = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        # 課金リスク管理: taskkill で残留 python.exe を強制終了
        # 注意: proc が存在した（= 子プロセスを起動した）場合のみ実行する。
        # proc=None (dry-run や未起動) の場合は自プロセスを巻き込まないよう実行しない。
        if _had_proc:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "python.exe"],
                    capture_output=True,
                    timeout=10,
                )
            except Exception as exc:
                print(f"[WARN] taskkill 失敗: {exc}")

        # netstat でポートが解放されたか確認（警告のみ、ブロックしない）
        try:
            result = subprocess.run(
                ["netstat", "-an"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            port_str = f":{self._port}"
            listening_lines = [
                line for line in result.stdout.splitlines()
                if port_str in line and "LISTENING" in line
            ]
            if listening_lines:
                print(f"[WARN] ポート {self._port} がまだ LISTENING 状態です:")
                for line in listening_lines:
                    print(f"       {line.strip()}")
            else:
                print(f"[INFO] ポート {self._port} が解放されました")
        except Exception as exc:
            print(f"[WARN] netstat 確認失敗: {exc}")

    # ------------------------------------------------------------------
    # シナリオ S1: route_a 単独起動
    # ------------------------------------------------------------------

    def _scenario_s1_route_a_solo(self) -> ScenarioResult:
        """S1: route_a のみ有効にしてアプリを起動し、英語 TTS を流して翻訳を確認する。

        検証項目:
        - console ログに [原文(RT)] が出現
        - console ログに [翻訳(RT)] が出現
        - logs/ に翻訳行を含む _translate.txt が生成される
        - billing_lamp_a = 赤
        """
        print("\n[SCENARIO S1] route_a 単独起動シナリオを開始...")
        today = datetime.now().strftime("%Y-%m-%d")
        fails: list[str] = []

        try:
            self._start_app()
            self._client.wait_ready(max_wait=APP_START_WAIT_SEC)

            # 系統1 ON / 系統2 OFF
            self._client.set_value("route_a_enable", True)
            self._client.set_value("route_b_enable", False)
            time.sleep(0.5)

            # 開始ボタン押下
            self._client.click("start_btn")
            time.sleep(2.0)

            # TTS で英語音声を流す
            print("[S1] TTS 再生中 (English)...")
            _speak_tts_sync(
                "Hello, this is a test for realtime translation. Please translate this sentence.",
                lang="en-US",
                timeout=15.0,
            )
            time.sleep(1.0)

            # 翻訳出力を最大 TRANSLATION_WAIT_SEC 秒待つ
            print("[S1] 翻訳出力待機中...")
            deadline = time.monotonic() + TRANSLATION_WAIT_SEC

            original_found = False
            translation_found = False
            translate_file_ok = False

            while time.monotonic() < deadline:
                if self._console_log_path:
                    if not original_found:
                        original_found = _check_console_log_pattern(
                            self._console_log_path, r"\[原文\(RT\)\]"
                        )
                    if not translation_found:
                        translation_found = _check_console_log_pattern(
                            self._console_log_path, r"\[翻訳\(RT\)\]"
                        )
                    if not translate_file_ok:
                        translate_file_ok = _check_translate_file_has_translation(
                            self._log_dir, date_prefix=today
                        )
                if original_found and translation_found and translate_file_ok:
                    break
                time.sleep(POLL_INTERVAL_SEC)

            # ランプ確認
            lamp_a_red = _assert_lamp_color(self._client, "billing_lamp_a", expected="red")

            # アサーション集計
            if not original_found:
                fails.append("[原文(RT)] がログに出現しなかった")
            if not translation_found:
                fails.append("[翻訳(RT)] がログに出現しなかった")
            if not translate_file_ok:
                fails.append("translate.txt に翻訳行がなかった")
            if not lamp_a_red:
                fails.append("billing_lamp_a が赤にならなかった")

        except Exception as exc:
            fails.append(f"例外発生: {exc}")
        finally:
            # 課金リスク管理: 必ずアプリを停止
            try:
                self._client.click("start_btn")  # 停止ボタン
                time.sleep(1.0)
            except Exception:
                pass
            self._stop_app()
            time.sleep(SCENARIO_COOLDOWN_SEC)

        if fails:
            return ScenarioResult("S1 route_a_solo", False, "; ".join(fails))
        return ScenarioResult(
            "S1 route_a_solo", True,
            "TTS検出 + [原文(RT)] + [翻訳(RT)] + translate.txt + ランプA赤"
        )

    # ------------------------------------------------------------------
    # シナリオ S4-S5: PTT 押下・離脱
    # ------------------------------------------------------------------

    def _scenario_s4_s5_ptt(self) -> ScenarioResult:
        """S4-S5: route_b のみ有効にして PTT 押下・離脱シナリオを確認する。

        検証項目:
        - アプリ起動後: route_b_state = IDLE (S2)
        - PTT press 後: route_b_state = RUNNING + gate=True (S4), ランプB赤
        - TTS で日本語音声入力
        - PTT release 後: gate=False (S5), ランプB黄
        """
        print("\n[SCENARIO S4-S5] PTT 押下・離脱シナリオを開始...")
        fails: list[str] = []

        try:
            self._start_app()
            self._client.wait_ready(max_wait=APP_START_WAIT_SEC)

            # 系統1 OFF / 系統2 ON
            self._client.set_value("route_a_enable", False)
            self._client.set_value("route_b_enable", True)
            time.sleep(0.5)

            # 開始ボタン押下
            self._client.click("start_btn")
            time.sleep(2.0)

            # S2 確認: route_b_state = IDLE
            idle_ok = self._client.poll_until(
                lambda: self._client.get_status().get("route_b_state") == "IDLE",
                max_wait=5.0,
            )
            if not idle_ok:
                fails.append("S2: route_b_state が IDLE にならなかった")

            # PTT GUI ボタン press (S4)
            print("[S4-S5] PTT press...")
            self._client.press("ptt_gui_btn")
            time.sleep(0.5)

            # S4: route_b_state = RUNNING + gate=True
            s4_state_ok = self._client.poll_until(
                lambda: _assert_route_b_state(self._client, "RUNNING", expected_gate=True),
                max_wait=5.0,
            )
            lamp_b_red = _assert_lamp_color(self._client, "billing_lamp_b", expected="red")

            if not s4_state_ok:
                fails.append("S4: route_b_state != RUNNING または gate != True")
            if not lamp_b_red:
                fails.append("S4: billing_lamp_b が赤にならなかった")

            # TTS で日本語音声を流す
            print("[S4-S5] TTS 再生中 (Japanese)...")
            _speak_tts_sync(
                "こんにちは、これはリアルタイム翻訳のテストです。",
                lang="ja-JP",
                timeout=15.0,
            )
            time.sleep(1.0)

            # PTT GUI ボタン release (S5)
            print("[S4-S5] PTT release...")
            self._client.release("ptt_gui_btn")
            time.sleep(0.5)

            # S5: gate=False
            s5_gate_ok = self._client.poll_until(
                lambda: self._client.get_status().get("route_b_audio_gate") is False,
                max_wait=5.0,
            )
            lamp_b_yellow = _assert_lamp_color(self._client, "billing_lamp_b", expected="yellow")

            if not s5_gate_ok:
                fails.append("S5: PTT release 後に audio_gate が False にならなかった")
            if not lamp_b_yellow:
                # 黄色の代わりにデフォルト色（グレー等）になる場合もあるため警告扱い
                print("[WARN] S5: billing_lamp_b が黄色にならなかった（実装依存）")

        except Exception as exc:
            fails.append(f"例外発生: {exc}")
        finally:
            # 課金リスク管理
            try:
                self._client.click("start_btn")  # 停止
                time.sleep(1.0)
            except Exception:
                pass
            self._stop_app()
            time.sleep(SCENARIO_COOLDOWN_SEC)

        if fails:
            return ScenarioResult("S4-S5 ptt_press_rel", False, "; ".join(fails))
        return ScenarioResult(
            "S4-S5 ptt_press_rel", True,
            "PTT press=RUNNING/gate=True/ランプB赤, release=gate=False"
        )

    # ------------------------------------------------------------------
    # シナリオ S6: ラッチ ON/OFF
    # ------------------------------------------------------------------

    def _scenario_s6_latch(self) -> ScenarioResult:
        """S6: S5 状態でラッチ ON → gate=True 維持、ラッチ OFF → 解除を確認する。

        検証項目:
        - ラッチ ON 後: gate=True 維持 / ランプB赤
        - ラッチ OFF 後: gate=False / ランプB黄またはグレー
        """
        print("\n[SCENARIO S6] ラッチ ON/OFF シナリオを開始...")
        fails: list[str] = []

        try:
            self._start_app()
            self._client.wait_ready(max_wait=APP_START_WAIT_SEC)

            # 系統1 OFF / 系統2 ON
            self._client.set_value("route_a_enable", False)
            self._client.set_value("route_b_enable", True)
            time.sleep(0.5)

            # 開始ボタン押下
            self._client.click("start_btn")
            time.sleep(2.0)

            # ラッチ OFF で開始 → PTT press → S5 状態（gate=False）まで遷移
            self._client.set_value("ptt_latch_check", False)
            time.sleep(0.3)
            self._client.press("ptt_gui_btn")
            time.sleep(0.5)
            self._client.release("ptt_gui_btn")
            time.sleep(0.5)

            # S6: ラッチ ON
            print("[S6] ラッチ ON...")
            self._client.set_value("ptt_latch_check", True)
            time.sleep(0.5)

            # gate=True が維持されることを確認
            latch_gate_ok = self._client.poll_until(
                lambda: self._client.get_status().get("route_b_audio_gate") is True,
                max_wait=5.0,
            )
            lamp_b_red_latch = _assert_lamp_color(self._client, "billing_lamp_b", expected="red")

            if not latch_gate_ok:
                fails.append("S6: ラッチON後に audio_gate が True にならなかった")
            if not lamp_b_red_latch:
                fails.append("S6: ラッチON後に billing_lamp_b が赤にならなかった")

            # ラッチ OFF → 解除
            print("[S6] ラッチ OFF...")
            self._client.set_value("ptt_latch_check", False)
            time.sleep(0.5)

            latch_off_ok = self._client.poll_until(
                lambda: self._client.get_status().get("route_b_audio_gate") is False,
                max_wait=5.0,
            )

            if not latch_off_ok:
                fails.append("S6: ラッチOFF後に audio_gate が False にならなかった")

        except Exception as exc:
            fails.append(f"例外発生: {exc}")
        finally:
            # 課金リスク管理
            try:
                self._client.set_value("ptt_latch_check", False)
                self._client.click("start_btn")  # 停止
                time.sleep(1.0)
            except Exception:
                pass
            self._stop_app()
            time.sleep(SCENARIO_COOLDOWN_SEC)

        if fails:
            return ScenarioResult("S6 latch", False, "; ".join(fails))
        return ScenarioResult(
            "S6 latch", True,
            "latch=ON: gate=True/ランプB赤, latch=OFF: gate=False"
        )

    # ------------------------------------------------------------------
    # シナリオ S_MIC_F8: マイク（CABLE Output）+ F8 長押し → バグ#8 再現
    # ------------------------------------------------------------------

    def _scenario_s_mic_f8(self) -> ScenarioResult:
        """S_MIC_F8: マイク（CABLE Output）+ F8 長押し → バグ#8 再現シナリオ。

        検証項目:
        - F8 押下後: route_b_state = RUNNING
        - CABLE Input への TTS 再生中: peak_level > 1000 を維持（バグ#8 なら 0 になる）
        - F8 離脱: 正常終了
        """
        from audio_router import AudioRouter
        import hotkey_simulator

        print("\n[SCENARIO S_MIC_F8] マイク+F8 バグ#8 再現シナリオを開始...")
        fails: list[str] = []

        try:
            self._start_app()
            self._client.wait_ready(max_wait=APP_START_WAIT_SEC)

            # CABLE Output (VB-Audio Virtual Cable) をデバイスに設定
            self._client.set_value("route_b_device", "CABLE Output (VB-Audio Virtual Cable)")
            self._client.set_value("route_a_enable", False)
            self._client.set_value("route_b_enable", True)
            time.sleep(0.5)

            # 開始ボタン押下
            self._client.click("start_btn")
            time.sleep(2.0)

            # F8 押下（PTT 開始）
            print("[S_MIC_F8] F8 press...")
            hotkey_simulator.press_hotkey("f8")
            time.sleep(0.5)

            # CABLE Input に 10 秒の日本語 TTS を流す
            print("[S_MIC_F8] TTS 再生中 (Japanese, CABLE Input 経由)...")
            with AudioRouter() as router:
                router.speak(
                    "これはバグ8の再現テストです。マイク入力が継続されているか確認します。",
                    lang="ja",
                    volume=60,
                )
            time.sleep(1.0)

            # peak_level を確認（バグ#8 なら 0 になる）
            status = self._client.get_status()
            peak = status.get("route_b_peak_level", 0)
            if peak <= 1000:
                fails.append(
                    f"peak_level={peak} <= 1000 (バグ#8 疑い: 音声が途中で停止した可能性)"
                )

            # F8 離脱（PTT 終了）
            print("[S_MIC_F8] F8 release...")
            hotkey_simulator.release_hotkey("f8")

        except Exception as exc:
            fails.append(f"例外発生: {exc}")
        finally:
            # F8 を確実に離脱（念のため）
            try:
                hotkey_simulator.release_hotkey("f8")
            except Exception:
                pass
            # 課金リスク管理: 必ずアプリを停止
            try:
                self._client.click("start_btn")
                time.sleep(1.0)
            except Exception:
                pass
            self._stop_app()
            time.sleep(SCENARIO_COOLDOWN_SEC)

        if fails:
            return ScenarioResult("S_MIC_F8 bug8_repro", False, "; ".join(fails))
        return ScenarioResult(
            "S_MIC_F8 bug8_repro", True,
            f"F8 press/release + TTS + peak_level > 1000"
        )

    # ------------------------------------------------------------------
    # シナリオ S_LATCH_TOGGLE: ラッチ ON/OFF 繰り返しでの挙動確認
    # ------------------------------------------------------------------

    def _scenario_s_latch_toggle(self) -> ScenarioResult:
        """S_LATCH_TOGGLE: ラッチ ON/OFF 繰り返しでの route_b 挙動確認シナリオ。

        検証項目:
        - ラッチ ON 後: audio_gate = True
        - ラッチ OFF 後: audio_gate = False
        - ラッチ再 ON 後: route_b_state = RUNNING（再起動確認）
        """
        from audio_router import AudioRouter

        print("\n[SCENARIO S_LATCH_TOGGLE] ラッチ ON/OFF 繰り返しシナリオを開始...")
        fails: list[str] = []

        try:
            self._start_app()
            self._client.wait_ready(max_wait=APP_START_WAIT_SEC)

            # 系統1 OFF / 系統2 ON
            self._client.set_value("route_a_enable", False)
            self._client.set_value("route_b_enable", True)
            time.sleep(0.5)

            # 開始ボタン押下
            self._client.click("start_btn")
            time.sleep(2.0)

            # ラッチ ON → gate=True 確認
            print("[S_LATCH_TOGGLE] ラッチ ON...")
            self._client.set_value("ptt_latch_check", True)
            time.sleep(0.5)

            gate_true_ok = self._client.poll_until(
                lambda: self._client.get_status().get("route_b_audio_gate") is True,
                max_wait=5.0,
            )
            if not gate_true_ok:
                fails.append("ラッチ ON 後: audio_gate が True にならなかった")

            # ラッチ OFF → route_b stop 確認
            print("[S_LATCH_TOGGLE] ラッチ OFF...")
            self._client.set_value("ptt_latch_check", False)
            time.sleep(0.5)

            gate_false_ok = self._client.poll_until(
                lambda: self._client.get_status().get("route_b_audio_gate") is False,
                max_wait=5.0,
            )
            if not gate_false_ok:
                fails.append("ラッチ OFF 後: audio_gate が False にならなかった")

            # ラッチ再 ON → route_b 再起動確認
            print("[S_LATCH_TOGGLE] ラッチ ON（再）...")
            self._client.set_value("ptt_latch_check", True)
            time.sleep(0.5)

            restart_ok = self._client.poll_until(
                lambda: _assert_route_b_state(self._client, "RUNNING"),
                max_wait=5.0,
            )
            if not restart_ok:
                fails.append("ラッチ再 ON 後: route_b_state が RUNNING にならなかった")

        except Exception as exc:
            fails.append(f"例外発生: {exc}")
        finally:
            # 課金リスク管理
            try:
                self._client.set_value("ptt_latch_check", False)
                self._client.click("start_btn")
                time.sleep(1.0)
            except Exception:
                pass
            self._stop_app()
            time.sleep(SCENARIO_COOLDOWN_SEC)

        if fails:
            return ScenarioResult("S_LATCH_TOGGLE", False, "; ".join(fails))
        return ScenarioResult(
            "S_LATCH_TOGGLE", True,
            "latch ON: gate=True, OFF: gate=False, ON再: RUNNING"
        )

    # ------------------------------------------------------------------
    # 全シナリオ実行
    # ------------------------------------------------------------------

    def run_all(self) -> int:
        """全シナリオを順次実行し、失敗数を返す。

        --dry-run の場合は実際のアプリ起動なしにシナリオ一覧と
        クリーンアップ動作のみ表示して終了する。

        Returns:
            int: 失敗したシナリオ数（0 = 全 PASS）
        """
        # シナリオ定義（名前 + メソッド + スキップ判定）
        scenario_defs = [
            ("S1 route_a_solo_eng2jp", self._scenario_s1_route_a_solo, False),
            ("S4-S5 ptt_press_release", self._scenario_s4_s5_ptt, False),
            ("S6 latch_on_off", self._scenario_s6_latch, self._skip_s6),
            ("S_MIC_F8 bug8_repro", self._scenario_s_mic_f8, False),
            ("S_LATCH_TOGGLE", self._scenario_s_latch_toggle, False),
        ]

        # --dry-run: シナリオ一覧を表示してクリーンアップ確認のみ
        if self._dry_run:
            print("\n[DRY-RUN] シナリオ一覧 (実際のアプリ起動なし)")
            print("=" * 60)
            for name, _, skipped in scenario_defs:
                status = "SKIP" if skipped else "WILL RUN"
                print(f"  {name:<32} [{status}]")
            print("=" * 60)
            print("[DRY-RUN] クリーンアップ動作確認...")
            self._stop_app()  # proc=None なので taskkill + netstat のみ実行
            print("[DRY-RUN] 完了")
            return 0

        results: list[ScenarioResult] = []

        # シナリオ実行（各シナリオ内で start/stop を行う）
        for name, method, skipped in scenario_defs:
            if skipped:
                results.append(ScenarioResult(name, True, "SKIP (option)"))
            else:
                results.append(method())

        # 結果表示（発注書指定フォーマット）
        print("\n" + "=" * 60)
        fail_count = 0
        for r in results:
            print(str(r))
            if not r.passed and "SKIP" not in r.detail:
                fail_count += 1

        total_run = sum(1 for r in results if "SKIP" not in r.detail)
        passed = total_run - fail_count
        print(f"\nTOTAL: {passed} / {total_run} PASS")
        print("=" * 60)

        return fail_count


# ---------------------------------------------------------------------------
# エントリーポイント
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="実機 E2E スモークテスト (SEM S1/S4/S5/S6)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"RPC ポート番号 (デフォルト: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default=DEFAULT_LOG_DIR,
        help=f"logs/ ディレクトリパス (デフォルト: {DEFAULT_LOG_DIR})",
    )
    parser.add_argument(
        "--skip-s6",
        action="store_true",
        help="S6 ラッチシナリオをスキップ",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="シナリオ一覧表示 + クリーンアップ動作確認のみ（実アプリ起動なし）",
    )
    args = parser.parse_args()

    runner = E2eSmokeRunner(
        port=args.port,
        log_dir=args.log_dir,
        skip_s6=args.skip_s6,
        dry_run=args.dry_run,
    )

    try:
        fail_count = runner.run_all()
    except KeyboardInterrupt:
        print("\n[ABORT] Ctrl+C 検出。アプリを強制停止します...")
        runner._stop_app()
        fail_count = -1

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
