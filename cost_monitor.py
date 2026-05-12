"""
cost_monitor.py

経過時間・想定コストの計算と、最大稼働時間・警告閾値の監視を行うモジュール。
threading のみで動作し、asyncio に依存しない。
"""

from __future__ import annotations

import threading
import time
from typing import Callable

RATE_USD_PER_MINUTE: float = 0.034
WARNING_THRESHOLDS_USD: tuple[float, ...] = (5.0, 10.0, 20.0)

_POLL_INTERVAL_SEC: float = 1.0


class CostMonitor:
    """経過時間・コスト計算と最大稼働時間・警告閾値の監視クラス。

    Args:
        max_session_minutes: 最大稼働時間（分）。0 で無制限。デフォルト 60。
        on_max_reached: 最大稼働時間に達したときのコールバック。一度だけ呼ばれる。
        on_warning: 警告閾値を超えたときのコールバック。threshold (USD) を引数に取る。
        _warning_thresholds: テスト用オーバーライド。省略時は WARNING_THRESHOLDS_USD を使用。
    """

    def __init__(
        self,
        max_session_minutes: int = 60,
        on_max_reached: Callable[[], None] | None = None,
        on_warning: Callable[[float], None] | None = None,
        _warning_thresholds: tuple[float, ...] | None = None,
    ) -> None:
        self._max_session_minutes = max_session_minutes
        self._on_max_reached = on_max_reached
        self._on_warning = on_warning
        self._thresholds = (
            _warning_thresholds
            if _warning_thresholds is not None
            else WARNING_THRESHOLDS_USD
        )

        self._start_time: float | None = None
        self._stop_time: float | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 発火済み管理フラグ（idempotent 保証）
        self._max_reached_fired = False
        self._fired_thresholds: set[float] = set()

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """監視を開始する。起動時刻を記録し、ウォッチャースレッドを起動する。"""
        self._start_time = time.monotonic()
        self._stop_time = None
        self._stop_event.clear()
        self._max_reached_fired = False
        self._fired_thresholds = set()

        self._thread = threading.Thread(
            target=self._watcher_loop, daemon=True, name="CostMonitor-watcher"
        )
        self._thread.start()

    def stop(self) -> None:
        """監視を停止する。stop() は何度呼んでも安全。"""
        if self._start_time is not None and self._stop_time is None:
            self._stop_time = time.monotonic()
        self._stop_event.set()

    def elapsed_minutes(self) -> float:
        """起動からの経過時間（分）を返す。stop() 後は停止時点の値を返す。"""
        if self._start_time is None:
            return 0.0
        if self._stop_time is not None:
            elapsed_sec = self._stop_time - self._start_time
        else:
            elapsed_sec = time.monotonic() - self._start_time
        return elapsed_sec / 60.0

    def estimated_cost_usd(self) -> float:
        """起動からの想定コスト（USD）を返す。"""
        return self.elapsed_minutes() * RATE_USD_PER_MINUTE

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _watcher_loop(self) -> None:
        """1 秒ごとに経過時間をチェックし、各種コールバックを発火する。"""
        while not self._stop_event.is_set():
            self._check_max_session()
            self._check_warning_thresholds()
            self._stop_event.wait(timeout=_POLL_INTERVAL_SEC)

    def _check_max_session(self) -> None:
        """最大稼働時間に達した場合に on_max_reached を一度だけ呼ぶ。"""
        if self._max_reached_fired:
            return
        if self._max_session_minutes == 0:
            return
        if self.elapsed_minutes() >= self._max_session_minutes:
            self._max_reached_fired = True
            if self._on_max_reached is not None:
                self._on_max_reached()

    def _check_warning_thresholds(self) -> None:
        """コストが警告閾値を超えた場合に on_warning を一度だけ呼ぶ。"""
        if self._on_warning is None:
            return
        cost = self.estimated_cost_usd()
        for threshold in sorted(self._thresholds):
            if threshold not in self._fired_thresholds and cost >= threshold:
                self._fired_thresholds.add(threshold)
                self._on_warning(threshold)
