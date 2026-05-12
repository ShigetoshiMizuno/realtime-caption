"""
tests/test_cost_monitor.py

CostMonitor の単体テスト。
threading のみで動作する時間計測・コスト計算・警告閾値クラスを検証する。
"""

import threading
import time

import pytest

try:
    from cost_monitor import CostMonitor, RATE_USD_PER_MINUTE, WARNING_THRESHOLDS_USD
    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False
    CostMonitor = None
    RATE_USD_PER_MINUTE = None
    WARNING_THRESHOLDS_USD = None


# ---------------------------------------------------------------------------
# モジュール定数テスト
# ---------------------------------------------------------------------------

class TestModuleConstants:
    """モジュールレベル定数の確認。"""

    def test_module_can_be_imported(self):
        """cost_monitor モジュールがインポートできること。"""
        try:
            import cost_monitor
        except ImportError as e:
            pytest.fail(f"cost_monitor モジュールのインポートに失敗: {e}")

    def test_rate_constant_exists(self):
        """RATE_USD_PER_MINUTE が定義されていること。"""
        try:
            from cost_monitor import RATE_USD_PER_MINUTE as rate
            assert rate == pytest.approx(0.034)
        except ImportError as e:
            pytest.fail(f"RATE_USD_PER_MINUTE が見つからない: {e}")

    def test_warning_thresholds_constant_exists(self):
        """WARNING_THRESHOLDS_USD が定義されていること。"""
        try:
            from cost_monitor import WARNING_THRESHOLDS_USD as thresholds
            assert thresholds == (5.0, 10.0, 20.0)
        except ImportError as e:
            pytest.fail(f"WARNING_THRESHOLDS_USD が見つからない: {e}")

    def test_cost_monitor_class_exists(self):
        """CostMonitor クラスが存在すること。"""
        try:
            from cost_monitor import CostMonitor
            assert CostMonitor is not None
        except ImportError as e:
            pytest.fail(f"CostMonitor クラスが存在しない: {e}")


# ---------------------------------------------------------------------------
# 基本動作テスト
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="cost_monitor モジュール未実装")
class TestCostMonitorBasic:
    """CostMonitor の基本機能テスト。"""

    def test_elapsed_minutes_before_start_is_zero(self):
        """start() 前は elapsed_minutes() が 0 を返すこと。"""
        monitor = CostMonitor()
        assert monitor.elapsed_minutes() == pytest.approx(0.0)

    def test_elapsed_minutes_after_start(self):
        """start() 後 0.5 秒経過すると elapsed_minutes がおよそ 0.5/60 分になること。"""
        monitor = CostMonitor()
        monitor.start()
        time.sleep(0.5)
        monitor.stop()
        elapsed = monitor.elapsed_minutes()
        # 0.5秒 ≒ 0.00833分。余裕を持って 0.005〜0.02 の範囲で確認
        assert 0.005 < elapsed < 0.02, f"elapsed_minutes が想定外: {elapsed}"

    def test_elapsed_minutes_stops_after_stop(self):
        """stop() 後は elapsed_minutes が増加しなくなること。"""
        monitor = CostMonitor()
        monitor.start()
        time.sleep(0.2)
        monitor.stop()
        elapsed_at_stop = monitor.elapsed_minutes()
        time.sleep(0.3)
        elapsed_after_stop = monitor.elapsed_minutes()
        # stop 後は増えない（0.001分=0.06秒の誤差は許容）
        assert abs(elapsed_after_stop - elapsed_at_stop) < 0.001, (
            f"stop後も elapsed が増加した: {elapsed_at_stop} -> {elapsed_after_stop}"
        )

    def test_estimated_cost_usd(self):
        """estimated_cost_usd が elapsed_minutes × 0.034 になること。"""
        monitor = CostMonitor()
        monitor.start()
        time.sleep(0.3)
        monitor.stop()
        elapsed = monitor.elapsed_minutes()
        cost = monitor.estimated_cost_usd()
        expected = elapsed * 0.034
        assert cost == pytest.approx(expected, rel=0.01), (
            f"コスト計算が不一致: {cost} vs {expected}"
        )

    def test_stop_is_idempotent(self):
        """stop() を複数回呼んでも例外が発生しないこと。"""
        monitor = CostMonitor()
        monitor.start()
        time.sleep(0.1)
        monitor.stop()
        monitor.stop()  # 2回目は無視されること
        monitor.stop()  # 3回目も無視されること

    def test_stop_without_start_is_safe(self):
        """start() なしで stop() を呼んでも例外が発生しないこと。"""
        monitor = CostMonitor()
        monitor.stop()  # start 前でも安全


