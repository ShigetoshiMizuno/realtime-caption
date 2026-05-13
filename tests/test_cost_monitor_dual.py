"""
tests/test_cost_monitor_dual.py

MultiCaptionSystem のコスト合算テスト。

仕様書 §4 「cost_monitor の 2倍化」より:
- CostMonitor は変更しない
- MultiCaptionSystem.total_estimated_cost_usd が両 CostMonitor の合算を返す
"""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from cost_monitor import CostMonitor, RATE_USD_PER_MINUTE
from main import AudioStats, CaptionSystem, MultiCaptionSystem


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_mock_cost_monitor(elapsed_minutes: float) -> CostMonitor:
    """指定した経過時間を返すモック CostMonitor を生成する。"""
    monitor = MagicMock(spec=CostMonitor)
    monitor.estimated_cost_usd.return_value = elapsed_minutes * RATE_USD_PER_MINUTE
    return monitor


def _make_multi_caption_system_with_monitors(
    elapsed_a: float,
    elapsed_b: float,
) -> MultiCaptionSystem:
    """指定した経過時間のモック CostMonitor を持つ MultiCaptionSystem を作る。"""
    obj = object.__new__(MultiCaptionSystem)

    route_a = object.__new__(CaptionSystem)
    route_a._audio_stats_lock = threading.Lock()
    route_a._audio_stats = AudioStats()
    route_a._stop_event = threading.Event()
    route_a._realtime_translator = None
    route_a._cost_monitor = _make_mock_cost_monitor(elapsed_a)

    route_b = object.__new__(CaptionSystem)
    route_b._audio_stats_lock = threading.Lock()
    route_b._audio_stats = AudioStats()
    route_b._stop_event = threading.Event()
    route_b._realtime_translator = None
    route_b._cost_monitor = _make_mock_cost_monitor(elapsed_b)

    obj._route_a = route_a
    obj._route_b = route_b

    return obj


# ---------------------------------------------------------------------------
# コスト合算テスト
# ---------------------------------------------------------------------------

class TestDualCostMonitor:
    """MultiCaptionSystem のコスト合算ロジックのテスト。"""

    def test_total_estimated_cost_is_sum_of_two_sessions(self):
        """MultiCaptionSystem.total_estimated_cost_usd が両モニターの合算を返すこと。"""
        # 経路A: 10分、経路B: 20分
        mcs = _make_multi_caption_system_with_monitors(
            elapsed_a=10.0,
            elapsed_b=20.0,
        )

        expected = (10.0 + 20.0) * RATE_USD_PER_MINUTE
        assert mcs.total_estimated_cost_usd == pytest.approx(expected)

    def test_total_cost_is_zero_when_both_monitors_are_none(self):
        """両系統の _cost_monitor が None のとき合算コストは 0.0 であること。"""
        obj = object.__new__(MultiCaptionSystem)

        route_a = object.__new__(CaptionSystem)
        route_a._audio_stats_lock = threading.Lock()
        route_a._audio_stats = AudioStats()
        route_a._stop_event = threading.Event()
        route_a._realtime_translator = None
        route_a._cost_monitor = None

        route_b = object.__new__(CaptionSystem)
        route_b._audio_stats_lock = threading.Lock()
        route_b._audio_stats = AudioStats()
        route_b._stop_event = threading.Event()
        route_b._realtime_translator = None
        route_b._cost_monitor = None

        obj._route_a = route_a
        obj._route_b = route_b

        assert obj.total_estimated_cost_usd == 0.0

    def test_dual_rate_is_approximately_0_068_per_minute(self):
        """1分間動作時の合算コストが約 $0.068 であること（±10%）。

        RATE_USD_PER_MINUTE = 0.034 なので 2系統で 0.068/分。
        """
        # 両系統ともに 1分間動作
        mcs = _make_multi_caption_system_with_monitors(
            elapsed_a=1.0,
            elapsed_b=1.0,
        )

        expected_rate = 0.068  # 2 * RATE_USD_PER_MINUTE = 2 * 0.034
        tolerance = 0.1  # ±10%

        actual = mcs.total_estimated_cost_usd
        assert actual == pytest.approx(expected_rate, rel=tolerance), (
            f"合算レートが期待値から外れています: actual={actual:.4f}, expected≈{expected_rate:.4f}"
        )
