"""E2E テスト用 RPC クライアントヘルパー。"""
import time
import requests


class RpcClient:
    def __init__(self, port: int = 8788, timeout: float = 5.0):
        self.base = f"http://localhost:{port}"
        self.timeout = timeout

    def get_status(self) -> dict:
        return requests.get(f"{self.base}/api/status", timeout=self.timeout).json()

    def wait_ready(self, max_wait: float = 30.0) -> None:
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            try:
                r = requests.get(f"{self.base}/api/status", timeout=2.0)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"App did not become ready within {max_wait}s")

    def get_value(self, tag: str) -> dict:
        return requests.get(f"{self.base}/api/ui/get_value", params={"tag": tag}, timeout=self.timeout).json()

    def get_config(self, tag: str, field: str = "") -> dict:
        params = {"tag": tag}
        if field:
            params["field"] = field
        return requests.get(f"{self.base}/api/ui/get_config", params=params, timeout=self.timeout).json()

    def exists(self, tag: str) -> bool:
        return requests.get(f"{self.base}/api/ui/exists", params={"tag": tag}, timeout=self.timeout).json().get("exists", False)

    def tags(self) -> dict:
        return requests.get(f"{self.base}/api/ui/tags", timeout=self.timeout).json()

    def press(self, tag: str) -> dict:
        return requests.post(f"{self.base}/api/ui/press", json={"tag": tag}, timeout=self.timeout).json()

    def release(self, tag: str) -> dict:
        return requests.post(f"{self.base}/api/ui/release", json={"tag": tag}, timeout=self.timeout).json()

    def click(self, tag: str) -> dict:
        return requests.post(f"{self.base}/api/ui/click", json={"tag": tag}, timeout=self.timeout).json()

    def set_value(self, tag: str, value) -> dict:
        return requests.post(f"{self.base}/api/ui/set_value", json={"tag": tag, "value": value}, timeout=self.timeout).json()

    def poll_until(self, condition_fn, interval: float = 0.2, max_wait: float = 5.0) -> bool:
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            try:
                if condition_fn():
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False
