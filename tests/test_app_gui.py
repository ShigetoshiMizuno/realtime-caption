"""
test_app_gui.py

app.py の純関数部分の単体テスト。
dpg (DearPyGui) の実機起動は不要な部分だけを対象にする。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_zoom_preset_finds_cable_input():
    """Zoom プリセットで CABLE Input 出力デバイスが見つかること。"""
    from app import _find_zoom_preset_output  # noqa: PLC0415

    devices = [
        {"name": "Speakers", "index": 1},
        {"name": "CABLE Input (VB-Audio Virtual Cable)", "index": 5},
    ]
    assert _find_zoom_preset_output(devices) == 5


def test_zoom_preset_finds_cable_input_case_insensitive():
    """CABLE INPUT (大文字) でも検出できること。"""
    from app import _find_zoom_preset_output  # noqa: PLC0415

    devices = [
        {"name": "CABLE INPUT (VB-Audio Virtual Cable)", "index": 3},
    ]
    assert _find_zoom_preset_output(devices) == 3


def test_zoom_preset_no_cable():
    """CABLE Input が見つからない場合 None を返すこと。"""
    from app import _find_zoom_preset_output  # noqa: PLC0415

    devices = [{"name": "Speakers", "index": 1}]
    assert _find_zoom_preset_output(devices) is None


def test_zoom_preset_empty_devices():
    """デバイスリストが空の場合 None を返すこと。"""
    from app import _find_zoom_preset_output  # noqa: PLC0415

    assert _find_zoom_preset_output([]) is None


# ---------------------------------------------------------------------------
# _resolve_settings_visibility のテスト
# ---------------------------------------------------------------------------

def test_visibility_openai_realtime():
    """openai-realtime モード時: Whisper設定非表示、Realtime設定表示、DeepLキー非表示。"""
    from app import _resolve_settings_visibility  # noqa: PLC0415

    v = _resolve_settings_visibility("openai-realtime")
    assert v["whisper"] is False
    assert v["realtime"] is True
    assert v["deepl_key"] is False


def test_visibility_openai():
    """openai モード時: Whisper設定表示、Realtime設定非表示、DeepLキー表示。"""
    from app import _resolve_settings_visibility  # noqa: PLC0415

    v = _resolve_settings_visibility("openai")
    assert v["whisper"] is True
    assert v["realtime"] is False
    assert v["deepl_key"] is True


def test_visibility_deepl():
    """deepl モード時: Whisper設定表示、Realtime設定非表示、DeepLキー表示。"""
    from app import _resolve_settings_visibility  # noqa: PLC0415

    v = _resolve_settings_visibility("deepl")
    assert v["whisper"] is True
    assert v["realtime"] is False
    assert v["deepl_key"] is True


# ---------------------------------------------------------------------------
# _on_zoom_preset_click の visibility 更新テスト
# ---------------------------------------------------------------------------

def test_zoom_preset_updates_visibility():
    """
    Zoom プリセット押下時に _update_settings_visibility("openai-realtime") が
    呼ばれることを確認する。
    dpg は GUI 環境なしでは動作しないため、_update_settings_visibility と dpg を
    モックして検証する。
    """
    import importlib
    import types
    from unittest.mock import MagicMock, patch, call

    # dpg モックを組み立てる
    mock_dpg = MagicMock()
    # TAG_TRANS_COMBO が存在し、items に "openai-realtime" のラベルが含まれる状態を模擬
    # _trans_key_to_label("openai-realtime") は app モジュール内で呼ばれるため、
    # does_item_exist → True、get_item_configuration → items あり を返す
    mock_dpg.does_item_exist.return_value = True
    mock_dpg.get_item_configuration.return_value = {
        "items": ["OpenAI (Whisper)", "OpenAI Realtime", "DeepL"]
    }
    # set_value は副作用なし

    import app  # noqa: PLC0415

    # _trans_key_to_label が "OpenAI Realtime" を返すようにモック
    with (
        patch.object(app, "dpg", mock_dpg),
        patch.object(app, "_trans_key_to_label", return_value="OpenAI Realtime"),
        patch.object(app, "_save_settings"),
        patch.object(app, "_update_settings_visibility") as mock_update_vis,
    ):
        app._on_zoom_preset_click()

    mock_update_vis.assert_called_once_with("openai-realtime")
