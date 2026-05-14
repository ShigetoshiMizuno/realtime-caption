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


def _make_minimal_caption_system() -> CaptionSystem:
    """object.__new__ でバイパスして最小限の CaptionSystem を作るヘルパー。"""
    cs = object.__new__(CaptionSystem)
    cs._audio_stats_lock = threading.Lock()
    cs._audio_stats = AudioStats()
    cs._stop_event = threading.Event()
    cs._realtime_translator = None
    cs._cost_monitor = None
    # shutdown() が参照するフィールドをすべて初期化
    cs._recorder = None
    cs._loop = None
    cs._stop_event_async = None
    cs._audio_stream = None
    return cs


def _make_multi_caption_system_minimal() -> MultiCaptionSystem:
    """object.__new__ でバイパスして最小限 MultiCaptionSystem インスタンスを作る。

    MultiCaptionSystem の __init__ は CaptionSystem を2つ生成するが、
    それぞれが重い依存（OpenAI クライアント等）を持つため、
    スレッド安全性テスト等では object.__new__ で生成し必要属性だけ設定する。
    """
    obj = object.__new__(MultiCaptionSystem)
    obj._route_a = _make_minimal_caption_system()
    obj._route_b = _make_minimal_caption_system()
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


# ---------------------------------------------------------------------------
# Phase 4: shared broadcaster + asyncio event loop テスト (Issue #38)
# ---------------------------------------------------------------------------

class TestMultiCaptionSystemPhase4:
    """Phase 4 で追加する start() 本実装のテスト。"""

    def test_multi_caption_system_uses_shared_broadcaster(self):
        """両 route の CaptionSystem が同一 SubtitleBroadcaster インスタンスを参照すること。"""
        config = _make_fake_config()
        route_a = _make_route_config("a")
        route_b = _make_route_config("b")

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=config,
                route_a=route_a,
                route_b=route_b,
            )

        broadcaster_a = mcs.route_a_system._broadcaster
        broadcaster_b = mcs.route_b_system._broadcaster

        assert broadcaster_a is broadcaster_b, (
            "route_a と route_b が異なる SubtitleBroadcaster を持っている"
        )

    def test_broadcaster_lock_safe_across_event_loops(self):
        """SubtitleBroadcaster が threading.Lock を使い、複数の asyncio.run() スレッドから
        安全に broadcast できること（type(self._lock) が threading.Lock であること）。"""
        from main import SubtitleBroadcaster
        import threading
        broadcaster = SubtitleBroadcaster()
        assert isinstance(broadcaster._lock, type(threading.Lock())), (
            f"SubtitleBroadcaster._lock は threading.Lock であるべき、実際: {type(broadcaster._lock)}"
        )

    def test_multi_caption_system_start_creates_event_loop(self):
        """start() が asyncio イベントループを開始し、shutdown() で終了すること。

        実音声デバイス不要のモックで検証。
        start() はバックグラウンドスレッドでループを起動し、
        shutdown() 後にそのスレッドが終了することを確認する。
        """
        import asyncio
        import time
        config = _make_fake_config()
        route_a = _make_route_config("a")
        route_b = _make_route_config("b")

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=config,
                route_a=route_a,
                route_b=route_b,
            )

        # start() が内部スレッドを立てて asyncio ループを回すことを確認する。
        # CaptionSystem.run() の WebSocket 起動・音声デバイスオープンをモックする。
        # patch.object でクラスメソッドを置換する際は self を受け取る必要がある。
        async def _fake_run_a(self_ignored):
            mcs.route_a_system._loop = asyncio.get_running_loop()
            mcs.route_a_system._stop_event_async = asyncio.Event()
            # shutdown() で stop_event が set されるまで待機
            while not mcs.route_a_system._stop_event.is_set():
                await asyncio.sleep(0.05)

        async def _fake_run_b(self_ignored):
            mcs.route_b_system._loop = asyncio.get_running_loop()
            mcs.route_b_system._stop_event_async = asyncio.Event()
            while not mcs.route_b_system._stop_event.is_set():
                await asyncio.sleep(0.05)

        with (
            patch.object(mcs.route_a_system.__class__, "run", _fake_run_a),
            patch.object(mcs.route_b_system.__class__, "run", _fake_run_b),
        ):
            mcs.start()
            # スレッドが起動していることを確認
            assert mcs._thread_a is not None and mcs._thread_a.is_alive(), \
                "route_a のスレッドが起動していない"
            assert mcs._thread_b is not None and mcs._thread_b.is_alive(), \
                "route_b のスレッドが起動していない"

            # shutdown して両スレッドが終了するか確認
            mcs.shutdown()
            mcs._thread_a.join(timeout=3.0)
            mcs._thread_b.join(timeout=3.0)
            assert not mcs._thread_a.is_alive(), "route_a スレッドが終了していない"
            assert not mcs._thread_b.is_alive(), "route_b スレッドが終了していない"


