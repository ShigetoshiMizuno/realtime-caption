"""
tests/test_audio_output.py

AudioOutputStream の単体テスト。
pyaudio は使わずモックバッファでデータフローを検証する。
"""

import queue
import threading
import time
from unittest.mock import MagicMock

import pytest

# audio_output モジュールは実装後にインポート可能になる
try:
    from audio_output import AudioOutputStream
    _MODULE_AVAILABLE = True
except ImportError:
    _MODULE_AVAILABLE = False
    AudioOutputStream = None


# ---------------------------------------------------------------------------
# モック pyaudio（pyaudio を使わずにテストするためのスタブ）
# ---------------------------------------------------------------------------

class _MockPyAudioStream:
    """pyaudio.Stream のミニマルモック。"""

    def __init__(self):
        self.written: list[bytes] = []
        self._closed = False

    def write(self, data: bytes):
        if not self._closed:
            self.written.append(data)

    def stop_stream(self):
        pass

    def close(self):
        self._closed = True


class _MockPyAudio:
    """pyaudio.PyAudio のミニマルモック。"""

    def __init__(self, stream: _MockPyAudioStream):
        self._stream = stream
        self._device_count = 3
        self._devices = [
            {"name": "Default Speaker", "maxOutputChannels": 2, "defaultSampleRate": 44100.0},
            {"name": "CABLE Input (VB-Audio Virtual Cable)", "maxOutputChannels": 2, "defaultSampleRate": 44100.0},
            {"name": "Headphones", "maxOutputChannels": 2, "defaultSampleRate": 48000.0},
        ]
        self.terminated = False

    def open(self, **kwargs):
        return self._stream

    def get_device_count(self):
        return self._device_count

    def get_device_info_by_index(self, index: int):
        return self._devices[index]

    def terminate(self):
        self.terminated = True


