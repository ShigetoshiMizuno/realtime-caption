"""
tests/test_idle_disconnect_monitor.py

IdleDisconnectMonitor の単体テスト。
PR1 スコープ: cost_monitor.py 内の新規クラスのみを対象とする。
将来 PR2 で CaptionSystem._lazy_initialize から使用予定。
"""

from __future__ import annotations

import threading
import time

import pytest

try:
    from cost_monitor import IdleDisconnectMonitor

    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False
    IdleDisconnectMonitor = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# インポートテスト
# ---------------------------------------------------------------------------


class TestImport:
    """IdleDisconnectMonitor がインポートできること。"""

    def test_class_exists(self):
        """cost_monitor から IdleDisconnectMonitor をインポートできること。"""
        try:
            from cost_monitor import IdleDisconnectMonitor as Cls

            assert Cls is not None
        except ImportError as e:
            pytest.fail(f"IdleDisconnectMonitor のインポートに失敗: {e}")


# ---------------------------------------------------------------------------
# 初期化テスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="IdleDisconnectMonitor 未実装")
class TestInit:
    """コンストラクタのデフォルト値と型を確認する。"""

    def test_default_idle_timeout_sec(self):
        """idle_timeout_sec のデフォルトが 300.0 秒であること。"""
        import inspect

        sig = inspect.signature(IdleDisconnectMonitor.__init__)
        default = sig.parameters["idle_timeout_sec"].default
        assert default == 300.0, f"デフォルト idle_timeout_sec が 300.0 でない: {default}"

    def test_default_audio_threshold(self):
        """audio_threshold のデフォルトが 200 であること（TBD-4-2 実機計測で 100 は厳しすぎると判明）。"""
        import inspect

        sig = inspect.signature(IdleDisconnectMonitor.__init__)
        default = sig.parameters["audio_threshold"].default
        assert default == 200, f"デフォルト audio_threshold が 200 でない: {default}"

    def test_default_on_idle_timeout_is_none(self):
        """on_idle_timeout のデフォルトが None であること。"""
        import inspect

        sig = inspect.signature(IdleDisconnectMonitor.__init__)
        default = sig.parameters["on_idle_timeout"].default
        assert default is None, f"デフォルト on_idle_timeout が None でない: {default}"

    def test_public_methods_exist(self):
        """公開メソッドが全て存在すること。"""
        for method in (
            "start",
            "stop",
            "report_audio_level",
            "set_disconnected",
            "reset_idle_timer",
            "is_idle",
            "is_disconnected",
        ):
            assert hasattr(IdleDisconnectMonitor, method), f"{method} メソッドが存在しない"

    def test_initial_state_not_idle(self):
        """インスタンス生成直後は is_idle() == False であること。"""
        monitor = IdleDisconnectMonitor(timeout_override=0.1)
        assert monitor.is_idle() is False

    def test_initial_state_not_disconnected(self):
        """インスタンス生成直後は is_disconnected() == False であること。"""
        monitor = IdleDisconnectMonitor(timeout_override=0.1)
        assert monitor.is_disconnected() is False


# ---------------------------------------------------------------------------
# report_audio_level テスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="IdleDisconnectMonitor 未実装")
class TestReportAudioLevel:
    """report_audio_level() によるタイマーリセット動作を確認する。"""

    def test_above_threshold_resets_last_audio_time(self):
        """threshold 以上の peak を渡すと _last_audio_time が更新されること。"""
        monitor = IdleDisconnectMonitor(audio_threshold=100, timeout_override=0.5)
        monitor.start()
        before = monitor._last_audio_time
        time.sleep(0.05)
        monitor.report_audio_level(peak=150)
        after = monitor._last_audio_time
        monitor.stop()
        assert after > before, "_last_audio_time が更新されなかった"

    def test_below_threshold_does_not_update_last_audio_time(self):
        """threshold 未満の peak を渡しても _last_audio_time が更新されないこと。"""
        monitor = IdleDisconnectMonitor(audio_threshold=100, timeout_override=0.5)
        monitor.start()
        monitor.report_audio_level(peak=150)  # まず threshold 超えで更新
        time.sleep(0.05)
        before = monitor._last_audio_time
        monitor.report_audio_level(peak=50)  # threshold 未満
        after = monitor._last_audio_time
        monitor.stop()
        assert after == before, "_last_audio_time が不正に更新された"

    def test_exactly_at_threshold_resets_timer(self):
        """peak == threshold のとき _last_audio_time が更新されること（以上 = boundary included）。"""
        monitor = IdleDisconnectMonitor(audio_threshold=100, timeout_override=0.5)
        monitor.start()
        before = monitor._last_audio_time
        time.sleep(0.05)
        monitor.report_audio_level(peak=100)
        after = monitor._last_audio_time
        monitor.stop()
        assert after > before, "peak == threshold のとき _last_audio_time が更新されなかった"