# ---------------------------------------------------------------------------
# 最大稼働時間テスト
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="cost_monitor モジュール未実装")
class TestCostMonitorMaxSession:
    """最大稼働時間（max_session_minutes）のテスト。"""

    def test_max_zero_never_triggers_on_max_reached(self):
        """max_session_minutes=0 のとき on_max_reached が呼ばれないこと。"""
        called = []
        monitor = CostMonitor(
            max_session_minutes=0,
            on_max_reached=lambda: called.append(True),
        )
        monitor.start()
        time.sleep(0.3)
        monitor.stop()
        assert called == [], f"max=0 なのに on_max_reached が呼ばれた: {called}"

    def test_max_reached_callback_fires(self):
        """max_session_minutes=0.01（0.6秒）で on_max_reached が呼ばれること。"""
        called = threading.Event()
        monitor = CostMonitor(
            max_session_minutes=0.01,  # 0.6秒
            on_max_reached=lambda: called.set(),
        )
        monitor.start()
        # 最大2秒待つ（余裕を持たせる）
        fired = called.wait(timeout=2.0)
        monitor.stop()
        assert fired, "on_max_reached が時間内に呼ばれなかった"

    def test_max_reached_callback_fires_once(self):
        """on_max_reached は1回だけ呼ばれること。"""
        call_count = [0]
        monitor = CostMonitor(
            max_session_minutes=0.01,
            on_max_reached=lambda: call_count.__setitem__(0, call_count[0] + 1),
        )
        monitor.start()
        time.sleep(1.5)  # 閾値超過後も少し待つ
        monitor.stop()
        assert call_count[0] == 1, f"on_max_reached が {call_count[0]} 回呼ばれた（期待: 1回）"

    def test_no_callback_when_no_on_max_reached(self):
        """on_max_reached=None のときエラーが発生しないこと。"""
        monitor = CostMonitor(max_session_minutes=0.005)  # 0.3秒
        monitor.start()
        time.sleep(0.5)
        monitor.stop()  # 例外なし


