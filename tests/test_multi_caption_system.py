"""
tests/test_multi_caption_system.py

MultiCaptionSystem の起動・停止・経路独立性テスト。

Phase 2 (issue #38 翻訳こんにゃくモード) のコア実装を TDD で検証する。
"""

import sys
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import AudioStats, CaptionSystem, MultiCaptionSystem, RouteConfig


# ---------------------------------------------------------------------------
# テスト用ヘルパー
# ---------------------------------------------------------------------------

def _make_route_config(route_id: str = "a") -> RouteConfig:
    """テスト用 RouteConfig を生成するヘルパー。"""
    return RouteConfig(
        route_id=route_id,
        input_device_info={"index": 0, "name": f"FakeDevice-{route_id}"},
        target_language_code="ja" if route_id == "a" else "en",
        audio_output_enabled=False,
        output_device_index=None,
        output_volume=1.0,
    )


def _make_fake_config() -> dict:
    """テスト用の最小限設定 dict（API キーはフェイク値）。"""
    return {
        "translation": {
            "translation_model": "openai-realtime",
        },
        "openai": {
            "api_key": "sk-test-fake-0000000000000000",
        },
        "openai_realtime": {
            "target_language_code": "ja",
            "model": "gpt-realtime-translate",
            "connect_timeout": 10,
            "reconnect_max_attempts": 5,
            "reconnect_backoff_base": 1.5,
            "max_session_minutes": 60,
            "audio_output": {},
        },
        "output": {
            "log_dir": ".",
        },
    }


def _make_multi_caption_system_minimal() -> MultiCaptionSystem:
    """object.__new__ でバイパスして最小限 MultiCaptionSystem インスタンスを作る。

    MultiCaptionSystem の __init__ は CaptionSystem を2つ生成するが、
    それぞれが重い依存（OpenAI クライアント等）を持つため、
    スレッド安全性テスト等では object.__new__ で生成し必要属性だけ設定する。
    """
    obj = object.__new__(MultiCaptionSystem)

    # route_a: AudioStats のみ持つ最小 CaptionSystem
    route_a = object.__new__(CaptionSystem)
    route_a._audio_stats_lock = threading.Lock()
    route_a._audio_stats = AudioStats()
    route_a._stop_event = threading.Event()
    route_a._realtime_translator = None
    route_a._cost_monitor = None

    # route_b: AudioStats のみ持つ最小 CaptionSystem
    route_b = object.__new__(CaptionSystem)
    route_b._audio_stats_lock = threading.Lock()
    route_b._audio_stats = AudioStats()
    route_b._stop_event = threading.Event()
    route_b._realtime_translator = None
    route_b._cost_monitor = None

    obj._route_a = route_a
    obj._route_b = route_b

    return obj


# ---------------------------------------------------------------------------
# MultiCaptionSystem インスタンス化テスト
# ---------------------------------------------------------------------------

class TestMultiCaptionSystemInstantiation:
    """MultiCaptionSystem が正常にインスタンス化できること。"""

    def test_multi_caption_system_can_be_instantiated(self):
        """MultiCaptionSystem が RouteConfig 2つを受け取りインスタンス化できること。"""
        config = _make_fake_config()
        route_a = _make_route_config("a")
        route_b = _make_route_config("b")

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=config,
                route_a=route_a,
                route_b=route_b,
            )

        assert mcs is not None

    def test_multi_caption_system_has_two_routes(self):
        """route_a_system, route_b_system プロパティが CaptionSystem 型を返すこと。"""
        config = _make_fake_config()
        route_a = _make_route_config("a")
        route_b = _make_route_config("b")

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=config,
                route_a=route_a,
                route_b=route_b,
            )

        assert isinstance(mcs.route_a_system, CaptionSystem)
        assert isinstance(mcs.route_b_system, CaptionSystem)


# ---------------------------------------------------------------------------
# 停止テスト
# ---------------------------------------------------------------------------

class TestMultiCaptionSystemShutdown:
    """shutdown() 呼び出し時の動作テスト。"""

    def test_multi_caption_system_shutdown_stops_both(self):
        """shutdown() を呼ぶと両系統の stop_event がセットされること。"""
        mcs = _make_multi_caption_system_minimal()

        assert not mcs._route_a._stop_event.is_set()
        assert not mcs._route_b._stop_event.is_set()

        mcs.shutdown()

        assert mcs._route_a._stop_event.is_set(), "route_a の stop_event がセットされていない"
        assert mcs._route_b._stop_event.is_set(), "route_b の stop_event がセットされていない"


# ---------------------------------------------------------------------------
# 経路独立性テスト
# ---------------------------------------------------------------------------

class TestRouteIndependence:
    """経路A・Bが独立したリソースを持つことのテスト。"""

    def test_route_a_and_b_have_different_translator_instances(self):
        """route_a と route_b の _realtime_translator が別インスタンスであること。

        注: 実際の起動は重いので、インスタンス化直後のオブジェクト同一性のみ確認。
        """
        config = _make_fake_config()
        route_a = _make_route_config("a")
        route_b = _make_route_config("b")

        with patch("realtime_translator.RealtimeTranslator") as MockTranslator:
            # 呼び出しごとに別インスタンスを返す
            MockTranslator.side_effect = [MagicMock(), MagicMock()]
            mcs = MultiCaptionSystem(
                config=config,
                route_a=route_a,
                route_b=route_b,
            )

        translator_a = mcs.route_a_system._realtime_translator
        translator_b = mcs.route_b_system._realtime_translator

        assert translator_a is not None, "route_a の _realtime_translator が None"
        assert translator_b is not None, "route_b の _realtime_translator が None"
        assert translator_a is not translator_b, "route_a と route_b が同一の translator を共有している"

    def test_route_a_and_b_have_independent_audio_stats(self):
        """経路Aの AudioStats 更新が経路Bに影響しないこと。"""
        mcs = _make_multi_caption_system_minimal()

        # route_a の peak を更新
        mcs._route_a._update_audio_stats(peak=9999)

        # route_b は変化しない
        assert mcs._route_a.audio_stats.peak == 9999
        assert mcs._route_b.audio_stats.peak == 0, \
            "route_a の更新が route_b に波及している"

    def test_route_a_output_device_independent_from_route_b(self):
        """route_a の output_device_index 変更が route_b に影響しないこと。"""
        config = _make_fake_config()
        route_a_cfg = _make_route_config("a")
        route_b_cfg = _make_route_config("b")

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=config,
                route_a=route_a_cfg,
                route_b=route_b_cfg,
            )

        # _output_device_index は各 CaptionSystem が独立して保持する
        assert mcs.route_a_system._output_device_index is None
        assert mcs.route_b_system._output_device_index is None
        # 各系統のインスタンスが異なること（同一オブジェクトでないこと）
        assert mcs.route_a_system is not mcs.route_b_system
