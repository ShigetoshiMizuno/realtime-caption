"""
tests/test_multi_caption_system_residence.py

MultiCaptionSystem の常駐モデル API（PR-3）テスト。

対象 API:
  - start_route(route_id) / stop_route(route_id)
  - start_all() / stop_all()
  - terminate()
  - start() → start_all() thin wrapper
  - shutdown() → terminate() thin wrapper（PR-3 以降）
"""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import AudioStats, CaptionSystem, MultiCaptionSystem, RouteConfig, RouteState


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------


def _make_minimal_caption_system(route_id: str = "test") -> CaptionSystem:
    """object.__new__ で最小 CaptionSystem を構築するヘルパー。"""
    cs = object.__new__(CaptionSystem)
    cs._audio_stats_lock = threading.Lock()
    cs._audio_stats = AudioStats()
    cs._stop_event = threading.Event()
    cs._realtime_translator = None
    cs._cost_monitor = None
    cs._recorder = None
    cs._loop = None
    cs._stop_event_async = None
    cs._audio_stream = None
    cs._capture_stream = None
    cs._capture_thread = None
    cs._route_id = route_id
    cs._state = RouteState.IDLE
    cs._state_lock = threading.Lock()
    return cs


def _make_mcs_with_mock_routes() -> "tuple[MultiCaptionSystem, MagicMock, MagicMock]":
    """両 route を MagicMock に差し替えた MultiCaptionSystem を返す。

    start()/stop() の呼び出しを記録するため、CaptionSystem を MagicMock で置換する。
    """
    obj = object.__new__(MultiCaptionSystem)
    mock_a = MagicMock(spec=CaptionSystem)
    mock_a.state = RouteState.IDLE
    mock_b = MagicMock(spec=CaptionSystem)
    mock_b.state = RouteState.IDLE
    obj._route_a = mock_a
    obj._route_b = mock_b
    obj._thread_a = None
    obj._thread_b = None
    return obj, mock_a, mock_b


# ---------------------------------------------------------------------------
# start_route / stop_route 個別テスト
# ---------------------------------------------------------------------------


class TestStartStopRouteIndividual:
    """start_route / stop_route が指定 route のみを操作することのテスト。"""

    def test_start_route_a_calls_only_route_a_start(self):
        """start_route('a') が route_a.start() を呼び、route_b には影響しないこと。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()

        mcs.start_route("a")

        mock_a.start.assert_called_once()
        mock_b.start.assert_not_called()

    def test_start_route_b_calls_only_route_b_start(self):
        """start_route('b') が route_b.start() を呼び、route_a には影響しないこと。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()

        mcs.start_route("b")

        mock_b.start.assert_called_once()
        mock_a.start.assert_not_called()

    def test_stop_route_a_calls_only_route_a_stop(self):
        """stop_route('a') が route_a.stop() を呼び、route_b には影響しないこと。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()

        mcs.stop_route("a")

        mock_a.stop.assert_called_once()
        mock_b.stop.assert_not_called()

    def test_stop_route_b_calls_only_route_b_stop(self):
        """stop_route('b') が route_b.stop() を呼び、route_a には影響しないこと。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()

        mcs.stop_route("b")

        mock_b.stop.assert_called_once()
        mock_a.stop.assert_not_called()

    def test_start_route_none_is_safe_for_a(self):
        """route_a が None のとき start_route('a') がクラッシュしないこと。"""
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = None
        obj._route_b = _make_minimal_caption_system("b")
        obj._thread_a = None
        obj._thread_b = None

        # 例外が発生しなければ OK
        obj.start_route("a")

    def test_start_route_none_is_safe_for_b(self):
        """route_b が None のとき start_route('b') がクラッシュしないこと。"""
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = _make_minimal_caption_system("a")
        obj._route_b = None
        obj._thread_a = None
        obj._thread_b = None

        # 例外が発生しなければ OK
        obj.start_route("b")

    def test_stop_route_none_is_safe_for_a(self):
        """route_a が None のとき stop_route('a') がクラッシュしないこと。"""
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = None
        obj._route_b = _make_minimal_caption_system("b")

        obj.stop_route("a")

    def test_stop_route_none_is_safe_for_b(self):
        """route_b が None のとき stop_route('b') がクラッシュしないこと。"""
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = _make_minimal_caption_system("a")
        obj._route_b = None

        obj.stop_route("b")


# ---------------------------------------------------------------------------
# start_all / stop_all テスト
# ---------------------------------------------------------------------------


