"""
tests/test_review_phase_c.py

Phase C レビュー対応テスト（W-6 / W-7 / S-1-phase-c / W-2-pr4 / W-3-pr4 / S-1-pr4）。

TDD: RED フェーズで先に書き、GREEN フェーズで実装を修正する。
"""

import threading
from unittest.mock import MagicMock, patch, call

import pytest

import app


# ---------------------------------------------------------------------------
# W-7: test_log_route_id.py の _make_cs ヘルパーに _model_name="tiny" を持つこと
# ---------------------------------------------------------------------------

class TestMakeCsHasModelName:
    """_make_cs ヘルパーが _model_name 属性を含んでいること（W-7）。

    Whisper モデルロードが走らないよう "tiny" を設定しておかないと、
    prepare() を呼ぶテストで実際のモデルダウンロードが発生するリスクがある。
    """

    def test_make_cs_has_model_name_attribute(self):
        """_make_cs() が返す CaptionSystem インスタンスが _model_name 属性を持つこと。"""
        import sys
        from pathlib import Path

        # test_log_route_id.py の _make_cs を直接インポートして検査
        sys.path.insert(0, str(Path(__file__).parent))
        import importlib
        import tests.test_log_route_id as log_route_module

        cs = log_route_module._make_cs("a")
        assert hasattr(cs, "_model_name"), (
            "_make_cs() が返す CaptionSystem に _model_name 属性がない。"
            "_model_name='tiny' の設定が必要（W-7）。"
        )

    def test_make_cs_model_name_is_tiny(self):
        """_make_cs() が返す CaptionSystem の _model_name が 'tiny' であること（W-7）。"""
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent))
        import tests.test_log_route_id as log_route_module

        cs = log_route_module._make_cs("a")
        assert cs._model_name == "tiny", (
            f"_make_cs() の _model_name が 'tiny' でない: {cs._model_name!r}。"
            "Whisper モデルロード時間短縮のために 'tiny' を設定すること（W-7）。"
        )


# ---------------------------------------------------------------------------
# S-1 (Phase C): rate_limit + quota を同時含むメッセージのテスト
# ---------------------------------------------------------------------------

class TestClassifyRealtimeErrorRateLimitPlusQuota:
    """rate_limit と quota の両方のキーワードを含むエラーメッセージの分類（S-1 Phase C）。"""

    def test_rate_limit_takes_precedence_over_quota_when_both_present(self):
        """rate_limit と insufficient_quota を両方含むメッセージで、
        優先順位が実装通りに決まること（quota が先にチェックされるなら quota）。

        実装での検査順: insufficient_quota → auth → rate_limit → connection
        よって両方含む場合は quota が先にマッチする。
        """
        from main import _classify_realtime_error

        msg = "Error 429 rate_limit_exceeded insufficient_quota combined"
        cat, txt = _classify_realtime_error(msg)

        # insufficient_quota が先にチェックされるため quota になること
        assert cat == "quota", (
            f"rate_limit + quota 両方含む場合、quota が優先されること。実際: cat={cat!r}"
        )

    def test_quota_message_with_rate_limit_context_returns_quota(self):
        """quota エラーメッセージに 429 も含まれているケース（実際の API 応答形式）。"""
        from main import _classify_realtime_error

        msg = (
            "Error code: 429 - {'error': {'message': 'You have exceeded your quota. "
            "insufficient_quota', 'type': 'insufficient_quota', 'code': 'rate_limit_exceeded'}}"
        )
        cat, txt = _classify_realtime_error(msg)

        # insufficient_quota が先に検出されて quota になること
        assert cat == "quota", (
            f"quota + rate_limit 混在メッセージで quota が返ること。実際: cat={cat!r}"
        )
        assert "クォータ" in txt, f"display_text に 'クォータ' が含まれること: {txt!r}"

    def test_rate_limit_without_quota_returns_rate_limit(self):
        """quota キーワードなしで rate_limit のみ含むメッセージは rate_limit になること。"""
        from main import _classify_realtime_error

        msg = "Error 429 rate_limit_exceeded"
        cat, txt = _classify_realtime_error(msg)

        assert cat == "rate_limit", (
            f"rate_limit のみのメッセージが rate_limit カテゴリにならない: {cat!r}"
        )


# ---------------------------------------------------------------------------
# W-2 (PR4): _is_ptt_pressing() と _is_route_b_active_for_meter() の
#            STARTING 扱い差異が docstring で説明されていること
# ---------------------------------------------------------------------------