# ---------------------------------------------------------------------------
# Phase 5: 共有 PyAudio インスタンス（Issue #38 PortAudio assertion 対策）
# ---------------------------------------------------------------------------

class TestSharedPyAudioInstance:
    """MultiCaptionSystem が PyAudio を1つだけ生成し両 route で共有すること。

    背景: 複数の pyaudio.PyAudio() を並列初期化すると WASAPI の状態が破壊され
    'Assertion failed: hostApi->info.defaultOutputDevice < hostApi->info.deviceCount'
    が発生してプロセスがクラッシュする。
    """

    def test_multi_caption_system_creates_single_pyaudio_instance(self):
        """MultiCaptionSystem が PyAudio インスタンスを1つだけ生成し、両 route で共有すること。"""
        with patch("main.pyaudio.PyAudio") as mock_pa_cls:
            mock_pa_cls.return_value = MagicMock()
            with patch("realtime_translator.RealtimeTranslator"):
                mcs = MultiCaptionSystem(
                    config=_make_fake_config(),
                    route_a=_make_route_config("a"),
                    route_b=_make_route_config("b"),
                )
            # PyAudio() は1回だけ呼ばれること（共有インスタンス）
            assert mock_pa_cls.call_count == 1, (
                f"PyAudio() が {mock_pa_cls.call_count} 回呼ばれた。1回だけ呼ばれるべき。"
            )
            # 両 route が同じ PyAudio インスタンスを参照すること
            assert mcs.route_a_system._pa_instance is mcs.route_b_system._pa_instance, (
                "route_a と route_b が異なる PyAudio インスタンスを持っている"
            )

    def test_multi_caption_system_shutdown_terminates_pyaudio(self):
        """MultiCaptionSystem.shutdown() で共有 PyAudio インスタンスが terminate されること。"""
        with patch("main.pyaudio.PyAudio") as mock_pa_cls:
            mock_instance = MagicMock()
            mock_pa_cls.return_value = mock_instance
            with patch("realtime_translator.RealtimeTranslator"):
                mcs = MultiCaptionSystem(
                    config=_make_fake_config(),
                    route_a=_make_route_config("a"),
                    route_b=_make_route_config("b"),
                )
            mcs.shutdown()
            mock_instance.terminate.assert_called_once()

    def test_caption_system_single_mode_has_none_pa_instance_by_default(self):
        """CaptionSystem 単独起動時（pa_instance 未指定）は _pa_instance が None であること（後方互換）。"""
        with patch("realtime_translator.RealtimeTranslator"):
            cs = CaptionSystem(
                config=_make_fake_config(),
                device_info={"index": 0, "name": "FakeDevice"},
                model_name="tiny",
                # pa_instance を渡さない → デフォルト None
            )
        assert cs._pa_instance is None, (
            "_pa_instance はデフォルトで None であるべき"
        )


# ---------------------------------------------------------------------------
# Phase 6: shutdown 安全順序（Issue #38 access violation 修正）
# ---------------------------------------------------------------------------