# ---------------------------------------------------------------------------
# アイドルタイムアウト発火テスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="IdleDisconnectMonitor 未実装")
class TestIdleTimeout:
    """アイドルタイムアウト到達時のコールバック発火を確認する。"""

    def test_idle_timeout_fires_callback(self):
        """timeout_override=0.3 秒後に on_idle_timeout が呼ばれること。"""
        fired = threading.Event()
        monitor = IdleDisconnectMonitor(
            audio_threshold=100,
            on_idle_timeout=lambda: fired.set(),
            timeout_override=0.3,
        )
        monitor.start()
        result = fired.wait(timeout=3.0)
        monitor.stop()
        assert result, "on_idle_timeout が時間内に呼ばれなかった"

    def test_idle_timeout_fires_only_once(self):
        """on_idle_timeout は 1 回だけ呼ばれること（idempotent）。"""
        call_count = [0]

        def on_timeout():
            call_count[0] += 1

        monitor = IdleDisconnectMonitor(
            audio_threshold=100,
            on_idle_timeout=on_timeout,
            timeout_override=0.2,
        )
        monitor.start()
        # タイムアウトの 3 倍以上待っても 1 回だけ
        time.sleep(1.0)
        monitor.stop()
        assert call_count[0] == 1, f"on_idle_timeout が {call_count[0]} 回呼ばれた（期待: 1 回）"

    def test_idle_timeout_not_fired_when_audio_active(self):
        """音声が継続している間はタイムアウトコールバックが呼ばれないこと。"""
        fired = threading.Event()
        monitor = IdleDisconnectMonitor(
            audio_threshold=100,
            on_idle_timeout=lambda: fired.set(),
            timeout_override=0.4,
        )
        monitor.start()
        # 0.4 秒の timeout_override の間、継続的に音声を報告する
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            monitor.report_audio_level(peak=200)
            time.sleep(0.1)
        monitor.stop()
        assert not fired.is_set(), "音声アクティブなのにタイムアウトが発火した"

    def test_no_callback_when_on_idle_timeout_is_none(self):
        """on_idle_timeout=None のときエラーが発生しないこと。"""
        monitor = IdleDisconnectMonitor(
            audio_threshold=100,
            timeout_override=0.2,
        )
        monitor.start()
        time.sleep(0.8)
        monitor.stop()  # 例外なし


# ---------------------------------------------------------------------------
# set_disconnected テスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="IdleDisconnectMonitor 未実装")
class TestSetDisconnected:
    """set_disconnected() によるフラグ管理を確認する。"""

    def test_set_disconnected_true(self):
        """set_disconnected(True) 後は is_disconnected() == True になること。"""
        monitor = IdleDisconnectMonitor(timeout_override=0.5)
        monitor.set_disconnected(True)
        assert monitor.is_disconnected() is True

    def test_set_disconnected_false(self):
        """set_disconnected(False) 後は is_disconnected() == False になること。"""
        monitor = IdleDisconnectMonitor(timeout_override=0.5)
        monitor.set_disconnected(True)
        monitor.set_disconnected(False)
        assert monitor.is_disconnected() is False

    def test_no_refiring_after_disconnected(self):
        """set_disconnected(True) 後はタイムアウト後も on_idle_timeout が再発火しないこと。"""
        call_count = [0]

        def on_timeout():
            call_count[0] += 1

        monitor = IdleDisconnectMonitor(
            audio_threshold=100,
            on_idle_timeout=on_timeout,
            timeout_override=0.2,
        )
        monitor.start()
        # 最初のタイムアウトを待つ
        time.sleep(0.8)
        first_count = call_count[0]
        # 切断フラグをセット（タイムアウト後の想定）
        monitor.set_disconnected(True)
        # さらに待って再発火しないことを確認
        time.sleep(0.8)
        monitor.stop()
        assert first_count == 1, f"最初のタイムアウト発火が 1 回でない: {first_count}"
        assert call_count[0] == 1, (
            f"set_disconnected(True) 後も on_idle_timeout が呼ばれた: {call_count[0]}"
        )


