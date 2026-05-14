"""
tests/test_realtime_translator_reconnect.py

RealtimeTranslator の connect/disconnect API と再接続可能化のテスト (PR-5)。

- connect(loop) / disconnect() が正しく動作すること
- disconnect() 後の内部状態リセット（再 connect 可能化）
- start() / stop() が thin wrapper として動作すること
"""

import asyncio
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call

import pytest

from realtime_translator import RealtimeTranslator


class TestConnectDisconnectMethods:
    """connect() / disconnect() メソッドの基本テスト。"""

    def test_connect_method_exists(self):
        """connect(loop) メソッドが存在すること。"""
        assert hasattr(RealtimeTranslator, "connect"), "connect メソッドが存在しない"

    def test_disconnect_method_exists(self):
        """disconnect() メソッドが存在すること。"""
        assert hasattr(RealtimeTranslator, "disconnect"), "disconnect メソッドが存在しない"

    def test_start_is_thin_wrapper_for_connect(self):
        """start(loop) が connect(loop) を呼ぶ thin wrapper であること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-start-wrapper",
            target_language_code="ja",
        )
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False

        with patch.object(translator, "connect") as mock_connect:
            translator.start(mock_loop)
            mock_connect.assert_called_once_with(mock_loop)

    def test_stop_is_thin_wrapper_for_disconnect(self):
        """stop() が disconnect() を呼ぶ thin wrapper であること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-stop-wrapper",
            target_language_code="ja",
        )

        with patch.object(translator, "disconnect") as mock_disconnect:
            translator.stop()
            mock_disconnect.assert_called_once_with()


class TestDisconnectResetsInternalState:
    """disconnect() 後に内部状態がリセットされること。"""

    def test_disconnect_resets_internal_state(self):
        """
        disconnect() 後に _stop_event / _task / _future / _audio_queue が None。
        これにより次回 connect() が安全に再初期化できる。
        """
        translator = RealtimeTranslator(
            api_key="sk-test-fake-reset-state",
            target_language_code="ja",
        )

        # 内部状態を手動で設定してセットアップ（接続済み状態をシミュレート）
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        translator._loop = mock_loop

        mock_stop_event = MagicMock()
        translator._stop_event = mock_stop_event

        mock_task = MagicMock()
        translator._task = mock_task

        mock_future = MagicMock()
        mock_future.done.return_value = True
        translator._future = mock_future

        mock_queue = MagicMock()
        translator._audio_queue = mock_queue

        # disconnect() を呼ぶ
        translator.disconnect()

        # 内部状態がリセットされていること
        assert translator._stop_event is None, \
            f"_stop_event が None にリセットされていない: {translator._stop_event}"
        assert translator._task is None, \
            f"_task が None にリセットされていない: {translator._task}"
        assert translator._future is None, \
            f"_future が None にリセットされていない: {translator._future}"
        assert translator._audio_queue is None, \
            f"_audio_queue が None にリセットされていない: {translator._audio_queue}"

    def test_disconnect_when_not_connected_does_not_raise(self):
        """
        未接続状態（_loop=None）で disconnect() を呼んでもエラーにならないこと。
        """
        translator = RealtimeTranslator(
            api_key="sk-test-fake-no-connect",
            target_language_code="ja",
        )
        # _loop は None のまま
        assert translator._loop is None

        # 例外が発生しないこと
        translator.disconnect()

        # 内部状態が None のまま
        assert translator._stop_event is None
        assert translator._task is None
        assert translator._future is None
        assert translator._audio_queue is None


