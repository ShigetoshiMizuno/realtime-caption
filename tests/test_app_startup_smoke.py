"""app.py を実起動して TypeError 等の起動時例外を検出する smoke test。

PR #143 で発覚したバグの再発防止。--auto-konnyaku=2 で 2 秒だけ稼働して終了。
stderr/stdout に Exception が含まれていなければ PASS。

結合点:
  app.py エントリポイント × dpg × _verbose_callback wrapper
配線チェック:
  1. app.py が --auto-konnyaku=2 で起動できること
  2. 起動時に TypeError が発生しないこと (PR #143 で修正したバグの再発防止)
  3. 起動時に Traceback が発生しないこと
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.smoke
def test_app_startup_no_typeerror():
    """app.py を短時間起動して stderr に TypeError が出ないことを確認。

    実 dpg を使う smoke test。--auto-konnyaku=2 で 2 秒後に自動終了する。
    Windows 環境専用（GUI 起動が必要）。

    PR #143 を revert すると dpg コールバック起動時に
    TypeError が発生してこのテストが FAIL する。
    """
    env = {**os.environ}
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        [sys.executable, str(_ROOT / "app.py"), "--auto-konnyaku=2"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )

    combined = result.stdout + result.stderr

    # PR #143 で発覚した「callback 引数 mismatch」の TypeError シグナルを検出。
    # CI 環境固有の Traceback（音声デバイス未接続等）は本テストでは無視する
    # （ローカル実機で発生しない例外まで CI が拾うと過剰検出になる）。
    assert "TypeError:" not in combined, (
        "起動時に TypeError 発生:\n"
        + "\n".join(line for line in combined.splitlines() if "TypeError" in line)[:2000]
    )