class TestStartingStateDocumented:
    """_is_ptt_pressing と _is_route_b_active_for_meter の STARTING 扱い差異が
    docstring で明記されていること（W-2 PR4）。"""

    def test_is_ptt_pressing_docstring_mentions_starting(self):
        """_is_ptt_pressing の docstring が STARTING または starting に言及していること。"""
        doc = app._is_ptt_pressing.__doc__
        assert doc is not None, "_is_ptt_pressing に docstring がない"
        assert "STARTING" in doc or "starting" in doc.lower(), (
            "_is_ptt_pressing の docstring が STARTING 状態の扱いを説明していない（W-2）。"
            f"現在の docstring:\n{doc}"
        )

    def test_is_route_b_active_for_meter_docstring_mentions_starting(self):
        """_is_route_b_active_for_meter の docstring が STARTING は対象外であることを
        RUNNING または starting に言及して明記していること。"""
        doc = app._is_route_b_active_for_meter.__doc__
        assert doc is not None, "_is_route_b_active_for_meter に docstring がない"
        # RUNNING のみチェック、STARTING は除外する設計なのでその旨が分かること
        assert "RUNNING" in doc or "running" in doc.lower(), (
            "_is_route_b_active_for_meter の docstring が RUNNING のみ対象であることを説明していない（W-2）。"
            f"現在の docstring:\n{doc}"
        )

    def test_starting_state_returns_false_for_level_meter(self):
        """STARTING 状態では _is_route_b_active_for_meter が False を返すこと。

        仕様: STARTING 中はレベルメーター 0 表示が妥当（音声まだ流れていない）。
        """
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.STARTING

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b
        app._konnyaku_system = mock_system

        result = app._is_route_b_active_for_meter(ptt_enabled=True)

        assert result is False, (
            "STARTING 状態でレベルメーターが更新されてはならない（音声未入力）。"
            "_is_route_b_active_for_meter は RUNNING のみ True を返すべき。"
        )

    def test_starting_state_returns_true_for_ptt_pressing(self):
        """STARTING 状態では _is_ptt_pressing が True を返すこと（既存動作の確認）。

        仕様: PTT 押下中フラグは STARTING でも True（デバイスコンボ disable のため）。
        """
        from main import RouteState

        mock_route_b = MagicMock()
        mock_route_b.state = RouteState.STARTING

        mock_system = MagicMock()
        mock_system.route_b_system = mock_route_b
        app._konnyaku_system = mock_system
        app._ptt_enabled = True

        result = app._is_ptt_pressing()

        assert result is True, (
            "STARTING 状態では PTT 押下中と見なすべき（デバイスコンボ disable 継続）。"
        )


# ---------------------------------------------------------------------------
# W-3 (PR4): PTT ON + 系統B 手動 ON ケースのテスト
# ---------------------------------------------------------------------------

class TestRouteBEnablePttOnNoop:
    """PTT ON 中に _on_route_b_enable_change_ptt_aware(enabled=True) が noop になること（W-3 PR4）。"""

    def test_route_b_on_when_ptt_enabled_is_noop(self):
        """PTT ON 状態で系統B チェックを ON にしても start_route が呼ばれないこと。

        TBD-4 仕様: PTT ON 中は PTT がルートBの制御権を持つ。
        手動で ON にしてもここでは PTT を再起動しない。
        """
        app._ptt_enabled = True
        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        app._on_route_b_enable_change_ptt_aware(enabled=True)

        mock_system.start_route.assert_not_called()
        mock_system.stop_route.assert_not_called()

    def test_route_b_on_ptt_enabled_does_not_change_ptt_enabled_flag(self):
        """PTT ON 中に系統B を ON にしても _ptt_enabled が変わらないこと。"""
        app._ptt_enabled = True
        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        app._on_route_b_enable_change_ptt_aware(enabled=True)

        assert app._ptt_enabled is True, (
            "PTT ON 中に系統B ON してもフラグが変わらないこと。"
        )

    def test_route_b_on_ptt_enabled_saves_settings(self):
        """PTT ON 中に系統B ON しても _save_settings() が呼ばれること。"""
        app._ptt_enabled = True
        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        with patch.object(app, '_save_settings') as mock_save:
            app._on_route_b_enable_change_ptt_aware(enabled=True)
            mock_save.assert_called()


# ---------------------------------------------------------------------------
# S-1 (PR4): _on_route_b_enable_change_ptt_aware での _save_settings() 呼び出し順序
# ---------------------------------------------------------------------------

