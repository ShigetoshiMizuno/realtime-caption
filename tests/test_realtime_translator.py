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

    def test_constructor_has_audio_output_params(self):
        """
        コンストラクタに request_audio_output と on_audio_delta パラメータが
        存在すること。
        """
        from realtime_translator import RealtimeTranslator
        import inspect
        sig = inspect.signature(RealtimeTranslator.__init__)
        params = set(sig.parameters.keys())
        assert "request_audio_output" in params, "request_audio_output パラメータが存在しない"
        assert "on_audio_delta" in params, "on_audio_delta パラメータが存在しない"


# ---------------------------------------------------------------------------
# 音声出力テスト（Step 1: request_audio_output / on_audio_delta）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="realtime_translator モジュール未実装")
class TestRealtimeTranslatorAudioOutput:
    """音声出力（VB-CABLE 連携）テスト。"""

    def test_request_audio_output_false_does_not_include_format(self):
        """
        request_audio_output=False（デフォルト）のとき、
        session.update に pcm16 フォーマット指定が含まれないこと。
        """
        sent_messages = []

        async def mock_handler(websocket):
            msg = await asyncio.wait_for(websocket.recv(), timeout=5)
            sent_messages.append(json.loads(msg))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19770)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-audio-false",
                target_language_code="ja",
                request_audio_output=False,
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19770"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # session.update を待つ
            deadline = time.time() + 5
            while not sent_messages and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(sent_messages) >= 1, "session.update が送信されなかった"
        update_msg = sent_messages[0]
        assert update_msg.get("type") == "session.update"
        # request_audio_output=False のときは audio.output.format が設定されないこと
        audio_output = update_msg.get("session", {}).get("audio", {}).get("output", {})
        assert "format" not in audio_output, \
            f"request_audio_output=False のとき format が設定されてはいけない: {audio_output}"

    def test_request_audio_output_true_includes_pcm16_format(self):
        """
        request_audio_output=True のとき、
        session.update の audio.output に format: "pcm16" が含まれること。
        """
        sent_messages = []

        async def mock_handler(websocket):
            msg = await asyncio.wait_for(websocket.recv(), timeout=5)
            sent_messages.append(json.loads(msg))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19771)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-audio-true",
                target_language_code="ja",
                request_audio_output=True,
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19771"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # session.update を待つ
            deadline = time.time() + 5
            while not sent_messages and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(sent_messages) >= 1, "session.update が送信されなかった"
        update_msg = sent_messages[0]
        assert update_msg.get("type") == "session.update"
        audio_output = update_msg.get("session", {}).get("audio", {}).get("output", {})
        assert audio_output.get("format") == "pcm16", \
            f"request_audio_output=True のとき format=pcm16 が必要: {audio_output}"

    def test_output_audio_delta_calls_on_audio_delta(self):
        """
        session.output_audio.delta イベントを受信したとき、
        on_audio_delta コールバックに base64 デコードした bytes が渡されること。
        """
        received_audio = []
        pcm_data = b"\x10\x20\x30\x40\x50\x60"
        audio_b64 = base64.b64encode(pcm_data).decode("utf-8")

        async def mock_handler(websocket):
            # session.update を受信
            await asyncio.wait_for(websocket.recv(), timeout=5)

            # output_audio.delta を送信
            await websocket.send(json.dumps({
                "type": "session.output_audio.delta",
                "delta": audio_b64,
            }))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19772)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-audio-delta",
                target_language_code="ja",
                request_audio_output=True,
                on_audio_delta=lambda data: received_audio.append(data),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19772"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # コールバックを待つ
            deadline = time.time() + 5
            while not received_audio and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received_audio) >= 1, "on_audio_delta が呼ばれなかった"
        assert received_audio[0] == pcm_data, \
            f"デコードされた bytes が不一致: {received_audio[0]!r}"

    def test_output_audio_delta_not_called_when_no_callback(self):
        """
        on_audio_delta が None のとき、output_audio.delta を受信しても
        エラーにならないこと（サイレント無視）。
        """
        errors = []
        delta_processed = threading.Event()

        pcm_data = b"\xAA\xBB"
        audio_b64 = base64.b64encode(pcm_data).decode("utf-8")

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.output_audio.delta",
                "delta": audio_b64,
            }))
            # transcript も送信して処理完了を検出できるようにする
            await asyncio.sleep(0.1)
            await websocket.send(json.dumps({
                "type": "session.output_transcript.delta",
                "delta": "test.",
            }))
            await asyncio.sleep(0.1)
            delta_processed.set()
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19773)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-no-callback",
                target_language_code="ja",
                request_audio_output=True,
                on_audio_delta=None,  # コールバックなし
                on_error=lambda msg: errors.append(msg),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19773"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # 処理完了を待つ
            delta_processed.wait(timeout=5)
            time.sleep(0.2)  # 処理の完了を確実に待つ

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert errors == [], f"on_audio_delta=None のときエラーが発生してはいけない: {errors}"