class TestSafeShutdownOrdering:
    """shutdown() が capture スレッドを join してから PyAudio.terminate() を呼ぶこと。

    背景: capture スレッドが pyaudiowpatch.read() を実行中に terminate() を呼ぶと
    PortAudio が access violation でクラッシュする（実機ログ確認済み）。
    修正後は join(timeout=5.0) でスレッド終了を待ってから terminate する。
    """

    def test_multi_caption_system_shutdown_joins_threads_before_pa_terminate(self):
        """shutdown() が asyncio スレッド (_thread_a/_thread_b) を join してから
        PyAudio.terminate() を呼ぶこと。

        背景: capture スレッドが pyaudiowpatch.read() を実行中に pa.terminate() が呼ばれると
        PortAudio が access violation でクラッシュする。
        MultiCaptionSystem.shutdown() は _thread_a/_thread_b を join(timeout=5.0) してから
        pa.terminate() を呼ぶ必要がある。
        """
        call_order = []

        # スレッドモック: join() が呼ばれたことを記録する
        mock_thread_a = MagicMock(spec=threading.Thread)
        mock_thread_a.is_alive.return_value = True
        mock_thread_a.join.side_effect = lambda timeout=None: call_order.append("join_a")

        mock_thread_b = MagicMock(spec=threading.Thread)
        mock_thread_b.is_alive.return_value = True
        mock_thread_b.join.side_effect = lambda timeout=None: call_order.append("join_b")

        # PyAudio モック: terminate() が呼ばれたことを記録する
        mock_pa = MagicMock()
        mock_pa.terminate.side_effect = lambda: call_order.append("pa_terminate")

        # start() 呼び出し済みを前提にした状態を組み立てる
        # （_thread_a/_thread_b が shutdown 後に join されるべき状態）
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = _make_minimal_caption_system()
        obj._route_b = _make_minimal_caption_system()
        obj._thread_a = mock_thread_a
        obj._thread_b = mock_thread_b
        obj._pa = mock_pa

        obj.shutdown()

        # join_a と join_b が pa_terminate より前に現れていること
        assert "pa_terminate" in call_order, "pa_terminate が呼ばれなかった"
        assert "join_a" in call_order, "_thread_a の join() が呼ばれなかった"
        assert "join_b" in call_order, "_thread_b の join() が呼ばれなかった"
        terminate_idx = call_order.index("pa_terminate")
        for join_label in ("join_a", "join_b"):
            join_idx = call_order.index(join_label)
            assert join_idx < terminate_idx, (
                f"{join_label}({join_idx}) が pa_terminate({terminate_idx}) より後に呼ばれた"
            )

    def test_multi_caption_system_shutdown_calls_join_with_timeout(self):
        """shutdown() の _thread join が timeout=5.0 で呼ばれること。

        無限ブロックを防ぐため timeout 引数は必須。
        """
        mock_thread_a = MagicMock(spec=threading.Thread)
        mock_thread_a.is_alive.return_value = True
        mock_thread_b = MagicMock(spec=threading.Thread)
        mock_thread_b.is_alive.return_value = True
        mock_pa = MagicMock()

        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = _make_minimal_caption_system()
        obj._route_b = _make_minimal_caption_system()
        obj._thread_a = mock_thread_a
        obj._thread_b = mock_thread_b
        obj._pa = mock_pa

        obj.shutdown()

        # join() が timeout 付きで呼ばれること（timeout=5.0）
        mock_thread_a.join.assert_called_once()
        call_kwargs = mock_thread_a.join.call_args
        timeout_val = call_kwargs.kwargs.get("timeout") or (
            call_kwargs.args[0] if call_kwargs.args else None
        )
        assert timeout_val is not None, "_thread_a.join() が timeout 引数なしで呼ばれた"
        assert timeout_val >= 1.0, f"timeout={timeout_val} が小さすぎる（最低 1.0 秒必要）"

    def test_caption_system_shutdown_wakes_asyncio_event(self):
        """CaptionSystem.shutdown() が asyncio 側 _stop_event_async を起こすこと。

        別スレッドから shutdown() を呼んだとき、loop.call_soon_threadsafe で
        _stop_event_async.set() が安全に呼ばれることを確認する。
        """
        cs = _make_minimal_caption_system()

        # asyncio loop と _stop_event_async のモックを注入
        mock_loop = MagicMock()
        mock_async_event = MagicMock()
        cs._loop = mock_loop
        cs._stop_event_async = mock_async_event

        cs.shutdown()

        # call_soon_threadsafe が _stop_event_async.set を引数に呼ばれること
        mock_loop.call_soon_threadsafe.assert_called_once_with(mock_async_event.set)


# ---------------------------------------------------------------------------
# Phase 7: capture スレッドを join してから PyAudio.terminate()（Issue #38 クラッシュ修正）
# ---------------------------------------------------------------------------