class TestConnectAfterDisconnect:
    """disconnect() の後に connect() で再接続できることのテスト。"""

    def test_connect_after_disconnect(self):
        """
        disconnect() → connect() で再接続できる。
        connect() 呼び出し後に _loop が設定されていること。
        """
        translator = RealtimeTranslator(
            api_key="sk-test-fake-reconnect-seq",
            target_language_code="ja",
        )

        # --- 1回目の接続をシミュレート ---
        mock_loop_1 = MagicMock()
        mock_loop_1.is_running.return_value = True

        mock_queue_1 = MagicMock()
        mock_event_1 = MagicMock()
        mock_future_1 = MagicMock()
        mock_future_1.done.return_value = True

        with patch("asyncio.Queue", return_value=mock_queue_1), \
             patch("asyncio.Event", return_value=mock_event_1), \
             patch("asyncio.run_coroutine_threadsafe", return_value=mock_future_1):
            translator.connect(mock_loop_1)

        assert translator._loop is mock_loop_1

        # --- disconnect() ---
        translator.disconnect()

        # 内部状態がリセットされたことを確認
        assert translator._stop_event is None
        assert translator._task is None
        assert translator._future is None
        assert translator._audio_queue is None

        # --- 2回目の接続（再接続）---
        mock_loop_2 = MagicMock()
        mock_loop_2.is_running.return_value = True

        mock_queue_2 = MagicMock()
        mock_event_2 = MagicMock()
        mock_future_2 = MagicMock()
        mock_future_2.done.return_value = False

        with patch("asyncio.Queue", return_value=mock_queue_2), \
             patch("asyncio.Event", return_value=mock_event_2), \
             patch("asyncio.run_coroutine_threadsafe", return_value=mock_future_2):
            translator.connect(mock_loop_2)

        # 2回目の接続後、内部状態が再設定されていること
        assert translator._loop is mock_loop_2, \
            "_loop が新しいループに更新されていない"
        assert translator._audio_queue is mock_queue_2, \
            "_audio_queue が再生成されていない"
        assert translator._stop_event is mock_event_2, \
            "_stop_event が再生成されていない"
        assert translator._future is mock_future_2, \
            "_future が再設定されていない"

    def test_connect_initializes_audio_queue_and_stop_event_when_loop_running(self):
        """
        connect() でループが稼働中の場合、_audio_queue と _stop_event が初期化されること。
        """
        translator = RealtimeTranslator(
            api_key="sk-test-fake-init-queue",
            target_language_code="ja",
        )

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        mock_queue = MagicMock()
        mock_event = MagicMock()
        mock_future = MagicMock()
        mock_future.done.return_value = False

        with patch("asyncio.Queue", return_value=mock_queue), \
             patch("asyncio.Event", return_value=mock_event), \
             patch("asyncio.run_coroutine_threadsafe", return_value=mock_future):
            translator.connect(mock_loop)

        assert translator._loop is mock_loop
        assert translator._audio_queue is mock_queue
        assert translator._stop_event is mock_event


class TestStartStopWrapperBehavior:
    """start() / stop() が thin wrapper として動作するテスト（後方互換）。"""

    def test_start_delegates_to_connect(self):
        """start(loop) は connect(loop) と同じ動作をすること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-start-delegates",
            target_language_code="ja",
        )

        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        mock_queue = MagicMock()
        mock_event = MagicMock()
        mock_future = MagicMock()
        mock_future.done.return_value = False

        with patch("asyncio.Queue", return_value=mock_queue), \
             patch("asyncio.Event", return_value=mock_event), \
             patch("asyncio.run_coroutine_threadsafe", return_value=mock_future):
            translator.start(mock_loop)

        assert translator._loop is mock_loop
        assert translator._audio_queue is mock_queue
        assert translator._stop_event is mock_event

    def test_stop_delegates_to_disconnect(self):
        """stop() は disconnect() と同じ動作をすること。"""
        translator = RealtimeTranslator(
            api_key="sk-test-fake-stop-delegates",
            target_language_code="ja",
        )

        # 接続済み状態をセットアップ
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        translator._loop = mock_loop

        mock_stop_event = MagicMock()
        translator._stop_event = mock_stop_event

        mock_future = MagicMock()
        mock_future.done.return_value = True
        translator._future = mock_future

        # stop() 呼び出し
        translator.stop()

        # disconnect() と同じ結果（内部状態がリセット）になること
        assert translator._stop_event is None, \
            "stop() 後に _stop_event が None になっていない"
        assert translator._future is None, \
            "stop() 後に _future が None になっていない"
