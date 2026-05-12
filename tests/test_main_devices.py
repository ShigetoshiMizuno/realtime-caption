"""
tests/test_main_devices.py

main.py のデバイス関連関数の単体テスト。

- list_audio_devices(device_type) の引数拡張
- find_device_by_name の部分一致
- select_output_device の存在確認
"""

import sys
import types
import unittest.mock as mock
from pathlib import Path

import pytest

# main.py は pyaudio / torch 等の重いライブラリを import するため、
# 最低限のスタブで差し替えてから import する。
# conftest.py がプロジェクトルートを sys.path に追加している。


def _make_mock_pyaudio(devices: list[dict]):
    """
    pyaudiowpatch のミニマルモックを返す。
    devices: list of {"name": str, "maxInputChannels": int, "maxOutputChannels": int,
                      "defaultSampleRate": float, "isLoopbackDevice": bool}
    """
    mock_pa_instance = mock.MagicMock()
    mock_pa_instance.get_device_count.return_value = len(devices)

    def _get_info(i):
        d = devices[i]
        return {
            "name": d.get("name", f"Device {i}"),
            "maxInputChannels": d.get("maxInputChannels", 0),
            "maxOutputChannels": d.get("maxOutputChannels", 0),
            "defaultSampleRate": d.get("defaultSampleRate", 44100.0),
            "isLoopbackDevice": d.get("isLoopbackDevice", False),
        }

    mock_pa_instance.get_device_info_by_index.side_effect = _get_info
    mock_pa_instance.terminate.return_value = None

    mock_pyaudio_module = mock.MagicMock()
    mock_pyaudio_module.PyAudio.return_value = mock_pa_instance
    mock_pyaudio_module.paInt16 = 8
    return mock_pyaudio_module, mock_pa_instance


# テスト用デバイス一覧
_SAMPLE_DEVICES = [
    {
        "name": "Microphone (USB Audio Device)",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": False,
    },
    {
        "name": "CABLE Input (VB-Audio Virtual Cable)",
        "maxInputChannels": 0,
        "maxOutputChannels": 2,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": False,
    },
    {
        "name": "Speakers (Realtek Audio) [Loopback]",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": True,
    },
    {
        "name": "Headphones",
        "maxInputChannels": 0,
        "maxOutputChannels": 2,
        "defaultSampleRate": 48000.0,
        "isLoopbackDevice": False,
    },
]


def _import_list_audio_devices_with_mock(devices):
    """pyaudiowpatch をモックして list_audio_devices をインポートする。"""
    mock_pyaudio_module, _ = _make_mock_pyaudio(devices)

    # main.py をインポートする前に依存を差し替える
    # main.py 自体は既にキャッシュにある可能性があるため、直接 pyaudiowpatch を差し替える
    import sys
    original = sys.modules.get("pyaudiowpatch")
    sys.modules["pyaudiowpatch"] = mock_pyaudio_module

    try:
        # main モジュールを再インポートして最新の pyaudiowpatch を使わせる
        if "main" in sys.modules:
            # 既存モジュールの pyaudiowpatch 参照を差し替える
            import main as main_mod
            original_pyaudio = main_mod.pyaudio
            main_mod.pyaudio = mock_pyaudio_module
            return main_mod, lambda: setattr(main_mod, "pyaudio", original_pyaudio)
        else:
            import main as main_mod
            return main_mod, lambda: None
    finally:
        if original is not None:
            sys.modules["pyaudiowpatch"] = original
        else:
            sys.modules.pop("pyaudiowpatch", None)


class TestListAudioDevices:
    """list_audio_devices の device_type 引数テスト。"""

    def test_default_returns_input_devices(self):
        """
        引数なし（または device_type="input"）のとき、
        入力デバイスとループバックデバイスが返ること（後方互換）。
        """
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio(_SAMPLE_DEVICES)
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices()
            names = [d["name"] for d in devices]
        finally:
            main_mod.pyaudio = original

        # 入力デバイスとループバックが含まれること
        assert any("Microphone" in n for n in names), \
            f"マイクが含まれていない: {names}"
        assert any("Loopback" in n for n in names), \
            f"ループバックが含まれていない: {names}"
        # 出力専用デバイスは含まれないこと
        assert not any("CABLE Input" in n for n in names), \
            f"出力専用デバイスが含まれてはいけない: {names}"

    def test_output_type_returns_output_devices(self):
        """
        device_type="output" のとき、出力チャンネルを持つデバイスが返ること。
        """
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio(_SAMPLE_DEVICES)
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="output")
            names = [d["name"] for d in devices]
        finally:
            main_mod.pyaudio = original

        assert any("CABLE Input" in n for n in names), \
            f"CABLE Input が含まれていない: {names}"
        assert any("Headphones" in n for n in names), \
            f"Headphones が含まれていない: {names}"
        # 入力専用デバイスは含まれないこと
        assert not any("Microphone" in n for n in names), \
            f"入力専用デバイスが含まれてはいけない: {names}"

    def test_all_type_returns_all_devices(self):
        """
        device_type="all" のとき、入出力すべてのデバイスが返ること。
        """
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio(_SAMPLE_DEVICES)
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="all")
            names = [d["name"] for d in devices]
        finally:
            main_mod.pyaudio = original

        assert any("Microphone" in n for n in names)
        assert any("CABLE Input" in n for n in names)
        assert any("Headphones" in n for n in names)


class TestFindDeviceByName:
    """find_device_by_name の部分一致テスト。"""

    def test_find_by_partial_name(self):
        """部分一致でデバイスが見つかること。"""
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio(_SAMPLE_DEVICES)
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="output")
            result = main_mod.find_device_by_name("CABLE Input", devices)
        finally:
            main_mod.pyaudio = original

        assert result is not None, "CABLE Input が見つからなかった"
        assert "CABLE" in result["name"]

    def test_find_case_insensitive(self):
        """大小文字を無視して一致すること。"""
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio(_SAMPLE_DEVICES)
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="all")
            result = main_mod.find_device_by_name("cable input", devices)
        finally:
            main_mod.pyaudio = original

        assert result is not None, "大小文字無視で CABLE Input が見つからなかった"

    def test_find_returns_none_when_not_found(self):
        """存在しない名前は None を返すこと。"""
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio(_SAMPLE_DEVICES)
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="all")
            result = main_mod.find_device_by_name("NonExistentDevice_XYZ", devices)
        finally:
            main_mod.pyaudio = original

        assert result is None


class TestSelectOutputDeviceExists:
    """select_output_device 関数の存在確認。"""

    def test_function_exists(self):
        """select_output_device 関数が main モジュールに存在すること。"""
        import main as main_mod
        assert hasattr(main_mod, "select_output_device"), \
            "select_output_device 関数が main.py に存在しない"
        assert callable(main_mod.select_output_device)
