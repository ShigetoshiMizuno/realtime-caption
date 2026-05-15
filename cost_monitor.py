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


class IdleDisconnectMonitor:
    """音声入力レベルを監視し、長時間アイドル時にコールバックを発火する。

    capture スレッドから 1 秒ごとに report_audio_level(peak) を呼ぶことで、
    peak が audio_threshold 未満の状態が idle_timeout_sec 秒以上継続すると
    on_idle_timeout コールバックを一度だけ発火する。

    将来 PR2 で CaptionSystem._lazy_initialize から使用予定。

    使用例:
        monitor = IdleDisconnectMonitor(
            idle_timeout_sec=300.0,
            audio_threshold=100,
            on_idle_timeout=lambda: print("idle"),
        )
        monitor.start()
        # capture スレッドから 1 秒ごとに呼ぶ
        monitor.report_audio_level(peak=150)
        # 切断後はフラグを立てる
        monitor.set_disconnected(True)
        # 再接続完了したらタイマーリセット
        monitor.reset_idle_timer()
        # 終了時
        monitor.stop()

    Args:
        idle_timeout_sec: アイドル判定タイムアウト（秒）。デフォルト 300.0。
        audio_threshold: 無音とみなす音量上限（int16 絶対値 max）。デフォルト 100。
        on_idle_timeout: アイドルタイムアウト時のコールバック。一度だけ呼ばれる。
        timeout_override: テスト用 DI。指定時はこの値をタイムアウトとして使用する。
    """

    def __init__(
        self,
        idle_timeout_sec: float = 300.0,
        audio_threshold: int = 100,
        on_idle_timeout: Callable[[], None] | None = None,
        *,
        timeout_override: float | None = None,
    ) -> None:
        self._idle_timeout_sec = idle_timeout_sec
        self._audio_threshold = audio_threshold
        self._on_idle_timeout = on_idle_timeout
        self._effective_timeout = timeout_override if timeout_override is not None else idle_timeout_sec
        # テスト用 DI: timeout_override が指定された場合はポーリング間隔も短くする
        self._poll_interval = (
            min(timeout_override / 5.0, _POLL_INTERVAL_SEC)
            if timeout_override is not None
            else _POLL_INTERVAL_SEC
        )

        self._lock = threading.Lock()
        self._last_audio_time: float = time.monotonic()
        self._disconnected: bool = False
        self._timeout_fired: bool = False

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """監視を開始する。ウォッチャースレッドを起動する。"""
        self._stop_event.clear()
        with self._lock:
            self._last_audio_time = time.monotonic()
            self._disconnected = False
            self._timeout_fired = False
        self._thread = threading.Thread(
            target=self._watcher_loop, daemon=True, name="IdleDisconnectMonitor-watcher"
        )
        self._thread.start()

    def stop(self) -> None:
        """監視を停止する。stop() は何度呼んでも安全。"""
        self._stop_event.set()

    def report_audio_level(self, peak: int) -> None:
        """capture スレッドから 1 秒ごとに呼ぶ。

        peak が audio_threshold 以上のとき _last_audio_time を更新する。
        """
        if peak >= self._audio_threshold:
            with self._lock:
                self._last_audio_time = time.monotonic()

    def set_disconnected(self, disconnected: bool) -> None:
        """アイドル切断状態フラグをセットする。

        True にすると _watcher_loop でのタイムアウト発火が停止する。
        """
        with self._lock:
            self._disconnected = disconnected

    def reset_idle_timer(self) -> None:
        """アイドルタイマーをリセットする（再接続完了時に呼ぶ）。"""
        with self._lock:
            self._last_audio_time = time.monotonic()
            self._timeout_fired = False

    def is_idle(self) -> bool:
        """現在アイドル状態（タイムアウト発火済み）かどうかを返す。"""
        with self._lock:
            return self._timeout_fired

    def is_disconnected(self) -> bool:
        """現在切断フラグが立っているかどうかを返す。"""
        with self._lock:
            return self._disconnected

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _watcher_loop(self) -> None:
        """ポーリング間隔ごとに経過時間をチェックし、タイムアウトコールバックを発火する。"""
        while not self._stop_event.is_set():
            self._check_idle_timeout()
            self._stop_event.wait(timeout=self._poll_interval)

    def _check_idle_timeout(self) -> None:
        """アイドルタイムアウトに達した場合に on_idle_timeout を一度だけ呼ぶ。"""
        with self._lock:
            if self._timeout_fired:
                return
            if self._disconnected:
                return
            elapsed = time.monotonic() - self._last_audio_time
            if elapsed >= self._effective_timeout:
                self._timeout_fired = True
                should_fire = True
            else:
                should_fire = False

        if should_fire and self._on_idle_timeout is not None:
            self._on_idle_timeout()