class TestCaptureThreadJoinBeforeTerminate:
    """shutdown() が _capture_thread も join してから PyAudio.terminate() を呼ぶこと。

    背景: _thread_a/_thread_b は asyncio.run() のスレッドのみ。
    _capture_thread (音声キャプチャ) は別物であり、read() 中に terminate() が呼ばれると
    PortAudio が access violation でクラッシュする（実機ログ確認済み）。
    """

    def test_shutdown_joins_capture_threads_before_pa_terminate(self):
        """shutdown() が capture スレッド (_capture_thread) も join してから
        pa.terminate() を呼ぶこと（access violation 防止）。

        背景: _thread_a/_thread_b は asyncio.run() のスレッドのみで、
        別途存在する _capture_thread (音声キャプチャ) は別物。
        pa.terminate() 時に _capture_thread が read() 中だとクラッシュ。
        """
        call_order = []

        # capture スレッドモック: join() 呼び出しを記録する
        mock_cap_a = MagicMock(spec=threading.Thread)
        mock_cap_a.is_alive.return_value = True
        mock_cap_a.join.side_effect = lambda timeout=None: call_order.append("join_cap_a")

        mock_cap_b = MagicMock(spec=threading.Thread)
        mock_cap_b.is_alive.return_value = True
        mock_cap_b.join.side_effect = lambda timeout=None: call_order.append("join_cap_b")

        # PyAudio モック: terminate() 呼び出しを記録する
        mock_pa = MagicMock()
        mock_pa.terminate.side_effect = lambda: call_order.append("pa_terminate")

        # MultiCaptionSystem を object.__new__ で最小構成にする
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = _make_minimal_caption_system()
        obj._route_b = _make_minimal_caption_system()
        # _capture_thread を各 CaptionSystem に注入
        obj._route_a._capture_thread = mock_cap_a
        obj._route_b._capture_thread = mock_cap_b
        # asyncio スレッドは持たない（shutdown が _capture_thread まで到達するケース）
        obj._pa = mock_pa

        obj.shutdown()

        # capture スレッドの join が pa_terminate より前に呼ばれること
        assert "pa_terminate" in call_order, "pa_terminate が呼ばれなかった"
        assert "join_cap_a" in call_order, "_route_a._capture_thread の join() が呼ばれなかった"
        assert "join_cap_b" in call_order, "_route_b._capture_thread の join() が呼ばれなかった"

        terminate_idx = call_order.index("pa_terminate")
        for label in ("join_cap_a", "join_cap_b"):
            join_idx = call_order.index(label)
            assert join_idx < terminate_idx, (
                f"{label}({join_idx}) が pa_terminate({terminate_idx}) より後に呼ばれた"
            )

    def test_shutdown_joins_capture_threads_with_sufficient_timeout(self):
        """shutdown() の _capture_thread join が十分な timeout（>= 5.0 秒）で呼ばれること。

        pa.read() は最大 chunk_size/sample_rate 秒ブロックするため、
        短い timeout だとスレッドが生き残り terminate() クラッシュの原因になる。
        """
        mock_cap_a = MagicMock(spec=threading.Thread)
        mock_cap_a.is_alive.return_value = True
        mock_pa = MagicMock()

        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = _make_minimal_caption_system()
        obj._route_b = _make_minimal_caption_system()
        obj._route_a._capture_thread = mock_cap_a
        obj._pa = mock_pa

        obj.shutdown()

        # join() が少なくとも1回以上呼ばれること
        assert mock_cap_a.join.call_count >= 1, "_capture_thread.join() が呼ばれなかった"
        # 各呼び出しすべてで timeout が 5.0 秒以上であること
        for i, call in enumerate(mock_cap_a.join.call_args_list):
            timeout_val = call.kwargs.get("timeout") or (
                call.args[0] if call.args else None
            )
            assert timeout_val is not None, (
                f"_capture_thread.join() 呼び出し #{i} に timeout 引数がない"
            )
            assert timeout_val >= 5.0, (
                f"capture thread join #{i} timeout={timeout_val} が小さすぎる（5.0 秒以上必要）"
            )

    def test_caption_system_shutdown_capture_thread_join_timeout_is_5_seconds(self):
        """CaptionSystem.shutdown() の capture thread join が timeout=5.0 で呼ばれること。

        旧実装は timeout=1.0 で pa.read() のブロックを抜けられず、
        MultiCaptionSystem.shutdown() が terminate() を呼ぶ前にスレッドが残存していた。
        """
        mock_cap = MagicMock(spec=threading.Thread)
        mock_cap.is_alive.return_value = True

        cs = _make_minimal_caption_system()
        cs._capture_thread = mock_cap

        cs.shutdown()

        # join() が timeout=5.0 で呼ばれること
        mock_cap.join.assert_called_once()
        call_kwargs = mock_cap.join.call_args
        timeout_val = call_kwargs.kwargs.get("timeout") or (
            call_kwargs.args[0] if call_kwargs.args else None
        )
        assert timeout_val is not None, "CaptionSystem.shutdown() の capture join に timeout がない"
        assert timeout_val >= 5.0, (
            f"CaptionSystem.shutdown() の capture join timeout={timeout_val} が小さすぎる（5.0 秒以上必要）"
        )