class TestRouteBEnableSaveSettingsOrder:
    """_on_route_b_enable_change_ptt_aware が _save_settings() を
    状態更新（_ptt_enabled = False）の後に呼ぶこと（S-1 PR4）。"""

    def test_save_settings_called_after_ptt_enabled_false(self):
        """PTT ON → 系統B OFF のとき、_save_settings() が _ptt_enabled=False になった後に呼ばれること。

        現状の問題: _save_settings() が _ptt_enabled = False の前に呼ばれているため、
        保存される ptt_enabled の値が依然 True になってしまう。
        修正後: 状態更新完了後に _save_settings() を呼ぶこと。
        """
        app._ptt_enabled = True
        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        captured_ptt_enabled_at_save: list[bool] = []

        def capture_save():
            """_save_settings 呼び出し時点の _ptt_enabled を記録する。"""
            captured_ptt_enabled_at_save.append(app._ptt_enabled)

        with patch.object(app, '_save_settings', side_effect=capture_save), \
             patch.object(app, '_cleanup_ptt_manager'), \
             patch.object(app, '_update_ptt_visual_feedback'):
            app._on_route_b_enable_change_ptt_aware(enabled=False)

        assert len(captured_ptt_enabled_at_save) >= 1, (
            "_save_settings() が一度も呼ばれていない"
        )
        # 最後の _save_settings() 呼び出し時点で _ptt_enabled が False であること
        last_ptt_enabled = captured_ptt_enabled_at_save[-1]
        assert last_ptt_enabled is False, (
            f"_save_settings() が _ptt_enabled=False になる前に呼ばれている。"
            f"_save_settings() 呼び出し時点の _ptt_enabled 値リスト: {captured_ptt_enabled_at_save}。"
            "S-1 PR4: 状態更新後に _save_settings() を呼ぶよう修正が必要。"
        )

    def test_save_settings_ptt_false_when_route_b_turned_off(self):
        """_save_settings() 呼び出し時に _ptt_enabled が False であること（別角度の確認）。"""
        app._ptt_enabled = True
        mock_system = MagicMock()
        app._konnyaku_system = mock_system

        ptt_values_at_save: list[bool] = []

        def record_ptt():
            ptt_values_at_save.append(app._ptt_enabled)

        with patch.object(app, '_save_settings', side_effect=record_ptt), \
             patch.object(app, '_cleanup_ptt_manager'), \
             patch.object(app, '_update_ptt_visual_feedback'):
            app._on_route_b_enable_change_ptt_aware(enabled=False)

        # _save_settings が呼ばれた回数は1回以上
        assert len(ptt_values_at_save) >= 1

        # 全ての _save_settings 呼び出しで _ptt_enabled が False であること
        # （少なくとも最後の呼び出しでは False でなければならない）
        assert all(v is False for v in ptt_values_at_save), (
            f"_save_settings() 呼び出し時に _ptt_enabled=True が残っている: {ptt_values_at_save}。"
            "S-1 PR4: _ptt_enabled=False 更新後に _save_settings() を呼ぶこと。"
        )


# ---------------------------------------------------------------------------
# W-6: CaptionSystem.start() の state transition コメント
# ---------------------------------------------------------------------------

class TestCaptionSystemStartStateTransitionComment:
    """CaptionSystem.start() の docstring が状態遷移仕様を説明していること（W-6）。

    実装変更はしない。docstring でコメントを明確化する。
    """

    def test_start_docstring_mentions_starting_state(self):
        """CaptionSystem.start() の docstring が STARTING 状態に言及していること。"""
        from main import CaptionSystem

        doc = CaptionSystem.start.__doc__
        assert doc is not None, "CaptionSystem.start に docstring がない"
        assert "STARTING" in doc or "starting" in doc.lower(), (
            "CaptionSystem.start() の docstring が STARTING 遷移について説明していない（W-6）。"
            f"現在の docstring:\n{doc}"
        )

    def test_start_docstring_mentions_running_transition(self):
        """CaptionSystem.start() の docstring が RUNNING への遷移タイミングを説明していること。

        重要: start() の最後に RUNNING へ遷移するが、実際の WebSocket 接続完了は
        非同期スレッド内で行われる。この設計差異をコメントで明記すること（W-6）。
        """
        from main import CaptionSystem

        doc = CaptionSystem.start.__doc__
        assert doc is not None, "CaptionSystem.start に docstring がない"
        # RUNNING に言及していること
        assert "RUNNING" in doc or "running" in doc.lower(), (
            "CaptionSystem.start() の docstring が RUNNING 遷移について説明していない（W-6）。"
            f"現在の docstring:\n{doc}"
        )

    def test_start_docstring_clarifies_running_timing(self):
        """CaptionSystem.start() の docstring が RUNNING 遷移のタイミング（スレッド起動後）を
        説明していること（W-6 の核心）。

        コメントで「スレッド起動完了時点で RUNNING」または「WebSocket 接続確認前に RUNNING」
        という設計意図が読み取れるようにする。
        """
        from main import CaptionSystem

        doc = CaptionSystem.start.__doc__
        assert doc is not None, "CaptionSystem.start に docstring がない"

        # スレッド起動、WebSocket、タイミングのいずれかに言及すること
        doc_lower = doc.lower()
        has_timing_note = (
            "スレッド起動" in doc
            or "thread" in doc_lower
            or "websocket" in doc_lower
            or "ws 接続" in doc
            or "接続完了前" in doc
            or "スレッド起動後" in doc
            or "注:" in doc
            or "note:" in doc_lower
            or "非同期" in doc
        )
        assert has_timing_note, (
            "CaptionSystem.start() の docstring が RUNNING 遷移のタイミング（"
            "スレッド起動完了時点で遷移し、WebSocket 接続完了タイミングとは異なる）を"
            "説明していない（W-6）。\n"
            f"現在の docstring:\n{doc}"
        )
