"""E2E テスト用 pytest フィクスチャ。"""
import sys
import subprocess
import pytest
from pathlib import Path

from tests.e2e.helpers import RpcClient

_PROJECT_ROOT = Path(__file__).parent.parent.parent
E2E_PORT = 8788


@pytest.fixture(scope="session")
def app_process():
    """アプリをサブプロセスで --test-mode 起動し、セッション全体で使い回す。"""
    proc = subprocess.Popen(
        [sys.executable, "app.py", "--test-mode", f"--rpc-port={E2E_PORT}"],
        cwd=str(_PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    client = RpcClient(port=E2E_PORT)
    try:
        client.wait_ready(max_wait=30.0)
    except TimeoutError:
        proc.terminate()
        out = proc.stdout.read().decode(errors="replace")
        pytest.fail(f"App did not start in time.\nOutput:\n{out}")
    yield proc, client
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture
def client(app_process) -> RpcClient:
    _, rpc_client = app_process
    return rpc_client
