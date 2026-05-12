"""
tests/test_realtime_translator.py

RealtimeTranslator の単体テスト。
モック WebSocket サーバーを使って、各シナリオを検証する。

NOTE: gpt-realtime-translate API は 2026 年リリース直後のため、
      公式ドキュメント確認日時: 2026-05-11
      確認ソース: SPEC文書（tranquil-stargazing-scott-agent-abc18dc2e2bbbcb06.md）
      イベント名は session.output_transcript.delta / session.output_transcript.done を採用。
      実際のAPI動作は verbose ログ（RT_DELTA / RT_DONE イベント）で検証すること。
"""

import asyncio
import base64
import json
import threading
import time
import unittest

import pytest

# realtime_translator モジュールは実装後にインポート可能になる
# Red フェーズではインポートが失敗することを期待する
try:
    from realtime_translator import RealtimeTranslator
    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False
    RealtimeTranslator = None


# ---------------------------------------------------------------------------
# ヘルパー: モック WebSocket サーバー（websockets 15.x 対応）
# ---------------------------------------------------------------------------

async def _run_mock_ws_server(host, port, handler, stop_event):
    """指定ポートでモックWSサーバーを起動し、stop_event まで待つ。"""
    import websockets
    async with websockets.serve(handler, host, port):
        await stop_event.wait()


def _start_mock_server_in_thread(handler, port=19765):
    """モックWSサーバーを別スレッドで起動し、(loop, stop_event, thread) を返す。"""
    loop = asyncio.new_event_loop()
    stop_event = asyncio.Event()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_mock_ws_server("localhost", port, handler, stop_event))

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # サーバー起動待ち
    time.sleep(0.2)
    return loop, stop_event, t


def _stop_mock_server(loop, stop_event):
    """モックWSサーバーを停止する。"""
    loop.call_soon_threadsafe(stop_event.set)
    time.sleep(0.1)