# ---------------------------------------------------------------------------
# テストクラス
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="audio_output モジュール未実装")
class TestAudioOutputStreamDataFlow:
    """write → queue → daemon thread のデータフロー検証。"""

    def test_write_enqueues_data(self):
        """
        write() したデータがキューに入ること。
        start() 前でも write() できること（キューに積まれる）。
        """
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        data = b"\x01\x02\x03\x04"
        stream.write(data)

        # キューに積まれているか確認
        assert not stream._queue.empty(), "write() 後にキューが空になってはいけない"
        queued = stream._queue.get_nowait()
        assert queued == data, f"キューのデータが不一致: {queued!r}"

    def test_start_drains_queue_to_stream(self):
        """
        start() 後に write() したデータが
        バックグラウンドスレッドを通じてストリームに書かれること。

        _MockPyAudio のデバイス (device_index=0) は maxOutputChannels=2 の stereo デバイスなので、
        mono 入力データは mono→stereo 変換されてストリームに届く。
        このテストではデータが届くこと（written が空でないこと）を確認する。
        """
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=0)
        stream.start()

        data = b"\xAA\xBB\xCC\xDD"
        stream.write(data)

        # スレッドが処理するのを待つ
        deadline = time.time() + 3
        while not mock_stream.written and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        assert mock_stream.written, \
            f"write() したデータがストリームに届いていない: {mock_stream.written}"

    def test_stop_is_idempotent(self):
        """stop() を複数回呼んでもエラーにならないこと。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=0)
        stream.start()
        stream.stop()
        stream.stop()  # 2回目も安全に呼べること

    def test_stop_without_start_is_safe(self):
        """start() せずに stop() を呼んでもエラーにならないこと。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=0)
        stream.stop()  # start 前でも安全

    def test_write_after_stop_does_not_raise(self):
        """stop() 後に write() してもエラーにならないこと（黙殺される）。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=0)
        stream.start()
        stream.stop()
        # stop 後の write はエラーにならない
        stream.write(b"\x00\x00")


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="audio_output モジュール未実装")
class TestAudioOutputStreamFindDevice:
    """find_device_index の部分一致テスト。"""

    def test_find_device_exact_match(self):
        """デバイス名の完全一致で正しいインデックスが返ること。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        idx = stream.find_device_index("CABLE Input (VB-Audio Virtual Cable)")
        assert idx == 1, f"期待インデックス 1、実際: {idx}"

    def test_find_device_partial_match(self):
        """デバイス名の部分一致でインデックスが返ること。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        idx = stream.find_device_index("CABLE Input")
        assert idx == 1, f"部分一致で期待インデックス 1、実際: {idx}"

    def test_find_device_case_insensitive(self):
        """デバイス名の大小文字を無視して一致すること。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        idx = stream.find_device_index("cable input")
        assert idx == 1, f"大小文字無視で期待インデックス 1、実際: {idx}"

    def test_find_device_not_found_returns_none(self):
        """存在しないデバイス名を検索したとき None が返ること。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        idx = stream.find_device_index("NonExistentDevice_XYZ_12345")
        assert idx is None, f"存在しないデバイスで None を期待、実際: {idx}"

    def test_find_device_filters_output_only(self):
        """maxOutputChannels == 0 のデバイスは除外されること。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)
        # デバイス 0 を入力専用にする
        mock_pa._devices[0]["maxOutputChannels"] = 0

        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        # "Default Speaker" は maxOutputChannels=0 なので見つからないはず
        idx = stream.find_device_index("Default Speaker")
        assert idx is None, f"出力チャンネルなしデバイスは除外されるべき、実際: {idx}"


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="audio_output モジュール未実装")
class TestAudioOutputStreamInit:
    """AudioOutputStream の初期化・インターフェーステスト。"""

    def test_constructor_accepts_required_params(self):
        """コンストラクタが必須パラメータで初期化できること。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)
        # device_index なし（None）でも構築できること
        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        assert stream is not None

    def test_has_required_methods(self):
        """start / write / stop / find_device_index メソッドが存在すること。"""
        assert hasattr(AudioOutputStream, "start"), "start メソッドが存在しない"
        assert hasattr(AudioOutputStream, "write"), "write メソッドが存在しない"
        assert hasattr(AudioOutputStream, "stop"), "stop メソッドが存在しない"
        assert hasattr(AudioOutputStream, "find_device_index"), "find_device_index メソッドが存在しない"

    def test_queue_attribute_exists(self):
        """_queue 属性（queue.Queue）が存在すること（データフローテスト用）。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)
        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        assert hasattr(stream, "_queue"), "_queue 属性が存在しない"
        assert isinstance(stream._queue, queue.Queue), "_queue は queue.Queue であること"


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="audio_output モジュール未実装")
class TestAudioOutputStreamVolume:
    """AudioOutputStream の volume 機能テスト。"""

    def test_audio_output_stream_applies_volume(self):
        """AudioOutputStream に volume を渡すと write 時に音量が乗算されること。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=0, volume=0.5)
        stream.start()

        import numpy as np
        # PCM16: 振幅 10000 の正弦波 1 サイクル
        samples = (np.ones(4, dtype=np.int16) * 10000).tobytes()
        stream.write(samples)

        deadline = time.time() + 3
        while not mock_stream.written and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        assert mock_stream.written, "データがストリームに届いていない"
        written_samples = np.frombuffer(mock_stream.written[0], dtype=np.int16)
        # volume=0.5 なので、各サンプルは約 5000 になるはず（誤差 ±1）
        assert all(abs(int(s) - 5000) <= 1 for s in written_samples), (
            f"volume=0.5 適用後の値が期待と違う: {written_samples.tolist()}"
        )

    def test_audio_output_stream_volume_default_bypass(self):
        """volume=1.0 のとき volume 処理によるデータ変更がないこと（パフォーマンス保護）。

        mono デバイス (maxOutputChannels=1) を使い、stereo 変換なしで検証する。
        volume=1.0 であれば PCM 値はそのまま stream に届くことを確認する。
        """
        mock_stream = _MockPyAudioStream()
        mock_pa = MagicMock()
        mock_pa.open.return_value = mock_stream
        # mono デバイス: stereo 変換を無効にする
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 1,
            "defaultSampleRate": 24000.0,
            "name": "Mono Test Device",
        }

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=0)
        stream.start()

        import numpy as np
        original = (np.array([10000, 20000, -5000, 0], dtype=np.int16)).tobytes()
        stream.write(original)

        deadline = time.time() + 3
        while not mock_stream.written and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        assert mock_stream.written, "データがストリームに届いていない"
        assert mock_stream.written[0] == original, (
            "volume=1.0 かつ mono デバイスのとき write データは変更されないはず"
        )

    def test_audio_output_stream_tracks_peak(self):
        """AudioOutputStream.audio_peak_now が write された PCM の peak を返すこと。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=0)
        stream.start()

        import numpy as np
        samples = np.array([100, 5000, -8000, 3000], dtype=np.int16)
        stream.write(samples.tobytes())

        deadline = time.time() + 3
        while not mock_stream.written and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        # audio_peak_now プロパティが存在し、書き込まれたデータの最大絶対値を返すこと
        assert hasattr(stream, "audio_peak_now"), (
            "AudioOutputStream に audio_peak_now プロパティが存在しない"
        )
        # 最大絶対値は 8000
        assert stream.audio_peak_now == 8000, (
            f"audio_peak_now が期待値 8000 と違う: {stream.audio_peak_now}"
        )


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="audio_output モジュール未実装")
class TestAudioOutputStreamTerminate:
    """stop() が PyAudio インスタンスの terminate() を呼ぶことを検証。"""

    def test_stop_terminates_pyaudio_instance(self):
        """stop() が PyAudio インスタンスの terminate() を呼ぶこと。"""
        mock_pa = MagicMock()
        mock_stream = MagicMock()
        mock_pa.open.return_value = mock_stream
        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=0,
        )
        stream.start()
        stream.stop()
        mock_pa.terminate.assert_called_once()

    def test_stop_terminates_only_once(self):
        """stop() を 2 回呼んでも terminate は 1 回だけ。"""
        mock_pa = MagicMock()
        mock_pa.open.return_value = MagicMock()
        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=0,
        )
        stream.start()
        stream.stop()
        stream.stop()
        mock_pa.terminate.assert_called_once()


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="audio_output モジュール未実装")
class TestAudioOutputStreamSampleRateFallback:
    """サンプルレートフォールバックとリサンプリング機能のテスト。"""

    def test_start_falls_back_to_device_default_rate_on_failure(self):
        """24000Hz でオープン失敗時、デバイスの defaultSampleRate にフォールバックすること。"""
        mock_pa = MagicMock()
        # 1回目（24000Hz）は例外、2回目（48000Hz）は成功
        mock_pa.open.side_effect = [
            Exception("[Errno -9997] Invalid sample rate"),
            MagicMock(),
        ]
        mock_pa.get_device_info_by_index.return_value = {
            "defaultSampleRate": 48000.0,
            "name": "CABLE Input (VB-Audio Virtual Cable)",
        }
        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=21,
            sample_rate=24000,
        )
        stream.start()

        assert stream._stream is not None, "フォールバック後もストリームが開かれるべき"
        assert stream._output_rate == 48000, (
            f"フォールバックレートは 48000 のはず、実際: {stream._output_rate}"
        )
        assert mock_pa.open.call_count == 2, (
            f"open は 2 回呼ばれるべき（1回目失敗、2回目成功）、実際: {mock_pa.open.call_count}"
        )
        stream.stop()

    def test_start_falls_back_through_all_rates_when_all_fail(self):
        """全てのサンプルレートで失敗した場合、stream は None のまま。"""
        mock_pa = MagicMock()
        mock_pa.open.side_effect = Exception("[Errno -9997] Invalid sample rate")
        mock_pa.get_device_info_by_index.return_value = {
            "defaultSampleRate": 48000.0,
            "name": "CABLE Input (VB-Audio Virtual Cable)",
        }
        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=21,
            sample_rate=24000,
        )
        stream.start()

        assert stream._stream is None, "全 rate で失敗したとき stream は None のまま"
        assert not stream._started, "全 rate で失敗したとき _started は False のまま"

    def test_start_tries_48000_and_44100_as_final_fallbacks(self):
        """デバイス defaultSampleRate が 24000 と同じ場合、最終フォールバックとして 48000Hz を試すこと。"""
        mock_pa = MagicMock()
        # 1回目（24000Hz）は失敗、2回目（48000Hz）は成功
        mock_pa.open.side_effect = [
            Exception("[Errno -9997] Invalid sample rate"),
            MagicMock(),
        ]
        # defaultSampleRate が 24000 の場合（フォールバック候補なし）
        mock_pa.get_device_info_by_index.return_value = {
            "defaultSampleRate": 24000.0,
            "name": "Some Device",
        }
        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=5,
            sample_rate=24000,
        )
        stream.start()

        assert stream._stream is not None, "48000Hz フォールバックで成功するはず"
        assert stream._output_rate == 48000, (
            f"最終フォールバック 48000Hz になるはず、実際: {stream._output_rate}"
        )
        stream.stop()

    def test_output_rate_equals_input_rate_when_open_succeeds_first_try(self):
        """24000Hz でそのまま開けた場合、_output_rate は 24000 のまま。"""
        mock_pa = MagicMock()
        mock_pa.open.return_value = MagicMock()
        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=0,
            sample_rate=24000,
        )
        stream.start()

        assert stream._output_rate == 24000, (
            f"直接成功時は _output_rate == 24000 のはず、実際: {stream._output_rate}"
        )
        stream.stop()

    def test_drain_resamples_when_output_rate_differs_from_input_rate(self):
        """input_rate(24000) と output_rate(48000) が異なるとき、
        _drain_loop がリサンプリングして stream.write に渡すこと。
        24000Hz → 48000Hz のリサンプリングでサンプル数が約 2 倍になる。
        """
        import numpy as np

        mock_pa = MagicMock()
        mock_stream_obj = MagicMock()
        written_data = []
        mock_stream_obj.write.side_effect = lambda d: written_data.append(d)

        # 1回目（24000Hz）は失敗、2回目（48000Hz）は成功
        mock_pa.open.side_effect = [
            Exception("[Errno -9997] Invalid sample rate"),
            mock_stream_obj,
        ]
        mock_pa.get_device_info_by_index.return_value = {
            "defaultSampleRate": 48000.0,
            "name": "CABLE Input (VB-Audio Virtual Cable)",
        }

        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=21,
            sample_rate=24000,
        )
        stream.start()
        assert stream._output_rate == 48000, "テスト前提: フォールバック後は 48000Hz"

        # 24000Hz 相当の PCM16 データ（960 サンプル = 40ms 分）
        n_samples_in = 960
        pcm_in = (np.ones(n_samples_in, dtype=np.int16) * 1000).tobytes()
        stream.write(pcm_in)

        deadline = time.time() + 3
        while not written_data and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        assert written_data, "リサンプリング後のデータがストリームに届いていない"
        out_samples = np.frombuffer(written_data[0], dtype=np.int16)
        # 48000/24000 = 2 倍のサンプル数になるはず
        assert len(out_samples) == n_samples_in * 2, (
            f"リサンプリング後のサンプル数は {n_samples_in * 2} のはず、実際: {len(out_samples)}"
        )

    def test_no_resample_when_rates_match(self):
        """input_rate と output_rate が同じとき、データはそのまま stream.write に渡される。"""
        import numpy as np

        mock_pa = MagicMock()
        mock_stream_obj = MagicMock()
        written_data = []
        mock_stream_obj.write.side_effect = lambda d: written_data.append(d)
        mock_pa.open.return_value = mock_stream_obj

        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=0,
            sample_rate=24000,
        )
        stream.start()
        assert stream._output_rate == 24000, "テスト前提: 直接成功で 24000Hz"

        original = (np.ones(960, dtype=np.int16) * 2000).tobytes()
        stream.write(original)

        deadline = time.time() + 3
        while not written_data and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        assert written_data, "データがストリームに届いていない"
        assert written_data[0] == original, (
            "レート一致のとき、データはそのまま渡されるはず（リサンプリングなし）"
        )


