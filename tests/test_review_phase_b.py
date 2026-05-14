"""
tests/test_review_phase_b.py

QA + codex レビュー Phase B 高優先度 5 件の TDD テスト。

C-2: RealtimeTranslator.disconnect() の future/timer cleanup 漏れ
C-4: 動的入力デバイス変更時の recorder state クリア (set_input_device 公開メソッド)
W-2: _stop_event_async 未初期化のまま stop() スキップ問題
W-3: RealtimeTranslator._thread の join 漏れ
W-4: set_output_device() の standalone PyAudio リーク
"""

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from realtime_translator import RealtimeTranslator
from main import CaptionSystem, RouteState, AudioStats


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_minimal_caption_system(route_id: str = "test") -> CaptionSystem:
    """object.__new__ で最小限の CaptionSystem を作るヘルパー。"""
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
    cs._audio_stream_lock = threading.Lock()
    cs._output_device_index = None
    cs._output_volume = 1.0
    cs._audio_output_mode = False
    cs._pa_instance = None
    cs._realtime_mode = True
    return cs


def _make_fake_config() -> dict:
    return {
        "translation": {"translation_model": "openai-realtime"},
        "openai": {"api_key": "sk-test-fake-0000000000000000"},
        "openai_realtime": {
            "target_language_code": "ja",
            "model": "gpt-realtime-translate",
            "connect_timeout": 10,
            "reconnect_max_attempts": 5,
            "reconnect_backoff_base": 1.5,
            "max_session_minutes": 60,
            "audio_output": {},
        },
        "output": {"log_dir": "."},
    }


# ---------------------------------------------------------------------------
# C-2: disconnect() が _future.cancel() を呼ぶこと
# ---------------------------------------------------------------------------

class TestDisconnectCleansFutureAndTimers:
    """C-2: RealtimeTranslator.disconnect() が _future cancel を確実に行うこと。

    背景: disconnect() は _task.cancel() を呼ぶが、run_coroutine_threadsafe の戻り値
    である _future をキャンセルしないとバックグラウンドスレッドがリークする可能性がある。
    """

    def test_disconnect_cancels_undone_future(self):
        """disconnect() を呼ぶと、未完了の _future に cancel() が呼ばれること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-c2-future-cancel",
            target_language_code="ja",
        )

        # 接続済み状態をシミュレート: _future が未完了
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        translator._loop = mock_loop

        mock_future = MagicMock()
        mock_future.done.return_value = False  # 未完了
        translator._future = mock_future

        translator.disconnect()

        # _future.cancel() が呼ばれていること
        mock_future.cancel.assert_called_once()

    def test_disconnect_does_not_cancel_done_future(self):
        """disconnect() を呼ぶとき、既完了の _future には cancel() を呼ばないこと。
        （呼んでも無害だが、現実装で done() をチェックして cancel している設計を確認）
        """
        translator = RealtimeTranslator(
            api_key="sk-test-fake-c2-done-future",
            target_language_code="ja",
        )

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        translator._loop = mock_loop

        mock_future = MagicMock()
        mock_future.done.return_value = True  # 既に完了済み
        translator._future = mock_future

        translator.disconnect()

        # done() が True なら cancel() を呼ばないこと
        mock_future.cancel.assert_not_called()

    def test_disconnect_resets_future_to_none(self):
        """disconnect() 後に _future が None にリセットされること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-c2-reset-future",
            target_language_code="ja",
        )

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        translator._loop = mock_loop

        mock_future = MagicMock()
        mock_future.done.return_value = False
        translator._future = mock_future

        translator.disconnect()

        assert translator._future is None, \
            f"disconnect() 後に _future が None にリセットされていない: {translator._future}"


# ---------------------------------------------------------------------------
# W-3: disconnect() が _thread を join すること
# ---------------------------------------------------------------------------