# ---------------------------------------------------------------------------
# Phase 8: shutdown() が _capture_stream.stop_stream() を呼んで read() ブロックを解除
# ---------------------------------------------------------------------------

class TestCaptureStreamStopOnShutdown:
    """shutdown() が _capture_stream.stop_stream() を呼んで read() のブロックを解除すること。

    背景: pyaudio.Stream.read() はブロッキング呼び出しで、stop_event をポーリングしない。
    _stop_event.set() だけでは capture loop が read() から抜け出せず、スレッドが 5 秒
    タイムアウト後も生き残り pa.terminate() 時に access violation が起きていた（実機確認済み）。

    修正: shutdown() は _stop_event.set() の直後に _capture_stream.stop_stream() を呼び、
    read() に OSError を投げさせて capture loop を即座に脱出させる。
    """

    def test_shutdown_calls_capture_stream_stop_stream(self):
        """CaptionSystem.shutdown() が _capture_stream.stop_stream() を呼ぶこと。"""
        cs = _make_minimal_caption_system()
        mock_stream = MagicMock()
        cs._capture_stream = mock_stream

        cs.shutdown()

        mock_stream.stop_stream.assert_called_once()

    def test_shutdown_calls_stop_stream_even_when_no_capture_thread(self):
        """_capture_thread が存在しない場合でも stop_stream() が呼ばれること。"""
        cs = _make_minimal_caption_system()
        mock_stream = MagicMock()
        cs._capture_stream = mock_stream
        # _capture_thread は設定しない（存在しない状態）

        cs.shutdown()

        mock_stream.stop_stream.assert_called_once()

    def test_shutdown_does_not_raise_if_capture_stream_is_none(self):
        """_capture_stream が None の場合に shutdown() が例外を投げないこと。"""
        cs = _make_minimal_caption_system()
        # _capture_stream は _make_minimal_caption_system では設定されていない
        # （None がデフォルト相当）

        # 例外が出なければ OK
        cs.shutdown()

    def test_shutdown_does_not_raise_if_stop_stream_raises(self):
        """stop_stream() が例外を投げても shutdown() が継続すること。"""
        cs = _make_minimal_caption_system()
        mock_stream = MagicMock()
        mock_stream.stop_stream.side_effect = OSError("stream already stopped")
        cs._capture_stream = mock_stream

        # 例外が出なければ OK（shutdown は stop_stream のエラーを飲み込む）
        cs.shutdown()

    def test_shutdown_calls_stop_stream_before_capture_thread_join(self):
        """stop_stream() が capture thread の join より前に呼ばれること。

        stop_stream() → read() が OSError → capture loop 脱出 → join 成功、
        という順序が保証されなければ join が 5 秒タイムアウトしてしまう。
        """
        call_order = []

        mock_stream = MagicMock()
        mock_stream.stop_stream.side_effect = lambda: call_order.append("stop_stream")

        mock_cap = MagicMock(spec=threading.Thread)
        mock_cap.is_alive.return_value = True
        mock_cap.join.side_effect = lambda timeout=None: call_order.append("join_cap")

        cs = _make_minimal_caption_system()
        cs._capture_stream = mock_stream
        cs._capture_thread = mock_cap

        cs.shutdown()

        assert "stop_stream" in call_order, "stop_stream() が呼ばれなかった"
        assert "join_cap" in call_order, "capture thread の join() が呼ばれなかった"

        stop_idx = call_order.index("stop_stream")
        join_idx = call_order.index("join_cap")
        assert stop_idx < join_idx, (
            f"stop_stream({stop_idx}) が join_cap({join_idx}) より後に呼ばれた"
        )