@pytest.mark.skipif(not _MODULE_AVAILABLE, reason="audio_output モジュール未実装")
class TestAudioOutputStreamStereoDetection:
    """Issue #41: デバイスが stereo のとき自動的に 2ch でオープンする機能のテスト。"""

    def test_start_auto_detects_stereo_device_and_opens_stereo(self):
        """device の maxOutputChannels >= 2 のとき、stream は channels=2 でオープンされること。"""
        mock_pa = MagicMock()
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 2,
            "defaultSampleRate": 48000.0,
            "name": "CABLE Input (VB-Audio Virtual Cable)",
        }
        mock_pa.open.return_value = MagicMock()

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=21)
        stream.start()

        # pa.open の kwargs を取り出して channels=2 を検証
        call_kwargs = mock_pa.open.call_args[1]
        assert call_kwargs["channels"] == 2, (
            f"stereo デバイスでは channels=2 でオープンされるべき、実際: {call_kwargs['channels']}"
        )
        assert stream._output_channels == 2, (
            f"_output_channels は 2 のはず、実際: {stream._output_channels}"
        )
        stream.stop()

    def test_start_opens_mono_when_device_is_mono(self):
        """device の maxOutputChannels == 1 のとき、stream は channels=1 でオープンされること。"""
        mock_pa = MagicMock()
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 1,
            "defaultSampleRate": 48000.0,
            "name": "Mono Output Device",
        }
        mock_pa.open.return_value = MagicMock()

        stream = AudioOutputStream(pyaudio_instance=mock_pa, device_index=5)
        stream.start()

        call_kwargs = mock_pa.open.call_args[1]
        assert call_kwargs["channels"] == 1, (
            f"mono デバイスでは channels=1 でオープンされるべき、実際: {call_kwargs['channels']}"
        )
        assert stream._output_channels == 1, (
            f"_output_channels は 1 のはず、実際: {stream._output_channels}"
        )
        stream.stop()

    def test_input_channels_default_is_mono(self):
        """_input_channels のデフォルトは 1 (mono) であること。"""
        mock_pa = MagicMock()
        mock_pa.open.return_value = MagicMock()

        stream = AudioOutputStream(pyaudio_instance=mock_pa)
        assert stream._input_channels == 1, (
            f"_input_channels のデフォルトは 1 (mono) のはず、実際: {stream._input_channels}"
        )

    def test_drain_loop_converts_mono_to_stereo_when_device_is_stereo(self):
        """mono 入力 + stereo 出力のとき、_drain_loop が PCM を mono→stereo 変換すること。

        PCM [s1, s2] (mono, int16×2) → 変換後 [s1, s1, s2, s2] (stereo, int16×4 = 8 bytes)
        """
        import numpy as np

        mock_pa = MagicMock()
        mock_stream_obj = MagicMock()
        written_data = []
        mock_stream_obj.write.side_effect = lambda d: written_data.append(d)

        # stereo デバイス: 1回目(24000Hz) 失敗、2回目(48000Hz) 成功
        mock_pa.open.side_effect = [
            Exception("[Errno -9997] Invalid sample rate"),
            mock_stream_obj,
        ]
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 2,
            "defaultSampleRate": 48000.0,
            "name": "CABLE Input (VB-Audio Virtual Cable)",
        }

        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=21,
            sample_rate=24000,
            channels=1,  # mono 入力（OpenAI Realtime API）
        )
        stream.start()
        assert stream._output_channels == 2, "テスト前提: stereo デバイスなので _output_channels=2"
        assert stream._input_channels == 1, "テスト前提: mono 入力なので _input_channels=1"

        # mono PCM: [256, 512] (int16, 2サンプル, 4bytes)
        pcm_mono = np.array([256, 512], dtype=np.int16).tobytes()
        stream.write(pcm_mono)

        deadline = time.time() + 3
        while not written_data and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        assert written_data, "変換後のデータがストリームに届いていない"

        out_bytes = written_data[0]
        out_samples = np.frombuffer(out_bytes, dtype=np.int16)

        # リサンプリング (24000→48000, 2倍) かつ mono→stereo (各サンプルを複製)
        # 入力 2 サンプル → リサンプリング後 4 サンプル → stereo 変換後 8 サンプル
        assert len(out_samples) == 8, (
            f"mono 2サンプル → 48kHzリサンプリング後4サンプル → stereo後8サンプルのはず、実際: {len(out_samples)}"
        )

        # stereo 変換済みのため、隣り合うサンプル(L,R)が同値のはず
        for i in range(0, len(out_samples), 2):
            assert out_samples[i] == out_samples[i + 1], (
                f"stereo 変換後: L[{i}]={out_samples[i]} と R[{i+1}]={out_samples[i+1]} が一致するはず"
            )

    def test_drain_loop_no_stereo_conversion_when_both_mono(self):
        """mono 入力 + mono 出力のとき、stereo 変換が行われないこと。"""
        import numpy as np

        mock_pa = MagicMock()
        mock_stream_obj = MagicMock()
        written_data = []
        mock_stream_obj.write.side_effect = lambda d: written_data.append(d)
        mock_pa.open.return_value = mock_stream_obj
        mock_pa.get_device_info_by_index.return_value = {
            "maxOutputChannels": 1,
            "defaultSampleRate": 24000.0,
            "name": "Mono Device",
        }

        stream = AudioOutputStream(
            pyaudio_instance=mock_pa,
            device_index=0,
            sample_rate=24000,
            channels=1,
        )
        stream.start()
        assert stream._output_channels == 1, "テスト前提: mono デバイス"

        pcm_mono = np.array([1000, 2000, 3000, 4000], dtype=np.int16).tobytes()
        stream.write(pcm_mono)

        deadline = time.time() + 3
        while not written_data and time.time() < deadline:
            time.sleep(0.05)

        stream.stop()

        assert written_data, "データがストリームに届いていない"
        # mono のまま届くはず: サンプル数は変わらない
        out_samples = np.frombuffer(written_data[0], dtype=np.int16)
        assert len(out_samples) == 4, (
            f"mono→mono 変換なしで 4 サンプルのはず、実際: {len(out_samples)}"
        )
