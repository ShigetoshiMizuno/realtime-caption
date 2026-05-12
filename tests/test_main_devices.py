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

    get_host_api_info_by_index は常に "Windows WASAPI" を返す（host_api デフォルト値との互換）。
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
            "hostApi": 0,
        }

    def _get_host_api_info(idx):
        return {"name": "Windows WASAPI", "index": idx}

    mock_pa_instance.get_device_info_by_index.side_effect = _get_info
    mock_pa_instance.get_host_api_info_by_index.side_effect = _get_host_api_info
    mock_pa_instance.terminate.return_value = None

    mock_pyaudio_module = mock.MagicMock()
    mock_pyaudio_module.PyAudio.return_value = mock_pa_instance
    mock_pyaudio_module.paInt16 = 8
    return mock_pyaudio_module, mock_pa_instance


def _make_mock_pyaudio_with_host_api(devices: list[dict]):
    """
    pyaudiowpatch のモックを返す。hostApi インデックスと get_host_api_info_by_index に対応。

    devices: list of {
        "name": str,
        "maxInputChannels": int,
        "maxOutputChannels": int,
        "defaultSampleRate": float,
        "isLoopbackDevice": bool,
        "hostApi": int,  # host API インデックス
    }
    host_apis は devices の "hostApi" インデックスから自動収集する。
    各インデックスに対応する名前は "hostApiName" キーで指定。
    """
    # hostApi インデックス -> 名前のマッピングを devices から収集
    host_api_map: dict[int, str] = {}
    for d in devices:
        idx = d.get("hostApi", 0)
        name = d.get("hostApiName", "MME")
        host_api_map[idx] = name

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
            "hostApi": d.get("hostApi", 0),
        }

    def _get_host_api_info(idx):
        name = host_api_map.get(idx, "Unknown")
        return {"name": name, "index": idx}

    mock_pa_instance.get_device_info_by_index.side_effect = _get_info
    mock_pa_instance.get_host_api_info_by_index.side_effect = _get_host_api_info
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


# Host API 付きテスト用デバイス一覧
# MME(0), DirectSound(1), WASAPI(2), WASAPI Loopback(2) の混在
_SAMPLE_DEVICES_WITH_HOST_API = [
    {
        "name": "Microphone (MME)",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": False,
        "hostApi": 0,
        "hostApiName": "MME",
    },
    {
        "name": "Microphone (DirectSound)",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": False,
        "hostApi": 1,
        "hostApiName": "Windows DirectSound",
    },
    {
        "name": "Microphone (WASAPI)",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": False,
        "hostApi": 2,
        "hostApiName": "Windows WASAPI",
    },
    {
        "name": "Speakers (Realtek Audio)",
        "maxInputChannels": 0,
        "maxOutputChannels": 2,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": False,
        "hostApi": 2,
        "hostApiName": "Windows WASAPI",
    },
    {
        "name": "Speakers (Realtek Audio)",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": True,
        "hostApi": 2,
        "hostApiName": "Windows WASAPI",
    },
    {
        "name": "Microphone (MME)",
        "maxInputChannels": 2,
        "maxOutputChannels": 0,
        "defaultSampleRate": 44100.0,
        "isLoopbackDevice": False,
        "hostApi": 3,
        "hostApiName": "Windows WDM-KS",
    },
]


class TestListAudioDevicesHostApiFilter:
    """list_audio_devices の host_api フィルタテスト（Issue #21）。"""

    def test_list_audio_devices_filters_wasapi_by_default(self):
        """
        host_api 引数なし（デフォルト "wasapi"）のとき、
        WASAPI 以外の Host API（MME, DirectSound 等）のデバイスは除外されること。
        """
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio_with_host_api(
            _SAMPLE_DEVICES_WITH_HOST_API
        )
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="input")
            host_apis = [d.get("hostApi", "") for d in devices]
        finally:
            main_mod.pyaudio = original

        # WASAPI 以外のエントリが含まれていないこと
        for ha in host_apis:
            assert "wasapi" in ha.lower(), \
                f"WASAPI 以外の Host API が含まれている: {ha}"

    def test_list_audio_devices_host_api_all_includes_mme(self):
        """
        host_api="all" のとき、MME など全 Host API のデバイスが含まれること。
        """
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio_with_host_api(
            _SAMPLE_DEVICES_WITH_HOST_API
        )
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="input", host_api="all")
            names = [d["name"] for d in devices]
        finally:
            main_mod.pyaudio = original

        # MME デバイスが含まれること
        assert any("MME" in n for n in names), \
            f"host_api='all' で MME デバイスが含まれていない: {names}"
        # DirectSound デバイスも含まれること
        assert any("DirectSound" in n for n in names), \
            f"host_api='all' で DirectSound デバイスが含まれていない: {names}"

    def test_list_audio_devices_loopback_has_marker(self):
        """
        ループバックデバイスの name に '[Loopback]' が付くこと。
        """
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio_with_host_api(
            _SAMPLE_DEVICES_WITH_HOST_API
        )
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            devices = main_mod.list_audio_devices(device_type="input", host_api="all")
            loopback_devices = [d for d in devices if d.get("isLoopback")]
        finally:
            main_mod.pyaudio = original

        assert len(loopback_devices) > 0, "ループバックデバイスが1件も含まれていない"
        for d in loopback_devices:
            assert "[Loopback]" in d["name"], \
                f"ループバックデバイスに '[Loopback]' が付いていない: {d['name']}"

    def test_find_device_by_name_partial_match_unchanged(self):
        """
        find_device_by_name の部分一致挙動が壊れていないこと（後方互換）。
        host_api フィルタを追加した後も既存の動作を維持すること。
        """
        import main as main_mod
        mock_pyaudio_module, _ = _make_mock_pyaudio_with_host_api(
            _SAMPLE_DEVICES_WITH_HOST_API
        )
        original = main_mod.pyaudio
        main_mod.pyaudio = mock_pyaudio_module

        try:
            # WASAPI デバイスに絞った後で部分一致
            devices = main_mod.list_audio_devices(device_type="all", host_api="wasapi")
            result = main_mod.find_device_by_name("Speakers", devices)
        finally:
            main_mod.pyaudio = original

        assert result is not None, \
            "WASAPI フィルタ後の find_device_by_name で 'Speakers' が見つからなかった"
        assert "Speakers" in result["name"]


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
