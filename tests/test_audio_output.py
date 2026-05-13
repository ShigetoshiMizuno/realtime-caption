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

        assert data in mock_stream.written, \
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
        """volume=1.0 のとき write の audio_bytes が変更されないこと（パフォーマンス保護）。"""
        mock_stream = _MockPyAudioStream()
        mock_pa = _MockPyAudio(mock_stream)

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
            "volume=1.0 のとき write データは変更されないはず"
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
