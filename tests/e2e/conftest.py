"""E2E テスト用 pytest フィクスチャ。"""
import sys
import subprocess
import tempfile
import pytest
from pathlib import Path

from tests.e2e.helpers import RpcClient

_PROJECT_ROOT = Path(__file__).parent.parent.parent
E2E_PORT = 8788


@pytest.fixture(scope="session")
def app_process():
    """アプリをサブプロセスで --test-mode 起動し、セッション全体で使い回す。"""
    # stdout=PIPE はバッファが満杯になるとアプリ自体がブロックするため、ファイルに逃がす
    log_file = tempfile.NamedTemporaryFile(
        prefix="e2e_app_", suffix=".log", delete=False, mode="w", encoding="utf-8", errors="replace"
    )
    proc = subprocess.Popen(
        [sys.executable, "app.py", "--test-mode", f"--rpc-port={E2E_PORT}"],
        cwd=str(_PROJECT_ROOT),
        stdout=log_file,
        stderr=log_file,
    )
    client = RpcClient(port=E2E_PORT)
    try:
        client.wait_ready(max_wait=30.0)
    except TimeoutError:
        proc.terminate()
        proc.wait(timeout=5)
        log_file.flush()
        log_path = log_file.name
        log_file.close()
        try:
            out = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            out = "(ログ読み取り失敗)"
        pytest.fail(f"App did not start in time.\nLog: {log_path}\n{out[-3000:]}")
    yield proc, client
    proc.terminate()
    proc.wait(timeout=10)
    log_file.close()


@pytest.fixture
def client(app_process) -> RpcClient:
    _, rpc_client = app_process
    return rpc_client
