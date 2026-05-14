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

import numpy as np

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

    使用順序:
        通常は ``start() → write() → ... → stop()`` の順序で使用してください。
        ``start()`` 前に ``write()`` で渡したデータはキューに蓄積され、
        ``start()`` 後にバースト再生されます。
    """

    def __init__(
        self,
        pyaudio_instance=None,
        device_index: int | None = None,
        sample_rate: int = _DEFAULT_SAMPLE_RATE,
        channels: int = _DEFAULT_CHANNELS,
        chunk_size: int = _DEFAULT_CHUNK,
        volume: float = 1.0,
        owns_pa: bool = True,
    ):
        """
        Parameters
        ----------
        pyaudio_instance:  pyaudio.PyAudio インスタンス（テスト時はモック）
        device_index:      出力デバイスインデックス（None で pyaudio デフォルト）
        sample_rate:       サンプルレート（Hz）。gpt-realtime-translate は 24000
        channels:          チャンネル数
        chunk_size:        1回の write サイズ（bytes）
        volume:            出力音量倍率（0.0〜2.0）。1.0 のときはバイパス（変換なし）
        owns_pa:           True（デフォルト）なら stop() で pa.terminate() を呼ぶ。
                           False なら呼ばない（共有 PyAudio の場合に使用）。
        """
        self._pa = pyaudio_instance
        self._owns_pa = owns_pa
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_size = chunk_size
        self._volume = volume

        self._queue: queue.Queue = queue.Queue()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False
        # 直近に write された PCM の peak 値（絶対値最大）を保持（GUIレベルメーター用）
        self._audio_peak_now: int = 0
        self._peak_lock = threading.Lock()
        # デバッグ: write() 呼び出し回数（最初の数回だけログ出力）
        self._write_count: int = 0
        # リサンプリング: 実際にデバイスをオープンしたレート（start() 後に確定する）
        # input_rate（= sample_rate, 通常 24000）と異なる場合は _drain_loop でリサンプリングする
        self._input_rate: int = sample_rate
        self._output_rate: int = sample_rate
        # チャンネル数: 入力（OpenAI Realtime API は mono=1）と出力（デバイス依存）を分離管理
        # _output_channels は start() でデバイスの maxOutputChannels を確認後に更新される
        self._input_channels: int = channels
        self._output_channels: int = channels

    def start(self) -> None:
        """
        pyaudio output stream を開き、キュー消費スレッドを起動する。
        既に起動済みの場合は何もしない。

        サンプルレートのフォールバック順序:
          1. 指定レート（通常 24000Hz）
          2. デバイスの defaultSampleRate（48000Hz 等）
          3. 48000Hz（最終フォールバック）
          4. 44100Hz（最終フォールバック）
        """
        if self._started:
            return

        # デバイスの maxOutputChannels を確認してチャンネル数決定
        if self._pa is not None and self._device_index is not None:
            try:
                info = self._pa.get_device_info_by_index(self._device_index)
                device_max_ch = int(info.get("maxOutputChannels", 1))
                # デバイスが stereo 以上なら stereo で開く（mono 入力は _drain_loop で複製）
                if device_max_ch >= 2:
                    self._output_channels = 2
                else:
                    self._output_channels = 1
            except Exception:
                self._output_channels = self._input_channels

        # フォールバック用レートリストを構築
        rates_to_try = [self._input_rate]
        if self._pa is not None and self._device_index is not None:
            try:
                info = self._pa.get_device_info_by_index(self._device_index)
                default_rate = int(info.get("defaultSampleRate", 48000))
                if default_rate != self._input_rate:
                    rates_to_try.append(default_rate)
            except Exception:
                pass
        for fallback in (48000, 44100):
            if fallback not in rates_to_try:
                rates_to_try.append(fallback)

        last_error = None
        for try_rate in rates_to_try:
            try:
                kwargs: dict = {
                    "format": self._get_pyaudio_format(),
                    "channels": self._output_channels,
                    "rate": try_rate,
                    "output": True,
                    "frames_per_buffer": self._chunk_size,
                }
                if self._device_index is not None:
                    kwargs["output_device_index"] = self._device_index

                self._stream = self._pa.open(**kwargs)
                self._output_rate = try_rate
                print(
                    f"[AudioOutputStream] 開始成功: device_index={self._device_index}"
                    f" rate={try_rate}Hz channels={self._output_channels}"
                    f"{' (リサンプリング有効)' if try_rate != self._input_rate else ''}"
                    f"{' (mono→stereo 変換有効)' if self._output_channels > self._input_channels else ''}",
                    flush=True,
                )
                break
            except Exception as e:
                last_error = e
                print(
                    f"[AudioOutputStream] rate={try_rate}Hz オープン失敗: {e}",
                    flush=True,
                )
                continue

        if self._stream is None:
            # 全てのレートで失敗した場合
            logger.error(
                "[AudioOutputStream] 全 rate で失敗 device_index=%s tried=%s last_error=%s",
                self._device_index, rates_to_try, last_error,
            )
            print(
                f"[AudioOutputStream] 全 rate で失敗 device_index={self._device_index}"
                f" tried={rates_to_try} last_error={last_error}",
                flush=True,
            )
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
            "[AudioOutputStream] 開始: device_index=%s input_rate=%d output_rate=%d",
            self._device_index,
            self._input_rate,
            self._output_rate,
        )

    @property
    def audio_peak_now(self) -> int:
        """直近に write された PCM データの最大絶対値（peak）を返す。

        GUI のレベルメーター更新に使用する。値は 0〜32767 の範囲。
        """
        with self._peak_lock:
            return self._audio_peak_now

    def write(self, pcm16_bytes: bytes) -> None:
        """
        PCM16 bytes をキューに積む（スレッドセーフ）。
        stop() 後に呼ばれた場合は黙殺する。

        volume != 1.0 のとき PCM16 に音量倍率を乗算してからキューに積む。
        volume == 1.0 のときはオーバーヘッド回避のためバイパスする。
        write と同時に audio_peak_now を更新する。
        """
        if self._stop_event.is_set():
            return

        # デバッグ: 最初の 3 回だけバイト数をログ出力（実機テスト時に音声が届いているか確認）
        if self._write_count < 3:
            print(
                f"[AudioOutputStream] write {len(pcm16_bytes)} bytes"
                f" to device_index={self._device_index}"
                f" (stream={'open' if self._stream is not None else 'closed'})",
                flush=True,
            )
            self._write_count += 1

        if self._volume != 1.0:
            # PCM16 を int32 に拡張して乗算し、int16 範囲にクリップして戻す
            samples = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.int32)
            samples = np.clip((samples * self._volume).astype(np.int32), -32768, 32767)
            pcm16_bytes = samples.astype(np.int16).tobytes()

        # peak 追跡: volume 適用後のデータで peak を更新
        if pcm16_bytes:
            samples_for_peak = np.frombuffer(pcm16_bytes, dtype=np.int16)
            peak = int(np.abs(samples_for_peak).max()) if samples_for_peak.size else 0
            with self._peak_lock:
                self._audio_peak_now = peak

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

        # 共有インスタンス（owns_pa=False）の場合は terminate しない
        # terminate は MultiCaptionSystem.shutdown() 等の呼び出し側が責任を持つ
        if self._pa is not None and self._owns_pa:
            try:
                self._pa.terminate()
            except Exception:
                pass

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

        input_rate と output_rate が異なる場合は scipy.signal.resample_poly で
        PCM16 データをリサンプリングしてから書き込む。
        """
        from math import gcd
        from scipy.signal import resample_poly

        # リサンプリング比を事前計算（不要な場合は None で無効化）
        if self._output_rate != self._input_rate:
            g = gcd(self._output_rate, self._input_rate)
            _resample_up = self._output_rate // g
            _resample_down = self._input_rate // g
        else:
            _resample_up = None
            _resample_down = None

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

            # 1. リサンプリングが必要な場合（input_rate != output_rate）
            if _resample_up is not None:
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                resampled = resample_poly(samples, _resample_up, _resample_down)
                samples = np.clip(resampled, -32768, 32767).astype(np.int16)
            else:
                samples = np.frombuffer(data, dtype=np.int16)

            # 2. mono → stereo 変換（input_channels=1、output_channels=2 の場合）
            if self._output_channels == 2 and self._input_channels == 1:
                # 各サンプルを左右に複製: [s1, s2, s3] → [s1, s1, s2, s2, s3, s3]
                samples = np.repeat(samples, 2)
            elif self._output_channels != self._input_channels:
                logger.warning(
                    "未対応のチャンネル数変換: input=%d output=%d",
                    self._input_channels, self._output_channels,
                )

            data = samples.tobytes()

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
