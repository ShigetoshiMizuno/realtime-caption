"""
audio_output.py

VB-CABLE 等の仮想オーディオデバイスに翻訳音声を出力するクラス。
pyaudio の output stream + queue.Queue + daemon thread で構成する。

使い方:
    import pyaudiowpatch as pyaudio
    pa = pyaudio.PyAudio()
    stream = AudioOutputStream(pyaudio_instance=pa, device_index=idx, sample_rate=24000)
    stream.start()
    stream.write(pcm16_bytes)
    stream.stop()
    pa.terminate()
"""

import queue
import threading
import logging

logger = logging.getLogger(__name__)

# デフォルトサンプルレート: gpt-realtime-translate の出力は 24kHz PCM16
_DEFAULT_SAMPLE_RATE = 24000
_DEFAULT_CHANNELS = 1
_DEFAULT_CHUNK = 1024


class AudioOutputStream:
    """
    PCM16 bytes を受け取り、pyaudio output stream に非同期で書き込むクラス。

    スレッドセーフな write() を提供し、daemon スレッドがキューを消費して
    pyaudio ストリームに書き込む。
    """

    def __init__(
        self,
        pyaudio_instance=None,
        device_index: int | None = None,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        channels: int = _DEFAULT_CHANNELS,
        chunk_size: int = _DEFAULT_CHUNK,
    ):
        """
        Parameters
        ----------
        pyaudio_instance:  pyaudio.PyAudio インスタンス（テスト時はモック）
        device_index:      出力デバイスインデックス（None で pyaudio デフォルト）
        sample_rate:       サンプルレート（Hz）。gpt-realtime-translate は 24000
        channels:          チャンネル数
        chunk_size:        1回の write サイズ（bytes）
        """
        self._pa = pyaudio_instance
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_size = chunk_size

        self._queue: queue.Queue = queue.Queue()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False

    def start(self) -> None:
        """
        pyaudio output stream を開き、キュー消費スレッドを起動する。
        既に起動済みの場合は何もしない。
        """
        if self._started:
            return

        try:
            kwargs: dict = {
                "format": self._get_pyaudio_format(),
                "channels": self._channels,
                "rate": self._sample_rate,
                "output": True,
                "frames_per_buffer": self._chunk_size,
            }
            if self._device_index is not None:
                kwargs["output_device_index"] = self._device_index

            self._stream = self._pa.open(**kwargs)
        except Exception as e:
            logger.error("[AudioOutputStream] ストリームのオープンに失敗: %s", e)
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._drain_loop,
            daemon=True,
            name="AudioOutputStream-drain",
        )
        self._thread.start()
        self._started = True
        logger.info(
            "[AudioOutputStream] 開始: device_index=%s rate=%d",
            self._device_index,
            self._sample_rate,
        )

    def write(self, pcm16_bytes: bytes) -> None:
        """
        PCM16 bytes をキューに積む（スレッドセーフ）。
        stop() 後に呼ばれた場合は黙殺する。
        """
        if self._stop_event.is_set():
            return
        self._queue.put(pcm16_bytes)

    def stop(self) -> None:
        """
        ストリームを停止してスレッドを終了する（idempotent）。
        """
        if self._stop_event.is_set():
            return
        self._stop_event.set()

        # スレッドが wait_for でブロックしているのを解除するためダミーを積む
        try:
            self._queue.put_nowait(b"")
        except Exception:
            pass

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None

        if self._stream is not None:
            try:
                self._stream.stop_stream()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        self._started = False
        logger.info("[AudioOutputStream] 停止")

    def find_device_index(self, name_keyword: str) -> int | None:
        """
        デバイス名の部分一致（大小文字無視）で出力デバイスのインデックスを返す。
        見つからない場合は None を返す。

        Parameters
        ----------
        name_keyword:  デバイス名の一部（"CABLE Input" 等）
        """
        if self._pa is None:
            return None

        keyword_lower = name_keyword.lower()
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info.get("maxOutputChannels", 0) <= 0:
                continue
            if keyword_lower in info.get("name", "").lower():
                return i
        return None

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _drain_loop(self) -> None:
        """
        キューからデータを取り出して pyaudio ストリームに書き込む
        daemon スレッドのメインループ。
        """
        while not self._stop_event.is_set():
            try:
                data = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if not data:
                # stop() が積んだダミー or 空フレーム
                continue

            if self._stream is None:
                continue

            try:
                self._stream.write(data)
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning("[AudioOutputStream] 書き込みエラー: %s", e)

    def _get_pyaudio_format(self) -> int:
        """pyaudio の paInt16 定数を返す。"""
        # pyaudiowpatch と pyaudio の両方に対応
        try:
            import pyaudiowpatch as pyaudio
            return pyaudio.paInt16
        except ImportError:
            pass
        try:
            import pyaudio
            return pyaudio.paInt16
        except ImportError:
            pass
        # フォールバック: paInt16 の値は 8 (pyaudio のデフォルト)
        return 8