# ---------------------------------------------------------------------------
# reset_idle_timer テスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="IdleDisconnectMonitor 未実装")
class TestResetIdleTimer:
    """reset_idle_timer() によるタイマーリセットを確認する。"""

    def test_reset_idle_timer_updates_last_audio_time(self):
        """reset_idle_timer() で _last_audio_time が現在時刻に更新されること。"""
        monitor = IdleDisconnectMonitor(timeout_override=0.5)
        monitor.start()
        time.sleep(0.1)
        before = monitor._last_audio_time
        time.sleep(0.05)
        monitor.reset_idle_timer()
        after = monitor._last_audio_time
        monitor.stop()
        assert after > before, "reset_idle_timer() 後に _last_audio_time が更新されなかった"

    def test_reset_idle_timer_prevents_timeout(self):
        """reset_idle_timer() を繰り返すとタイムアウトが発火しないこと。"""
        fired = threading.Event()
        monitor = IdleDisconnectMonitor(
            audio_threshold=100,
            on_idle_timeout=lambda: fired.set(),
            timeout_override=0.3,
        )
        monitor.start()
        # 0.3 秒の timeout を上回るまでリセットし続ける
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            monitor.reset_idle_timer()
            time.sleep(0.1)
        monitor.stop()
        assert not fired.is_set(), "reset 中にタイムアウトが発火した"


# ---------------------------------------------------------------------------
# stop() テスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="IdleDisconnectMonitor 未実装")
class TestStop:
    """stop() によるスレッド終了を確認する。"""

    def test_stop_terminates_thread(self):
        """stop() 後にウォッチャースレッドが終了すること。"""
        monitor = IdleDisconnectMonitor(timeout_override=10.0)
        monitor.start()
        assert monitor._thread is not None and monitor._thread.is_alive()
        monitor.stop()
        # スレッド終了を最大 2 秒待つ
        monitor._thread.join(timeout=2.0)
        assert not monitor._thread.is_alive(), "stop() 後もスレッドが動いている"

    def test_stop_is_idempotent(self):
        """stop() を複数回呼んでも例外が発生しないこと。"""
        monitor = IdleDisconnectMonitor(timeout_override=10.0)
        monitor.start()
        monitor.stop()
        monitor.stop()
        monitor.stop()

    def test_stop_without_start_is_safe(self):
        """start() なしで stop() を呼んでも例外が発生しないこと。"""
        monitor = IdleDisconnectMonitor()
        monitor.stop()

    def test_no_callback_after_stop(self):
        """stop() 後にタイムアウトコールバックが呼ばれないこと。"""
        fired = threading.Event()
        monitor = IdleDisconnectMonitor(
            audio_threshold=100,
            on_idle_timeout=lambda: fired.set(),
            timeout_override=0.3,
        )
        monitor.start()
        # タイムアウトより前に stop する
        time.sleep(0.05)
        monitor.stop()
        # タイムアウト時間以上待つ
        time.sleep(0.5)
        assert not fired.is_set(), "stop() 後にタイムアウトが発火した"


# ---------------------------------------------------------------------------
# スレッドセーフテスト
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="IdleDisconnectMonitor 未実装")
class TestThreadSafety:
    """並行呼び出しで例外が発生しないことを確認する。"""

    def test_concurrent_report_audio_level(self):
        """複数スレッドから report_audio_level() を同時に呼んでも例外が発生しないこと。"""
        monitor = IdleDisconnectMonitor(audio_threshold=100, timeout_override=5.0)
        monitor.start()
        errors = []

        def worker(peak_value: int) -> None:
            try:
                for _ in range(50):
                    monitor.report_audio_level(peak=peak_value)
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(v,)) for v in (50, 150, 200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        monitor.stop()
        assert errors == [], f"並行呼び出し中に例外: {errors}"

    def test_concurrent_set_disconnected(self):
        """複数スレッドから set_disconnected() を同時に呼んでも例外が発生しないこと。"""
        monitor = IdleDisconnectMonitor(timeout_override=5.0)
        monitor.start()
        errors = []

        def worker(flag: bool) -> None:
            try:
                for _ in range(50):
                    monitor.set_disconnected(flag)
                    time.sleep(0.001)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(True,)),
            threading.Thread(target=worker, args=(False,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        monitor.stop()
        assert errors == [], f"並行 set_disconnected 中に例外: {errors}"
