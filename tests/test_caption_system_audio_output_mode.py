"""
tests/test_caption_system_audio_output_mode.py

W-COST-1 PR2: CaptionSystem._create_realtime_translator が
self._audio_output_mode を request_audio_output に渡すことを検証するテスト。

TDD RED フェーズ: main.py:623 が request_audio_output=True 固定の段階で作成。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_fake_config() -> dict:
    """テスト用の最小限設定 dict（API キーはフェイク値）。"""
    return {
        "translation": {
            "translation_model": "openai-realtime",
        },
        "openai": {
            "api_key": "sk-test-fake-cost-w1",
        },
        "openai_realtime": {
            "target_language_code": "ja",
            "model": "gpt-realtime-translate",
            "connect_timeout": 10,
            "reconnect_max_attempts": 5,
            "reconnect_backoff_base": 1.5,
            "max_session_minutes": 60,
        },
        "output": {
            "log_dir": ".",
        },
    }


def _make_caption_system(output_device_index=None):
    """CaptionSystem を __init__ 経由で生成するヘルパー。

    output_device_index=None -> _audio_output_mode=False
    output_device_index=<int> -> _audio_output_mode=True
    """
    from main import CaptionSystem
    config = _make_fake_config()
    # device_info は最小限
    device_info = {"index": 0, "name": "FakeInputDevice"}
    return CaptionSystem(
        config=config,
        device_info=device_info,
        model_name="gpt-realtime-translate",
        output_device_index=output_device_index,
    )


# ---------------------------------------------------------------------------
# _audio_output_mode 初期化テスト
# ---------------------------------------------------------------------------

class TestAudioOutputModeInit:
    """CaptionSystem.__init__ で _audio_output_mode が正しく設定されることを確認。"""

    def test_audio_output_mode_false_when_no_device(self):
        """output_device_index=None のとき _audio_output_mode が False であること。"""
        cs = _make_caption_system(output_device_index=None)
        assert cs._audio_output_mode is False, (
            f"output_device_index=None なのに _audio_output_mode={cs._audio_output_mode}"
        )

    def test_audio_output_mode_true_when_device_given(self):
        """output_device_index=<int> のとき _audio_output_mode が True であること。"""
        cs = _make_caption_system(output_device_index=3)
        assert cs._audio_output_mode is True, (
            f"output_device_index=3 なのに _audio_output_mode={cs._audio_output_mode}"
        )


# ---------------------------------------------------------------------------
# _create_realtime_translator が request_audio_output を動的に渡すテスト
# ---------------------------------------------------------------------------

class TestCreateRealtimeTranslatorAudioOutputMode:
    """_create_realtime_translator が self._audio_output_mode を
    request_audio_output に渡すことを検証する。"""

    def test_request_audio_output_false_when_no_device(self):
        """output_device_index=None のとき RealtimeTranslator が
        request_audio_output=False で生成されること。

        W-COST-1 PR2 のコア検証:
        音声出力 OFF（デバイスなし）の場合に API 側の音声生成を抑制するフラグが
        正しく渡されることを確認する。
        """
        with patch("realtime_translator.RealtimeTranslator") as MockTranslator, \
             patch("cost_monitor.CostMonitor"):
            MockTranslator.return_value = MagicMock()
            cs = _make_caption_system(output_device_index=None)
            # _audio_output_mode が False であることを前提確認
            assert cs._audio_output_mode is False

            cs._create_realtime_translator()

        # RealtimeTranslator が呼ばれたこと
        assert MockTranslator.called, "RealtimeTranslator が呼ばれなかった"
        _, kwargs = MockTranslator.call_args
        assert "request_audio_output" in kwargs, (
            "request_audio_output キーワード引数が渡されなかった"
        )
        assert kwargs["request_audio_output"] is False, (
            f"output_device_index=None のとき request_audio_output が False であるべき。"
            f"実際の値: {kwargs['request_audio_output']}"
        )

    def test_request_audio_output_true_when_device_given(self):
        """output_device_index=<int> のとき RealtimeTranslator が
        request_audio_output=True で生成されること。

        音声出力 ON（デバイスあり）の場合に API 側の音声生成が有効化されることを確認する。
        """
        with patch("realtime_translator.RealtimeTranslator") as MockTranslator, \
             patch("cost_monitor.CostMonitor"):
            MockTranslator.return_value = MagicMock()
            cs = _make_caption_system(output_device_index=2)
            # _audio_output_mode が True であることを前提確認
            assert cs._audio_output_mode is True

            cs._create_realtime_translator()

        assert MockTranslator.called, "RealtimeTranslator が呼ばれなかった"
        _, kwargs = MockTranslator.call_args
        assert "request_audio_output" in kwargs, (
            "request_audio_output キーワード引数が渡されなかった"
        )
        assert kwargs["request_audio_output"] is True, (
            f"output_device_index=2 のとき request_audio_output が True であるべき。"
            f"実際の値: {kwargs['request_audio_output']}"
        )

    def test_create_realtime_translator_is_idempotent(self):
        """_create_realtime_translator は既に生成済みなら no-op であること。

        2回呼んでも RealtimeTranslator は1回しか生成されないことを確認する。
        """
        with patch("realtime_translator.RealtimeTranslator") as MockTranslator, \
             patch("cost_monitor.CostMonitor"):
            MockTranslator.return_value = MagicMock()
            cs = _make_caption_system(output_device_index=None)

            cs._create_realtime_translator()
            cs._create_realtime_translator()  # 2回目は no-op

        assert MockTranslator.call_count == 1, (
            f"RealtimeTranslator が {MockTranslator.call_count} 回呼ばれた（1回のみ期待）"
        )

    def test_audio_output_mode_reflects_in_translator_after_set_output_device_off(self):
        """set_output_device(None) で _audio_output_mode=False になった後、
        _realtime_translator を再生成すると request_audio_output=False になること。

        PR3 の再起動フロー（stop_route -> start_route）の前提確認:
        _audio_output_mode の最新値が _create_realtime_translator で使われることを検証する。
        """
        with patch("realtime_translator.RealtimeTranslator") as MockTranslator, \
             patch("cost_monitor.CostMonitor"):
            MockTranslator.side_effect = [MagicMock(), MagicMock()]
            cs = _make_caption_system(output_device_index=2)
            assert cs._audio_output_mode is True

            # 1回目の生成（audio_output_mode=True）
            cs._create_realtime_translator()
            first_kwargs = MockTranslator.call_args_list[0][1]
            assert first_kwargs["request_audio_output"] is True

            # 音声出力を OFF にする（stop_route -> start_route 前の状態変更を模倣）
            cs._audio_output_mode = False
            cs._realtime_translator = None  # 再起動による再生成を模倣

            # 2回目の生成（audio_output_mode=False）
            cs._create_realtime_translator()
            second_kwargs = MockTranslator.call_args_list[1][1]
            assert second_kwargs["request_audio_output"] is False, (
                f"set_output_device(None) 後の再生成で request_audio_output が False であるべき。"
                f"実際の値: {second_kwargs['request_audio_output']}"
            )