# ---------------------------------------------------------------------------
# session.input_transcript.delta / .done テスト（Issue #23）
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="realtime_translator モジュール未実装")
class TestRealtimeTranslatorSourceTranscript:
    """
    gpt-realtime-translate が返す原文（入力側トランスクリプト）の受信テスト。

    API ドキュメント（確認日 2026-05-12）に基づくイベント名:
      session.input_transcript.delta  — 原文 delta チャンク
      session.input_transcript.done   — 原文確定
    """

    def test_constructor_has_on_source_transcript_param(self):
        """
        RealtimeTranslator のコンストラクタに on_source_transcript パラメータが
        存在すること。
        """
        import inspect
        sig = inspect.signature(RealtimeTranslator.__init__)
        params = set(sig.parameters.keys())
        assert "on_source_transcript" in params, \
            "on_source_transcript パラメータがコンストラクタに存在しない"

    def test_input_transcript_delta_accumulates_buffer(self):
        """
        session.input_transcript.delta が複数届いたとき、
        done イベントまでコールバックを呼ばず、
        done で結合テキストとして on_source_transcript が呼ばれること。
        """
        received_source = []
        received_translation = []

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            # 原文 delta × 2
            await websocket.send(json.dumps({
                "type": "session.input_transcript.delta",
                "delta": "Hello, ",
            }))
            await asyncio.sleep(0.05)
            await websocket.send(json.dumps({
                "type": "session.input_transcript.delta",
                "delta": "world!",
            }))
            await asyncio.sleep(0.05)
            await websocket.send(json.dumps({
                "type": "session.input_transcript.done",
            }))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19774)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-source-delta",
                target_language_code="ja",
                on_transcript=lambda t: received_translation.append(t),
                on_source_transcript=lambda t: received_source.append(t),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19774"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            deadline = time.time() + 5
            while not received_source and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received_source) == 1, \
            f"on_source_transcript は done で 1 回だけ呼ばれるべき: {received_source}"
        assert received_source[0] == "Hello, world!", \
            f"原文テキストが不一致: {received_source[0]!r}"
        # 翻訳コールバックは呼ばれていないこと（原文と翻訳は独立）
        assert received_translation == [], \
            f"翻訳コールバックが誤って呼ばれた: {received_translation}"

    def test_input_transcript_done_calls_callback(self):
        """
        session.input_transcript.done イベントで on_source_transcript が呼ばれること。
        """
        received_source = []

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.input_transcript.delta",
                "delta": "おはようございます",
            }))
            await asyncio.sleep(0.05)
            await websocket.send(json.dumps({
                "type": "session.input_transcript.done",
            }))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19775)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-source-done",
                target_language_code="en",
                on_source_transcript=lambda t: received_source.append(t),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19775"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            deadline = time.time() + 5
            while not received_source and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received_source) >= 1, "on_source_transcript が呼ばれなかった"
        assert received_source[0] == "おはようございます", \
            f"原文テキストが不一致: {received_source[0]!r}"

    def test_input_transcript_punctuation_fallback(self):
        """
        session.input_transcript.done が来なくても、
        句読点（。）で終わる delta でフォールバック確定すること。
        """
        received_source = []

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.input_transcript.delta",
                "delta": "こんにちは。",
            }))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19776)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-source-punctuation",
                target_language_code="en",
                on_source_transcript=lambda t: received_source.append(t),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19776"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            deadline = time.time() + 5
            while not received_source and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received_source) >= 1, \
            "句読点フォールバックで on_source_transcript が呼ばれなかった"
        assert "こんにちは。" in received_source[0], \
            f"テキストが不一致: {received_source[0]!r}"

    def test_on_transcript_and_on_source_transcript_independent(self):
        """
        on_transcript（翻訳）と on_source_transcript（原文）が独立して動作すること:
        - output_transcript.done → on_transcript が呼ばれる
        - input_transcript.done → on_source_transcript が呼ばれる
        - それぞれ相手のコールバックは呼ばれない
        """
        received_source = []
        received_translation = []

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            # 翻訳テキスト
            await websocket.send(json.dumps({
                "type": "session.output_transcript.delta",
                "delta": "Good morning.",
            }))
            await asyncio.sleep(0.05)
            await websocket.send(json.dumps({
                "type": "session.output_transcript.done",
            }))
            await asyncio.sleep(0.05)
            # 原文テキスト
            await websocket.send(json.dumps({
                "type": "session.input_transcript.delta",
                "delta": "おはよう。",
            }))
            await asyncio.sleep(0.05)
            await websocket.send(json.dumps({
                "type": "session.input_transcript.done",
            }))
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19777)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-independent",
                target_language_code="en",
                on_transcript=lambda t: received_translation.append(t),
                on_source_transcript=lambda t: received_source.append(t),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19777"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            # 両方のコールバックを待つ
            deadline = time.time() + 5
            while (not received_source or not received_translation) and time.time() < deadline:
                time.sleep(0.1)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert len(received_translation) >= 1, \
            f"on_transcript が呼ばれなかった: {received_translation}"
        assert received_translation[0] == "Good morning.", \
            f"翻訳テキストが不一致: {received_translation[0]!r}"

        assert len(received_source) >= 1, \
            f"on_source_transcript が呼ばれなかった: {received_source}"
        assert received_source[0] == "おはよう。", \
            f"原文テキストが不一致: {received_source[0]!r}"

    def test_on_source_transcript_none_does_not_raise(self):
        """
        on_source_transcript=None のとき、input_transcript イベントを受信しても
        エラーにならないこと（サイレント無視）。
        """
        errors = []
        processed = threading.Event()

        async def mock_handler(websocket):
            await asyncio.wait_for(websocket.recv(), timeout=5)

            await websocket.send(json.dumps({
                "type": "session.input_transcript.delta",
                "delta": "テスト",
            }))
            await asyncio.sleep(0.1)
            await websocket.send(json.dumps({
                "type": "session.input_transcript.done",
            }))
            await asyncio.sleep(0.1)
            processed.set()
            try:
                await websocket.wait_closed()
            except Exception:
                pass

        server_loop, stop_event, _ = _start_mock_server_in_thread(mock_handler, port=19778)

        try:
            translator = RealtimeTranslator(
                api_key="sk-test-fake-source-none",
                target_language_code="en",
                on_source_transcript=None,
                on_error=lambda msg: errors.append(msg),
                reconnect_max_attempts=0,
            )
            translator._ws_url = "ws://localhost:19778"

            client_loop = asyncio.new_event_loop()
            translator.start(client_loop)

            processed.wait(timeout=5)
            time.sleep(0.2)

            translator.stop()
        finally:
            _stop_mock_server(server_loop, stop_event)

        assert errors == [], \
            f"on_source_transcript=None のときエラーが発生してはいけない: {errors}"
