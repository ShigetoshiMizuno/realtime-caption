"""
realtime_translator.py

gpt-realtime-translate モデルを使った WebSocket ベースのリアルタイム翻訳クライアント。
Whisper + 翻訳APIの2段パイプラインを1モデルに圧縮する。

API ドキュメント確認情報:
  - 確認日: 2026-05-11
  - 参照元: SPECちゃん成果物（tranquil-stargazing-scott-agent-abc18dc2e2bbbcb06.md）
  - エンドポイント: wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate
  - イベント名（受信）:
      session.output_transcript.delta  - テキストチャンク受信
      session.output_transcript.done   - テキスト確定
  - イベント名（送信）:
      session.update                   - セッション設定更新
      session.input_audio_buffer.append - 音声チャンク送信
  - NOTE: gpt-realtime-translate は 2026 年リリース直後のため仕様変動の可能性あり。
    verbose ログ（RT_* イベント）で全受信メッセージを記録し、後日デバッグで確認できるようにしている。

コスト: USD 0.034/分（約 USD 2.04/時間）
"""

import asyncio
import base64
import concurrent.futures
import hashlib
import json
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

# 句読点: これで終わるバッファはフォールバック確定する
_SENTENCE_END_CHARS = frozenset("。！？.!?,;、")

# フォールバックタイマー: done が来ない場合、この秒数で強制確定
_FALLBACK_TIMEOUT_SEC = 3.0