# ---------------------------------------------------------------------------
# 警告閾値テスト（小さい閾値で高速テスト）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="cost_monitor モジュール未実装")
class TestCostMonitorWarnings:
    """警告閾値（on_warning）のテスト。

    テスト高速化のため、閾値を $0.001/$0.002/$0.003 と極小にして
    elapsed_minutes で短時間に到達できるよう、内部的に経過時間を制御する。

    実際には cost_monitor は start() 時刻からの経過で計算するため、
    小さな max_session_minutes を使うのと同様に、
    ここでは実時間の sleep + 小閾値で代替する。

    rate = 0.034 USD/min → $0.001 に達するには 0.001/0.034 ≈ 0.0294分 ≈ 1.76秒
    テストタイムアウトを考慮し、閾値を $0.001/$0.002/$0.003 に設定。
    sleep 2秒で $0.001 を超えることを確認する。
    """

    def test_warning_fires_when_threshold_exceeded(self):
        """コストが閾値($0.001)を超えたとき on_warning が呼ばれること。

        0.034 USD/min × t_min = 0.001 USD → t_min ≈ 0.0294 → t_sec ≈ 1.76秒
        2秒待てば $0.001 を超えるはず。
        """
        warnings = []
        monitor = CostMonitor(
            on_warning=lambda threshold: warnings.append(threshold),
            _warning_thresholds=(0.001, 0.002, 0.003),
        )
        monitor.start()
        # $0.001 に達するまで待つ（最大3秒）
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and len(warnings) == 0:
            time.sleep(0.1)
        monitor.stop()
        assert len(warnings) >= 1, "on_warning が呼ばれなかった"
        assert warnings[0] == pytest.approx(0.001), (
            f"警告閾値が不一致: {warnings[0]}"
        )

    def test_warning_fires_for_each_threshold_once(self):
        """複数閾値($0.001/$0.002/$0.003)それぞれが一度だけ発火すること。

        rate = 0.034 USD/min → $0.003 に達するには ≈5.3秒。
        タイムアウトを10秒に設定する。
        """
        warnings = []
        done_event = threading.Event()

        def on_warn(threshold):
            warnings.append(threshold)
            if threshold == 0.003:
                done_event.set()

        monitor = CostMonitor(
            on_warning=on_warn,
            _warning_thresholds=(0.001, 0.002, 0.003),
        )
        monitor.start()
        fired = done_event.wait(timeout=10.0)
        monitor.stop()

        assert fired, f"$0.003 閾値に到達しなかった（発火: {warnings}）"
        assert warnings == pytest.approx([0.001, 0.002, 0.003]), (
            f"警告閾値の発火順序が不一致: {warnings}"
        )

    def test_warning_fires_only_once_per_threshold(self):
        """同じ閾値は2回以上発火しないこと。"""
        warnings = []
        monitor = CostMonitor(
            on_warning=lambda t: warnings.append(t),
            _warning_thresholds=(0.001,),
        )
        monitor.start()
        # $0.001 を超えた後、さらに待つ
        time.sleep(3.0)
        monitor.stop()

        count_001 = warnings.count(0.001)
        assert count_001 == 1, f"$0.001 閾値が {count_001} 回発火した（期待: 1回）"

    def test_no_warning_callback_is_safe(self):
        """on_warning=None のときエラーが発生しないこと。"""
        monitor = CostMonitor(
            _warning_thresholds=(0.001,),
        )
        monitor.start()
        time.sleep(2.5)
        monitor.stop()


# ---------------------------------------------------------------------------
# コンストラクタシグネチャテスト
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="cost_monitor モジュール未実装")
class TestCostMonitorSignature:
    """コンストラクタのシグネチャ検証。"""

    def test_constructor_accepts_max_session_minutes(self):
        """max_session_minutes パラメータを受け付けること。"""
        import inspect
        sig = inspect.signature(CostMonitor.__init__)
        assert "max_session_minutes" in sig.parameters, (
            "コンストラクタに max_session_minutes がない"
        )

    def test_constructor_accepts_on_max_reached(self):
        """on_max_reached パラメータを受け付けること。"""
        import inspect
        sig = inspect.signature(CostMonitor.__init__)
        assert "on_max_reached" in sig.parameters, (
            "コンストラクタに on_max_reached がない"
        )

    def test_constructor_accepts_on_warning(self):
        """on_warning パラメータを受け付けること。"""
        import inspect
        sig = inspect.signature(CostMonitor.__init__)
        assert "on_warning" in sig.parameters, (
            "コンストラクタに on_warning がない"
        )

    def test_public_methods_exist(self):
        """start / stop / elapsed_minutes / estimated_cost_usd の公開メソッドが存在すること。"""
        assert hasattr(CostMonitor, "start"), "start メソッドが存在しない"
        assert hasattr(CostMonitor, "stop"), "stop メソッドが存在しない"
        assert hasattr(CostMonitor, "elapsed_minutes"), "elapsed_minutes メソッドが存在しない"
        assert hasattr(CostMonitor, "estimated_cost_usd"), "estimated_cost_usd メソッドが存在しない"

    def test_default_max_session_minutes_is_60(self):
        """デフォルトの max_session_minutes が 60 であること。"""
        import inspect
        sig = inspect.signature(CostMonitor.__init__)
        default = sig.parameters["max_session_minutes"].default
        assert default == 60, f"デフォルトが 60 でない: {default}"