class TestDisconnectJoinsThread:
    """W-3: RealtimeTranslator.disconnect() が _thread を join すること。

    背景: connect(loop) で別スレッドでループを起動する場合、
    disconnect() でこのスレッドを join しないとスレッドがリークする。
    """

    def test_disconnect_joins_alive_thread(self):
        """disconnect() 後に稼働中の _thread が join されること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-w3-thread-join",
            target_language_code="ja",
        )

        # _thread が稼働中であることをシミュレート
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = True
        translator._thread = mock_thread

        translator.disconnect()

        # join が呼ばれていること（タイムアウト付き）
        mock_thread.join.assert_called_once()
        call_kwargs = mock_thread.join.call_args
        # timeout 引数が指定されていること
        timeout_val = (
            call_kwargs.kwargs.get("timeout")
            or (call_kwargs.args[0] if call_kwargs.args else None)
        )
        assert timeout_val is not None, (
            "disconnect() の _thread.join() にタイムアウトが指定されていない"
        )

    def test_disconnect_resets_thread_to_none(self):
        """disconnect() 後に _thread が None にリセットされること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-w3-thread-none",
            target_language_code="ja",
        )

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False  # 既に停止済み
        translator._thread = mock_thread

        translator.disconnect()

        assert translator._thread is None, \
            f"disconnect() 後に _thread が None にリセットされていない: {translator._thread}"

    def test_disconnect_does_not_join_dead_thread(self):
        """disconnect() を呼ぶとき、既に停止済みスレッドの join を呼ばないこと。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-w3-dead-thread",
            target_language_code="ja",
        )

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False  # 停止済み
        translator._thread = mock_thread

        translator.disconnect()

        # is_alive() が False なら join() を呼ばないこと
        mock_thread.join.assert_not_called()


# ---------------------------------------------------------------------------
# C-4: CaptionSystem.set_input_device() 公開メソッド
# ---------------------------------------------------------------------------

class TestSetInputDeviceClearsRecorder:
    """C-4: CaptionSystem.set_input_device() 公開メソッドが存在し、
    recorder クリアと stop/start 再起動を正しく行うこと。

    背景: app.py の _on_route_a_device_change でプライベート属性
    _device_info を直書きしている。公開メソッドを通じて安全にデバイス変更する。
    """

    def test_set_input_device_method_exists(self):
        """CaptionSystem に set_input_device(device_info) メソッドが存在すること。"""
        assert hasattr(CaptionSystem, "set_input_device"), \
            "CaptionSystem に set_input_device メソッドが存在しない"
        assert callable(CaptionSystem.set_input_device), \
            "set_input_device が callable でない"

    def test_set_input_device_updates_device_info(self):
        """set_input_device() が _device_info を新しいデバイスに更新すること。"""
        cs = _make_minimal_caption_system()
        cs._device_info = {"index": 0, "name": "OldDevice"}

        new_device = {"index": 1, "name": "NewDevice"}
        cs.set_input_device(new_device)

        assert cs._device_info == new_device, \
            f"_device_info が更新されていない: {cs._device_info}"

    def test_set_input_device_clears_recorder_when_idle(self):
        """停止中に set_input_device() を呼ぶと _recorder が None クリアされること。"""
        cs = _make_minimal_caption_system()
        cs._device_info = {"index": 0, "name": "OldDevice"}

        # recorder が存在する状態をシミュレート
        mock_recorder = MagicMock()
        cs._recorder = mock_recorder

        new_device = {"index": 1, "name": "NewDevice"}
        cs.set_input_device(new_device)

        # recorder がクリアされていること（次回 start() で再生成される）
        assert cs._recorder is None, \
            f"set_input_device() 後に _recorder がクリアされていない: {cs._recorder}"

    def test_set_input_device_restarts_when_running(self):
        """稼働中 (RUNNING) に set_input_device() を呼ぶと stop → start が実行されること。"""
        cs = _make_minimal_caption_system()
        cs._device_info = {"index": 0, "name": "OldDevice"}
        cs._state = RouteState.RUNNING

        stop_called = []
        start_called = []

        def mock_stop():
            stop_called.append(1)
            cs._state = RouteState.IDLE

        def mock_start():
            start_called.append(1)
            cs._state = RouteState.RUNNING

        cs.stop = mock_stop
        cs.start = mock_start

        new_device = {"index": 2, "name": "AnotherDevice"}
        cs.set_input_device(new_device)

        assert stop_called, "稼働中に set_input_device() したのに stop() が呼ばれなかった"
        assert start_called, "稼働中に set_input_device() したのに start() が呼ばれなかった"
        assert cs._device_info == new_device, \
            f"_device_info が更新されていない: {cs._device_info}"

    def test_set_input_device_does_not_restart_when_idle(self):
        """停止中 (IDLE) に set_input_device() を呼ぶと stop/start は実行されないこと。"""
        cs = _make_minimal_caption_system()
        cs._device_info = {"index": 0, "name": "OldDevice"}
        cs._state = RouteState.IDLE

        stop_called = []
        start_called = []
        cs.stop = lambda: stop_called.append(1)
        cs.start = lambda: start_called.append(1)

        new_device = {"index": 3, "name": "IdleDevice"}
        cs.set_input_device(new_device)

        assert not stop_called, "停止中なのに stop() が呼ばれた"
        assert not start_called, "停止中なのに start() が呼ばれた"


# ---------------------------------------------------------------------------
# W-2: _stop_event_async が None でも stop() が安全に動作すること
# ---------------------------------------------------------------------------

class TestStopWithUninitializedAsyncEvent:
    """W-2: start() が失敗して state=ERROR になっても、stop() が安全に動作すること。

    背景: start() が失敗（API キー未設定等）して state=ERROR になっても、
    _stop_event_async は None のまま。次回 stop() 時に安全に動くことを確認。
    """

    def test_stop_with_none_stop_event_async_is_safe(self):
        """_stop_event_async が None の状態で stop() を呼んでも例外が発生しないこと。"""
        cs = _make_minimal_caption_system()
        # start() 失敗後の状態をシミュレート: _stop_event_async は None のまま
        cs._stop_event_async = None
        cs._loop = None
        cs._state = RouteState.ERROR

        # 例外が発生しないこと
        try:
            cs.stop()
        except Exception as e:
            pytest.fail(f"_stop_event_async=None での stop() が例外を発生させた: {e}")

    def test_stop_does_not_access_none_stop_event_async(self):
        """stop() の実装で _stop_event_async が None かどうかを確認してからアクセスすること。

        ソースコードの静的解析または動作テストで確認する。
        _loop と _stop_event_async を組み合わせた None チェックが存在すること。
        """
        import inspect
        import ast
        import textwrap

        source = inspect.getsource(CaptionSystem.stop)
        source = textwrap.dedent(source)

        # "_stop_event_async" へのアクセスに何らかの None チェックがあること
        # パターン1: "if self._loop and self._stop_event_async:"
        # パターン2: "if self._stop_event_async is not None:"
        # パターン3: getattr パターン
        has_null_guard = (
            "self._stop_event_async" in source
            and (
                "if self._loop and self._stop_event_async" in source
                or "if self._stop_event_async" in source
                or "getattr" in source
            )
        )

        assert has_null_guard, (
            "stop() の実装に _stop_event_async の None チェックが見つからない（W-2 未対応の可能性）\n"
            f"ソース抜粋:\n{source[:1000]}"
        )

    def test_stop_with_error_state_transitions_to_idle(self):
        """state=ERROR で stop() を呼ぶと IDLE に遷移すること。"""
        cs = _make_minimal_caption_system()
        cs._stop_event_async = None
        cs._loop = None
        cs._state = RouteState.ERROR

        # stop() を呼んで IDLE に戻ること
        cs.stop()
        assert cs.state == RouteState.IDLE, \
            f"stop() 後に state が IDLE にならなかった: {cs.state}"


# ---------------------------------------------------------------------------
# W-4: set_output_device() で standalone PyAudio がリークしないこと
# ---------------------------------------------------------------------------

class TestSetOutputDeviceDoesNotLeakStandalonePa:
    """W-4: set_output_device() で生成した standalone PyAudio が
    AudioOutputStream.stop() で確実に terminate されること。

    背景: set_output_device(int) で _pa_instance が None の場合、
    pyaudio.PyAudio() を新規生成して AudioOutputStream(owns_pa=True) に渡す。
    AudioOutputStream.stop() が owns_pa=True なら pa.terminate() を呼ぶことを確認。
    """

    def test_audio_output_stream_stop_terminates_pa_when_owns_pa_true(self):
        """AudioOutputStream(owns_pa=True).stop() が pa.terminate() を呼ぶこと。"""
        from audio_output import AudioOutputStream

        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 2,
            "defaultSampleRate": 48000,
        }

        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=0,
            owns_pa=True,
        )
        stream.start()
        stream.stop()

        # terminate() が呼ばれていること
        mock_pa.terminate.assert_called_once()

    def test_audio_output_stream_stop_does_not_terminate_pa_when_owns_pa_false(self):
        """AudioOutputStream(owns_pa=False).stop() が pa.terminate() を呼ばないこと。

        共有 PyAudio の場合は terminate しない（MultiCaptionSystem が管理）。
        """
        from audio_output import AudioOutputStream

        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 2,
            "defaultSampleRate": 48000,
        }

        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=0,
            owns_pa=False,
        )
        stream.start()
        stream.stop()

        # terminate() が呼ばれないこと
        mock_pa.terminate.assert_not_called()

    def test_caption_system_set_output_device_uses_owns_pa_true_when_no_shared_pa(self):
        """CaptionSystem.set_output_device() で _pa_instance が None の場合、
        AudioOutputStream が owns_pa=True で生成されること。

        これにより stop() で standalone PyAudio が確実に terminate される。
        """
        from audio_output import AudioOutputStream

        cs = _make_minimal_caption_system()
        cs._pa_instance = None  # 共有 PA なし

        created_streams = []

        original_init = AudioOutputStream.__init__

        def mock_stream_init(self_stream, *args, **kwargs):
            created_streams.append(kwargs.get("owns_pa", True))
            original_init(self_stream, *args, **kwargs)

        mock_pa = MagicMock()
        mock_pa_stream = MagicMock()
        mock_pa.open.return_value = mock_pa_stream
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 2,
            "defaultSampleRate": 48000,
        }

        with (
            patch("main.pyaudio.PyAudio", return_value=mock_pa),
            patch.object(AudioOutputStream, "__init__", mock_stream_init),
            patch.object(AudioOutputStream, "start"),
        ):
            cs.set_output_device(0)

        assert created_streams, "AudioOutputStream が生成されなかった"
        assert created_streams[0] is True, (
            f"_pa_instance が None のとき owns_pa=True で生成されるべき: "
            f"owns_pa={created_streams[0]}"
        )


# ---------------------------------------------------------------------------
# W-1: set_input_device() が start() の例外を飲み込み、app.py コールバックも防御すること
# ---------------------------------------------------------------------------

class TestSetInputDeviceSwallowsStartException:
    """W-1: CaptionSystem.set_input_device() で start() が例外を発生させても
    呼び出し元に伝播しないこと。

    背景: was_running=True で stop() → _device_info 更新 → start() の順で処理されるが、
    start() が例外を raise した場合、_device_info は新値に更新済みで state は ERROR のまま残る。
    呼び出し元の GUI コールバックに例外が伝播するとクラッシュするため、
    set_input_device() 内で例外を握ること。
    """

    def test_set_input_device_swallows_start_exception(self):
        """stop後 start() が例外を発生させても set_input_device() は例外を re-raise しないこと。"""
        cs = _make_minimal_caption_system()
        cs._device_info = {"index": 0, "name": "OldDevice"}
        cs._state = RouteState.RUNNING

        def mock_stop():
            cs._state = RouteState.IDLE

        def mock_start():
            cs._state = RouteState.ERROR
            raise RuntimeError("API key not set (fake error for test)")

        cs.stop = mock_stop
        cs.start = mock_start

        new_device = {"index": 2, "name": "NewDevice"}

        # 例外が re-raise されないこと（ここで例外が出たらテスト失敗）
        try:
            cs.set_input_device(new_device)
        except Exception as e:
            pytest.fail(
                f"set_input_device() が start() の例外を呼び出し元に伝播させた: {type(e).__name__}: {e}"
            )

    def test_set_input_device_device_info_updated_even_if_start_fails(self):
        """start() が例外を発生させても _device_info は新値に更新済みであること。"""
        cs = _make_minimal_caption_system()
        cs._device_info = {"index": 0, "name": "OldDevice"}
        cs._state = RouteState.RUNNING

        def mock_stop():
            cs._state = RouteState.IDLE

        def mock_start():
            cs._state = RouteState.ERROR
            raise RuntimeError("API key not set (fake error for test)")

        cs.stop = mock_stop
        cs.start = mock_start

        new_device = {"index": 2, "name": "NewDevice"}
        cs.set_input_device(new_device)

        assert cs._device_info == new_device, (
            f"start() 失敗後も _device_info は新値であるべき: {cs._device_info}"
        )

    def test_set_input_device_state_is_error_after_start_fails(self):
        """start() が例外を発生させた後、state が ERROR のままであること。"""
        cs = _make_minimal_caption_system()
        cs._device_info = {"index": 0, "name": "OldDevice"}
        cs._state = RouteState.RUNNING

        def mock_stop():
            cs._state = RouteState.IDLE

        def mock_start():
            cs._state = RouteState.ERROR
            raise RuntimeError("API key not set (fake error for test)")

        cs.stop = mock_stop
        cs.start = mock_start

        new_device = {"index": 2, "name": "NewDevice"}
        cs.set_input_device(new_device)

        assert cs.state == RouteState.ERROR, (
            f"start() 失敗後の state は ERROR であるべき: {cs.state}"
        )


class TestRouteDeviceChangeCallbackHandlesException:
    """W-1 (app.py 側): _on_route_a/b_device_change コールバックが
    set_input_device() の例外を握って GUI スレッドにクラッシュさせないこと。

    背景: set_input_device() 内で例外を飲み込む修正をするが、
    app.py のコールバックにも防御的 try/except を追加して多層防御とする。
    """

    def test_route_a_device_change_callback_handles_exception(self):
        """_on_route_a_device_change が set_input_device() の例外を握ること。

        app.py はモジュールレベルのグローバル関数なので、
        関数のソースに try/except が含まれることを静的確認する。
        """
        import inspect
        import app as app_module

        source = inspect.getsource(app_module._on_route_a_device_change)

        assert "try" in source and "except" in source, (
            "_on_route_a_device_change に try/except が含まれていない (W-1 app.py 未対応)\n"
            f"ソース:\n{source}"
        )

    def test_route_b_device_change_callback_handles_exception(self):
        """_on_route_b_device_change が set_input_device() の例外を握ること。"""
        import inspect
        import app as app_module

        source = inspect.getsource(app_module._on_route_b_device_change)

        assert "try" in source and "except" in source, (
            "_on_route_b_device_change に try/except が含まれていない (W-1 app.py 未対応)\n"
            f"ソース:\n{source}"
        )