class RealtimeTranslator:
    """
    gpt-realtime-translate WebSocket クライアント。

    使い方:
        translator = RealtimeTranslator(api_key="sk-...", target_language_code="ja")
        translator.start(asyncio_event_loop)
        translator.feed_audio(pcm16_bytes)  # スレッドセーフ
        translator.stop()
    """

    def __init__(
        self,
        api_key: str,
        target_language_code: str,
        model: str = "gpt-realtime-translate",
        connect_timeout: int = 10,
        reconnect_max_attempts: int = 5,
        reconnect_backoff_base: float = 1.5,
        on_transcript: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        request_audio_output: bool = False,
        on_audio_delta: Callable[[bytes], None] | None = None,
    ):
        """
        Parameters
        ----------
        api_key:                  OpenAI API キー
        target_language_code:     出力言語コード（BCP-47: "ja", "en" 等）
        model:                    使用モデル名（config で変更可能）
        connect_timeout:          接続タイムアウト（秒）
        reconnect_max_attempts:   最大再接続試行回数
        reconnect_backoff_base:   指数バックオフの底（n 回目は base^n 秒待機）
        on_transcript:            翻訳テキスト確定時コールバック (text: str) -> None
        on_error:                 エラー時コールバック (msg: str) -> None
        on_connected:             接続確立時コールバック () -> None
        request_audio_output:     True のとき session.update に audio.output.format=pcm16 を追加する
        on_audio_delta:           音声出力チャンクコールバック (pcm16_bytes: bytes) -> None
        """
        self._api_key = api_key
        self._target_language_code = target_language_code
        self._model = model
        self._connect_timeout = connect_timeout
        self._reconnect_max_attempts = reconnect_max_attempts
        self._reconnect_backoff_base = reconnect_backoff_base
        self._on_transcript = on_transcript
        self._on_error = on_error
        self._on_connected = on_connected
        self._request_audio_output = request_audio_output
        self._on_audio_delta = on_audio_delta

        # WebSocket エンドポイント（テスト時はこの属性を上書きする）
        self._ws_url = (
            f"wss://api.openai.com/v1/realtime/translations?model={model}"
        )

        self._loop: asyncio.AbstractEventLoop | None = None
        self._audio_queue: asyncio.Queue | None = None
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._future: "concurrent.futures.Future | None" = None
        self._thread: threading.Thread | None = None

        # テスト用フラグ（test_force_401: 最初の接続を 401 として扱う）
        self._test_force_401: bool = False

        # verboseログ用コールバック（main.py の _log_verbose と統合可能）
        self._verbose_callback: Callable[[str], None] | None = None

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        asyncio イベントループでWS接続タスクを起動する。
        main.py の CaptionSystem.run() の中で呼ぶ。
        ループが別スレッドで動いている場合は、そのループにタスクを投入する。
        """
        self._loop = loop

        def _bootstrap():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._connect_loop())

        # ループが既に動いているか確認
        if loop.is_running():
            # ループが既に動いているスレッドからタスク投入
            self._audio_queue = asyncio.Queue()
            self._stop_event = asyncio.Event()
            self._future = asyncio.run_coroutine_threadsafe(self._connect_loop_async(), loop)
        else:
            # 別スレッドでループを起動
            self._thread = threading.Thread(target=_bootstrap, daemon=True)
            self._thread.start()

    def feed_audio(self, pcm16_bytes: bytes) -> None:
        """
        24kHz PCM16 bytes を受け取り、WS 送信キューに積む。
        メインスレッドから呼ぶことを想定（スレッドセーフ）。
        """
        if self._loop is None or self._audio_queue is None:
            return
        if not self._loop.is_running():
            return
        self._loop.call_soon_threadsafe(self._audio_queue.put_nowait, pcm16_bytes)

    def stop(self) -> None:
        """WS 接続を切断してタスクをキャンセルする（idempotent）。"""
        if self._loop is None:
            return
        if self._stop_event is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._task is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._task.cancel)
        if self._future is not None and not self._future.done():
            self._future.cancel()

    # ------------------------------------------------------------------
    # 内部: ループ起動時の初期化
    # ------------------------------------------------------------------

    async def _connect_loop(self):
        """ループが起動していないケース用（別スレッドで run_until_complete される）。"""
        self._audio_queue = asyncio.Queue()
        self._stop_event = asyncio.Event()
        await self._connect_loop_async()

    async def _connect_loop_async(self):
        """接続・再接続ループ本体。"""
        attempt = 0
        while not self._stop_event.is_set():
            try:
                await self._run_session()
                # 正常終了なら停止
                break
            except asyncio.CancelledError:
                break
            except _AuthError as e:
                # 401 系: 再接続不要
                self._fire_error(f"認証エラー（再接続しません）: {e}")
                break
            except _RateLimitError:
                # 429: 60秒待機後に1回だけ再試行
                self._log_verbose("RT_RECONNECT", reason="429 rate limit", wait_sec=60)
                await asyncio.sleep(60)
                try:
                    await self._run_session()
                except Exception as e2:
                    self._fire_error(f"再試行も失敗: {e2}")
                break
            except Exception as e:
                if self._stop_event.is_set():
                    break
                if attempt >= self._reconnect_max_attempts:
                    self._fire_error(f"最大再接続回数に達しました（{attempt}回）: {e}")
                    break
                wait = self._reconnect_backoff_base ** attempt
                self._log_verbose(
                    "RT_RECONNECT",
                    attempt=attempt,
                    reason=str(e),
                    wait_sec=round(wait, 2),
                )
                await asyncio.sleep(wait)
                attempt += 1

    async def _run_session(self):
        """
        1セッション分の WebSocket 接続・送受信。
        切断されると例外を送出する（再接続は呼び出し元が担う）。
        """
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosedError

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Safety-Identifier": hashlib.sha256(
                self._api_key.encode()
            ).hexdigest(),
        }

        self._log_verbose("RT_CONNECT", url=self._ws_url)

        async with connect(
            self._ws_url,
            additional_headers=headers,
            open_timeout=self._connect_timeout,
        ) as ws:
            # テスト用 401 シミュレーション
            if self._test_force_401:
                raise _AuthError("test_force_401 flag")

            # セッション設定を送信
            audio_output_cfg: dict = {"language": self._target_language_code}
            if self._request_audio_output:
                audio_output_cfg["format"] = "pcm16"
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "audio": {
                        "output": audio_output_cfg
                    }
                }
            }))

            if self._on_connected:
                self._on_connected()

            self._log_verbose("RT_CONNECT", status="connected", lang=self._target_language_code)

            # recv と send を並走
            try:
                await asyncio.gather(
                    self._recv_loop(ws),
                    self._send_loop(ws),
                )
            except ConnectionClosedError as e:
                raise e
            except asyncio.CancelledError:
                raise

    async def _recv_loop(self, ws):
        """受信ループ: transcript delta/done を処理する。"""
        from websockets.exceptions import ConnectionClosedError

        buf = ""
        fallback_timer: asyncio.Task | None = None

        async def _start_fallback_timer():
            nonlocal fallback_timer
            if fallback_timer is not None and not fallback_timer.done():
                fallback_timer.cancel()
            fallback_timer = asyncio.create_task(_fallback_flush())

        async def _fallback_flush():
            """done が来なければ _FALLBACK_TIMEOUT_SEC 後に強制確定。"""
            nonlocal buf
            await asyncio.sleep(_FALLBACK_TIMEOUT_SEC)
            if buf:
                text = buf
                buf = ""
                self._log_verbose("RT_DONE", source="fallback_timer", text=text)
                if self._on_transcript:
                    self._on_transcript(text)

        try:
            async for raw in ws:
                if self._stop_event.is_set():
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    self._log_verbose("RT_ERROR", reason="json_decode_error", raw=str(raw)[:200])
                    continue

                event_type = msg.get("type", "")
                self._log_verbose("RT_RAW", type=event_type)

                if event_type == "session.output_transcript.delta":
                    delta = msg.get("delta", "")
                    buf += delta
                    self._log_verbose("RT_DELTA", delta=delta, buf_len=len(buf))

                    # フォールバックタイマーをリセット
                    await _start_fallback_timer()

                    # 句読点フォールバック確定
                    if buf and buf[-1] in _SENTENCE_END_CHARS:
                        if fallback_timer is not None and not fallback_timer.done():
                            fallback_timer.cancel()
                        text = buf
                        buf = ""
                        self._log_verbose("RT_DONE", source="punctuation", text=text)
                        if self._on_transcript:
                            self._on_transcript(text)

                elif event_type == "session.output_transcript.done":
                    if fallback_timer is not None and not fallback_timer.done():
                        fallback_timer.cancel()
                    text = buf
                    buf = ""
                    self._log_verbose("RT_DONE", source="done_event", text=text)
                    if text and self._on_transcript:
                        self._on_transcript(text)

                # NOTE: gpt-realtime-translate は 2026 年リリース直後のため、イベント名
                #       (session.output_audio.delta/done) は公式ドキュメント未確認
                #       (SPEC 文書類推)。仕様変動の可能性あり。
                #       実際のイベント名は verbose ログ (RT_RAW_UNKNOWN) で確認可能。
                elif event_type == "session.output_audio.delta":
                    delta_b64 = msg.get("delta", "")
                    if delta_b64 and self._on_audio_delta:
                        try:
                            pcm_bytes = base64.b64decode(delta_b64)
                            self._on_audio_delta(pcm_bytes)
                        except Exception as e:
                            self._log_verbose("RT_AUDIO_DELTA_ERROR", reason=str(e))

                elif event_type == "error":
                    code = msg.get("error", {}).get("code", "")
                    message = msg.get("error", {}).get("message", str(msg))
                    self._log_verbose("RT_ERROR", code=code, message=message)
                    if code in ("invalid_api_key", "authentication_error") or "401" in str(code):
                        raise _AuthError(message)
                    elif code in ("rate_limit_exceeded",) or "429" in str(code):
                        raise _RateLimitError(message)
                    else:
                        self._fire_error(f"サーバーエラー: {message}")

                else:
                    self._log_verbose("RT_RAW_UNKNOWN", type=event_type, msg=str(msg)[:200])

        except ConnectionClosedError as e:
            self._log_verbose("RT_ERROR", reason="connection_closed", detail=str(e))
            if fallback_timer is not None and not fallback_timer.done():
                fallback_timer.cancel()
            # buf に残りがあればフラッシュ
            if buf:
                self._log_verbose("RT_DONE", source="connection_closed_flush", text=buf)
                if self._on_transcript:
                    self._on_transcript(buf)
            raise
        finally:
            if fallback_timer is not None and not fallback_timer.done():
                fallback_timer.cancel()

    async def _send_loop(self, ws):
        """送信ループ: キューから PCM bytes を取り出して base64 エンコードして送信。"""
        while not self._stop_event.is_set():
            try:
                pcm_bytes = await asyncio.wait_for(
                    self._audio_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            audio_b64 = base64.b64encode(pcm_bytes).decode("utf-8")
            try:
                await ws.send(json.dumps({
                    "type": "session.input_audio_buffer.append",
                    "audio": audio_b64,
                }))
            except Exception:
                # 送信失敗は recv ループ側でも検知されるので握りつぶす
                break

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _fire_error(self, msg: str):
        """on_error コールバックを呼ぶ。"""
        self._log_verbose("RT_ERROR", message=msg)
        if self._on_error:
            self._on_error(msg)
        else:
            logger.error("[RealtimeTranslator] %s", msg)

    def _log_verbose(self, event: str, **fields):
        """verboseコールバックがあれば転送、なければ logger.debug に流す。"""
        if self._verbose_callback:
            self._verbose_callback(event, **fields)
        else:
            logger.debug("[%s] %s", event, fields)


# ------------------------------------------------------------------
# 内部例外
# ------------------------------------------------------------------

class _AuthError(Exception):
    """HTTP 401 / invalid_api_key 相当のエラー。再接続しない。"""


class _RateLimitError(Exception):
    """HTTP 429 / rate_limit_exceeded 相当のエラー。60秒待機後1回再試行。"""