# ---------------------------------------------------------------------------
# Issue #43: 系統別 ON/OFF — Optional route テスト
# ---------------------------------------------------------------------------

class TestOptionalRouteInstantiation:
    """MultiCaptionSystem が route_a / route_b の片方を None で受け付けること。"""

    def test_multi_caption_system_accepts_only_route_a(self):
        """route_b=None で route_a のみで起動できること。"""
        with patch("main.pyaudio.PyAudio"), patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=None,
            )
        assert mcs.route_a_system is not None
        assert mcs.route_b_system is None

    def test_multi_caption_system_accepts_only_route_b(self):
        """route_a=None で route_b のみで起動できること。"""
        with patch("main.pyaudio.PyAudio"), patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=None,
                route_b=_make_route_config("b"),
            )
        assert mcs.route_a_system is None
        assert mcs.route_b_system is not None

    def test_multi_caption_system_rejects_both_none(self):
        """両方 None なら ValueError を投げること。"""
        with pytest.raises(ValueError, match="少なくとも1つ"):
            MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=None,
                route_b=None,
            )

    def test_route_a_only_total_cost_uses_single_session(self):
        """route_b=None のとき total_estimated_cost_usd が route_a 分だけ計上されること。"""
        with patch("main.pyaudio.PyAudio"), patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=None,
            )
        # _cost_monitor は None（初期状態）なので cost = 0.0 が返ること
        assert mcs.total_estimated_cost_usd == 0.0

    def test_route_b_only_creates_own_broadcaster(self):
        """route_a=None のとき route_b が broadcaster を所有すること（_owns_broadcaster=True）。"""
        with patch("main.pyaudio.PyAudio"), patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=None,
                route_b=_make_route_config("b"),
            )
        assert mcs.route_b_system._owns_broadcaster is True

    def test_shutdown_handles_missing_route_a_safely(self):
        """route_a=None（_route_a が None）のとき shutdown() が NoneType アクセスでクラッシュしないこと。"""
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = None
        obj._route_b = _make_minimal_caption_system()
        obj._pa = None

        # 例外が出なければ OK
        obj.shutdown()

    def test_shutdown_handles_missing_route_b_safely(self):
        """route_b=None（_route_b が None）のとき shutdown() が NoneType アクセスでクラッシュしないこと。"""
        obj = object.__new__(MultiCaptionSystem)
        obj._route_a = _make_minimal_caption_system()
        obj._route_b = None
        obj._pa = None

        # 例外が出なければ OK
        obj.shutdown()

    def test_pyaudio_created_once_for_single_route(self):
        """route_b=None でも PyAudio が1回だけ生成されること。"""
        with patch("main.pyaudio.PyAudio") as mock_pa_cls:
            mock_pa_cls.return_value = MagicMock()
            with patch("realtime_translator.RealtimeTranslator"):
                MultiCaptionSystem(
                    config=_make_fake_config(),
                    route_a=_make_route_config("a"),
                    route_b=None,
                )
        assert mock_pa_cls.call_count == 1


# ---------------------------------------------------------------------------
# Issue #46: CaptionSystem.output_volume setter テスト
# ---------------------------------------------------------------------------