class TestStartAllStopAll:
    """start_all / stop_all が両 route を操作することのテスト。"""

    def test_start_all_calls_start_on_both_routes(self):
        """start_all() が route_a.start() と route_b.start() を両方呼ぶこと。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()

        mcs.start_all()

        mock_a.start.assert_called_once()
        mock_b.start.assert_called_once()

    def test_stop_all_calls_stop_on_both_routes(self):
        """stop_all() が route_a.stop() と route_b.stop() を両方呼ぶこと。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()

        mcs.stop_all()

        mock_a.stop.assert_called_once()
        mock_b.stop.assert_called_once()

    def test_start_all_skips_none_route_a(self):
        """route_a が None のとき start_all() が route_b.start() だけ呼ぶこと。"""
        obj = object.__new__(MultiCaptionSystem)
        mock_b = MagicMock(spec=CaptionSystem)
        obj._route_a = None
        obj._route_b = mock_b
        obj._thread_a = None
        obj._thread_b = None

        obj.start_all()

        mock_b.start.assert_called_once()

    def test_start_all_skips_none_route_b(self):
        """route_b が None のとき start_all() が route_a.start() だけ呼ぶこと。"""
        obj = object.__new__(MultiCaptionSystem)
        mock_a = MagicMock(spec=CaptionSystem)
        obj._route_a = mock_a
        obj._route_b = None
        obj._thread_a = None
        obj._thread_b = None

        obj.start_all()

        mock_a.start.assert_called_once()

    def test_stop_all_skips_none_route_a(self):
        """route_a が None のとき stop_all() が route_b.stop() だけ呼ぶこと。"""
        obj = object.__new__(MultiCaptionSystem)
        mock_b = MagicMock(spec=CaptionSystem)
        obj._route_a = None
        obj._route_b = mock_b

        obj.stop_all()

        mock_b.stop.assert_called_once()

    def test_stop_all_skips_none_route_b(self):
        """route_b が None のとき stop_all() が route_a.stop() だけ呼ぶこと。"""
        obj = object.__new__(MultiCaptionSystem)
        mock_a = MagicMock(spec=CaptionSystem)
        obj._route_a = mock_a
        obj._route_b = None

        obj.stop_all()

        mock_a.stop.assert_called_once()


# ---------------------------------------------------------------------------
# terminate テスト
# ---------------------------------------------------------------------------


class TestTerminate:
    """terminate() が stop_all + PyAudio.terminate() を実行することのテスト。"""

    def test_terminate_calls_stop_all_and_pa_terminate(self):
        """terminate() が stop_all() を呼んで PyAudio.terminate() を実行すること。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()
        mock_pa = MagicMock()
        mcs._pa = mock_pa

        mcs.terminate()

        mock_a.stop.assert_called_once()
        mock_b.stop.assert_called_once()
        mock_pa.terminate.assert_called_once()

    def test_terminate_sets_pa_to_none_after_terminate(self):
        """terminate() の後、_pa が None になること（二重 terminate 防止）。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()
        mock_pa = MagicMock()
        mcs._pa = mock_pa

        mcs.terminate()

        assert mcs._pa is None

    def test_terminate_safe_when_pa_is_none(self):
        """_pa が None のとき terminate() がクラッシュしないこと。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()
        mcs._pa = None

        # 例外が発生しなければ OK
        mcs.terminate()

    def test_terminate_safe_when_pa_terminate_raises(self):
        """PyAudio.terminate() が例外を投げても terminate() が継続すること。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()
        mock_pa = MagicMock()
        mock_pa.terminate.side_effect = Exception("PA error")
        mcs._pa = mock_pa

        # 例外が伝播しなければ OK
        mcs.terminate()

    def test_terminate_calls_stop_before_pa_terminate(self):
        """stop_all() が pa.terminate() より前に呼ばれること（安全な順序保証）。"""
        call_order = []

        obj = object.__new__(MultiCaptionSystem)
        mock_a = MagicMock(spec=CaptionSystem)
        mock_a.stop.side_effect = lambda: call_order.append("stop_a")
        mock_b = MagicMock(spec=CaptionSystem)
        mock_b.stop.side_effect = lambda: call_order.append("stop_b")
        obj._route_a = mock_a
        obj._route_b = mock_b
        mock_pa = MagicMock()
        mock_pa.terminate.side_effect = lambda: call_order.append("pa_terminate")
        obj._pa = mock_pa

        obj.terminate()

        assert "pa_terminate" in call_order
        assert "stop_a" in call_order
        assert "stop_b" in call_order
        terminate_idx = call_order.index("pa_terminate")
        for label in ("stop_a", "stop_b"):
            assert call_order.index(label) < terminate_idx, (
                f"{label} が pa_terminate より後に呼ばれた"
            )


# ---------------------------------------------------------------------------
# thin wrapper テスト
# ---------------------------------------------------------------------------


class TestThinWrappers:
    """start() / shutdown() が thin wrapper として機能することのテスト。"""

    def test_start_method_calls_start_all(self):
        """start() が start_all() を呼ぶこと（thin wrapper）。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()

        # start_all を patch してコール数を確認
        with patch.object(mcs, "start_all") as mock_start_all:
            mcs.start()

        mock_start_all.assert_called_once()

    def test_shutdown_method_calls_terminate(self):
        """shutdown() が terminate() を呼ぶこと（thin wrapper）。"""
        mcs, mock_a, mock_b = _make_mcs_with_mock_routes()
        mcs._pa = None

        with patch.object(mcs, "terminate") as mock_terminate:
            mcs.shutdown()

        mock_terminate.assert_called_once()