# ---------------------------------------------------------------------------
# テストクラス
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="realtime_translator モジュール未実装")
class TestRealtimeTranslatorBasic:
    """基本的な delta/done フローのテスト。"""

    def test_delta_then_done_calls_on_transcript_once(self):
        """
        delta が複数届いた後に done が来ると、
        on_transcript が結合テキストで 1 回だけ呼ばれること。
        """
        received = []
        errors = []

        async def mock_handler(websocket):
            # クライアントからの session.update を受信
            msg = await asyncio.wait_for(websocket.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "session.update"

            # delta × 2 → done を送信
            await websocket.send(json.dumps({
                "type": "session.output_transcript.delta",
                "delta": "Hello, "
            }))
            await asyncio.sleep(0.05)
            await websocket.send(json.dumps({
                "type": "session.output_transcript.delta",
                "delta": "world!"
            }))
            await asyncio.sleep(0.05)
            await websocket.send(json.dumps({
                "type": "session.output_transcript.done",
            }))
            # 接続を維持（クライアントが切断するまで）
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19765)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test",
                target_language_code="ja",
                on_transcript=lambda text: received.append(text),
                on_error=lambda msg: errors.append(msg),
                reconnect_max_attempts=0,
            )
            # 内部URLをlocalhost:19765に上書き
            translator._ws_url = "ws://localhost:19765"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # 結果を待つ
            deadline = time.time() + 5
            while not received and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received) == 1, f"on_transcript は 1 回だけ呼ばれるべき。実際: {received}"
        assert received[0] == "Hello, world!", f"結合テキストが不一致: {received[0]!r}"
        assert errors == [], f"エラーが発生した: {errors}"

    def test_feed_audio_sends_base64_encoded(self):
        """
        feed_audio で渡した PCM bytes が base64 エンコードされて
        input_audio_buffer.append イベントとして送信されること。
        """
        received_messages = []

        async def mock_handler(websocket):
            # session.update を受信
            msg = await asyncio.wait_for(websocket.recv(), timeout=5)
            data = json.loads(msg)
            assert data["type"] == "session.update"

            # 音声メッセージを受信
            try:
                msg2 = await asyncio.wait_for(websocket.recv(), timeout=3)
                received_messages.append(json.loads(msg2))
            except asyncio.TimeoutError:
                pass

            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19766)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test",
                target_language_code="ja",
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19766"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # 接続確立まで少し待つ
            time.sleep(0.5)

            pcm_bytes = b"\x00\x01\x02\x03\x04\x05"
            translator.feed_audio(pcm_bytes)

            # 送信を待つ
            deadline = time.time() + 3
            while not received_messages and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received_messages) >= 1, "音声メッセージが送信されていない"
        audio_msg = received_messages[0]
        assert audio_msg["type"] == "session.input_audio_buffer.append", \
            f"イベントタイプが不一致: {audio_msg.get('type')!r}"
        expected_b64 = base64.b64encode(pcm_bytes).decode("utf-8")
        assert audio_msg["audio"] == expected_b64, \
            f"base64 エンコードが不一致: {audio_msg['audio']!r}"


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="realtime_translator モジュール未実装")
class TestRealtimeTranslatorError:
    """エラー処理のテスト。"""

    def test_401_calls_on_error_and_no_reconnect(self):
        """
        HTTP 401 を受信したら on_error が呼ばれ、
        再接続を試みないこと。
        """
        errors = []
        connect_count = [0]

        async def mock_handler(websocket):
            connect_count[0] += 1
            # 即座に接続を閉じる（401 相当のシミュレーション）
            await websocket.close(1008, "Unauthorized")

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19767)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test",
                target_language_code="ja",
                on_error=lambda msg: errors.append(msg),
                reconnect_max_attempts=3,
            )
            translator._ws_url = "ws://localhost:19767"
            # 401 シミュレーション: close code 1008 を 401 として扱うようにフラグ設定
            translator._test_force_401 = True

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # エラーコールバックを待つ
            deadline = time.time() + 3
            while not errors and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(errors) >= 1, "on_error が呼ばれなかった"
        # 401 で再接続しないこと（接続回数は1回のみ）
        assert connect_count[0] == 1, \
            f"401 受信後に再接続している（接続回数: {connect_count[0]}）"

    def test_reconnect_after_disconnect(self):
        """
        切断後に自動再接続（指数バックオフ）が実行されること。
        reconnect_backoff_base=0.01 を使って短時間でバックオフが完了するようにする。
        asyncio.sleep は patch せず、短いバックオフ値で実時間テストを行う。
        タイムアウトは 10 秒に設定（--cov 付き実行のオーバーヘッドを吸収するため）。
        """
        connect_count = [0]
        connected_event = threading.Event()

        async def mock_handler(websocket):
            connect_count[0] += 1
            if connect_count[0] == 1:
                # 1回目は即切断
                await websocket.close(1000, "test disconnect")
            else:
                # 2回目以降は維持
                connected_event.set()
                try:
                    await websocket.wait_closed()
                except Exception:
                    pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19768)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test",
                target_language_code="ja",
                reconnect_max_attempts=3,
                reconnect_backoff_base=0.01,  # テスト用に短縮（0.01^1 = 0.01秒待機）
            )
            translator._ws_url = "ws://localhost:19768"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # 再接続を待つ（バックオフ 0.01秒 + 余裕 10秒、--cov オーバーヘッドを考慮）
            assert connected_event.wait(timeout=10), "再接続タイムアウト"

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert connect_count[0] >= 2, \
            f"再接続が行われていない（接続回数: {connect_count[0]}）"


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="realtime_translator モジュール未実装")
class TestRealtimeTranslatorFallback:
    """フォールバック確定のテスト。"""

    def test_punctuation_triggers_transcript(self):
        """
        done イベントが来なくても、句読点（。）で終わる delta で確定すること。
        """
        received = []

        async def mock_handler(websocket):
            # session.update を受信
            await asyncio.wait_for(websocket.recv(), timeout=5)

            # done なしで、句読点終わりの delta だけ送信
            await websocket.send(json.dumps({
                "type": "session.output_transcript.delta",
                "delta": "こんにちは。"
            }))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19769)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test",
                target_language_code="ja",
                on_transcript=lambda text: received.append(text),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19769"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # フォールバック確定を待つ（3秒タイマーか句読点検出）
            deadline = time.time() + 5
            while not received and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received) >= 1, "句読点フォールバックで on_transcript が呼ばれなかった"
        assert "こんにちは。" in received[0], f"テキストが不一致: {received[0]!r}"


# ---------------------------------------------------------------------------
# モジュール不在時の明示的な失敗テスト（Red フェーズ確認用）
# ---------------------------------------------------------------------------

class TestModuleImport:
    """realtime_translator モジュールのインポートテスト（Red フェーズ確認用）。"""

    def test_module_can_be_imported(self):
        """realtime_translator モジュールがインポートできること。"""
        try:
            import realtime_translator
        except ImportError as e:
            pytest.fail(f"realtime_translator モジュールのインポートに失敗: {e}")

    def test_class_exists(self):
        """RealtimeTranslator クラスが存在すること。"""
        try:
            from realtime_translator import RealtimeTranslator
            assert RealtimeTranslator is not None
        except ImportError as e:
            pytest.fail(f"RealtimeTranslator クラスが存在しない: {e}")

    def test_constructor_signature(self):
        """RealtimeTranslator のコンストラクタが正しいシグネチャを持つこと。"""
        from realtime_translator import RealtimeTranslator
        import inspect
        sig = inspect.signature(RealtimeTranslator.__init__)
        params = set(sig.parameters.keys())
        required = {
            "api_key", "target_language_code", "model",
            "connect_timeout", "reconnect_max_attempts", "reconnect_backoff_base",
            "on_transcript", "on_error", "on_connected"
        }
        missing = required - params
        assert not missing, f"コンストラクタに必須パラメータが不足: {missing}"

    def test_public_methods_exist(self):
        """start / feed_audio / stop の公開メソッドが存在すること。"""
        from realtime_translator import RealtimeTranslator
        assert hasattr(RealtimeTranslator, "start"), "start メソッドが存在しない"
        assert hasattr(RealtimeTranslator, "feed_audio"), "feed_audio メソッドが存在しない"
        assert hasattr(RealtimeTranslator, "stop"), "stop メソッドが存在しない"