class TestCaptionSystemOutputVolumeSetter:
    """CaptionSystem.output_volume setter が動的に AudioOutputStream に反映されること。"""

    def test_output_volume_property_exists(self):
        """CaptionSystem に output_volume プロパティが存在すること。"""
        cs = _make_minimal_caption_system()
        cs._output_volume = 1.0
        cs._audio_stream = None
        assert hasattr(cs, "output_volume"), (
            "CaptionSystem に output_volume プロパティが存在しない"
        )

    def test_output_volume_setter_updates_internal_value(self):
        """output_volume setter が _output_volume を更新すること。"""
        cs = _make_minimal_caption_system()
        cs._output_volume = 1.0
        cs._audio_stream = None
        cs.output_volume = 0.7
        assert cs._output_volume == 0.7, (
            f"output_volume setter 後 _output_volume が 0.7 のはず、実際: {cs._output_volume}"
        )

    def test_output_volume_setter_updates_audio_stream(self):
        """output_volume setter が動作中の _audio_stream.set_volume() を呼ぶこと。"""
        cs = _make_minimal_caption_system()
        cs._output_volume = 1.0
        mock_stream = MagicMock()
        cs._audio_stream = mock_stream

        cs.output_volume = 0.5

        mock_stream.set_volume.assert_called_once_with(0.5), (
            "output_volume setter が _audio_stream.set_volume() を呼んでいない"
        )

    def test_output_volume_setter_ignores_none_audio_stream(self):
        """_audio_stream が None のとき output_volume setter が例外を出さないこと。"""
        cs = _make_minimal_caption_system()
        cs._output_volume = 1.0
        cs._audio_stream = None

        # 例外が出なければ OK
        cs.output_volume = 0.3
        assert cs._output_volume == 0.3


# ---------------------------------------------------------------------------
# Issue #48: on_realtime_error コールバック伝播テスト
# ---------------------------------------------------------------------------

class TestOnRealtimeErrorCallback:
    """CaptionSystem._on_realtime_error が MultiCaptionSystem の on_realtime_error コールバックを呼ぶこと。"""

    def test_multi_caption_system_calls_on_realtime_error_for_route_a(self):
        """CaptionSystem._on_realtime_error が MultiCaptionSystem の on_realtime_error コールバックを
        route_id='a', 正しい category と display_text で呼ぶこと。"""
        callbacks = []

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=_make_route_config("b"),
                on_realtime_error=lambda r, c, t: callbacks.append((r, c, t)),
            )

        # 経路A の _on_realtime_error を直接呼ぶ
        mcs.route_a_system._on_realtime_error("insufficient_quota: exceeded")

        assert len(callbacks) == 1
        assert callbacks[0][0] == "a"            # route_id
        assert callbacks[0][1] == "quota"         # category
        assert "クォータ" in callbacks[0][2]      # display_text

    def test_multi_caption_system_calls_on_realtime_error_for_route_b(self):
        """経路Bの _on_realtime_error がコールバックを route_id='b' で呼ぶこと。"""
        callbacks = []

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=_make_route_config("b"),
                on_realtime_error=lambda r, c, t: callbacks.append((r, c, t)),
            )

        mcs.route_b_system._on_realtime_error("invalid_api_key: bad key")

        assert len(callbacks) == 1
        assert callbacks[0][0] == "b"            # route_id
        assert callbacks[0][1] == "auth"          # category
        assert "API キーが無効" in callbacks[0][2]

    def test_on_realtime_error_callback_not_required(self):
        """on_realtime_error を渡さなくても MultiCaptionSystem が正常にインスタンス化できること。"""
        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=_make_route_config("b"),
                # on_realtime_error を渡さない（デフォルト None）
            )

        # コールバックなしでも _on_realtime_error が例外を投げないこと
        mcs.route_a_system._on_realtime_error("some error")

    def test_on_realtime_error_callback_exception_does_not_propagate(self):
        """on_realtime_error コールバック自身が例外を投げても _on_realtime_error が安全に完了すること。"""
        def _bad_callback(r, c, t):
            raise RuntimeError("callback crashed")

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=_make_route_config("b"),
                on_realtime_error=_bad_callback,
            )

        # 例外が伝播しないこと
        mcs.route_a_system._on_realtime_error("some error")

    def test_on_realtime_error_not_called_when_no_error(self):
        """エラーが発生しないときにコールバックが呼ばれないこと（誤発火なし）。"""
        callbacks = []

        with patch("realtime_translator.RealtimeTranslator"):
            mcs = MultiCaptionSystem(
                config=_make_fake_config(),
                route_a=_make_route_config("a"),
                route_b=_make_route_config("b"),
                on_realtime_error=lambda r, c, t: callbacks.append((r, c, t)),
            )

        # _on_realtime_error を呼ばない状態ではコールバックは0件
        assert len(callbacks) == 0
