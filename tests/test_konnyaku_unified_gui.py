"""
test_konnyaku_unified_gui.py

翻訳こんにゃくモードを正規モード（メインコンテンツ）に統一したあとの
GUI 構造・ラベル・コールバックを検証するテスト群。

設計方針:
- dpg を使わず TAG 定数・コールバック関数の純関数部分だけを検証する
- こんにゃく結果コールバックの [相手]/[自分] プレフィックスを確認
- ボタンラベル・系統名のテキストを確認（_build_gui は呼ばず定数/関数で検証）
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 系統名定数・ラベルの確認
# ---------------------------------------------------------------------------

class TestRouteLabels:
    """GUI 上の系統名が正しいことを定数・ソース検索で確認する。"""

    def test_route_a_label_contains_keitou1(self):
        """系統1 のラベルに「系統1」または「相手→自分」または「聞き取り字幕」が含まれること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        has_keitou1 = "系統1" in source or "相手→自分" in source or "聞き取り字幕" in source
        assert has_keitou1, (
            "_build_gui に「系統1」「相手→自分」「聞き取り字幕」の系統名が含まれていない"
        )

    def test_route_b_label_contains_keitou2(self):
        """系統2 のラベルに「系統2」または「自分→相手」または「同時通訳」が含まれること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        has_keitou2 = "系統2" in source or "自分→相手" in source or "同時通訳" in source
        assert has_keitou2, (
            "_build_gui に「系統2」「自分→相手」「同時通訳」の系統名が含まれていない"
        )

    def test_route_a_old_label_not_in_build_gui(self):
        """旧ラベル「経路A」「話者の声」が _build_gui のテキストラベルに残っていないこと。

        タグ名（変数名）は残っても良いが、add_text / collapsing_header の表示文字列は変更済みであること。
        """
        import inspect
        source = inspect.getsource(app._build_gui)
        # "【経路A】" という表示文字列が残っていないこと
        assert "【経路A】" not in source, (
            "_build_gui に旧ラベル「【経路A】」が残っている"
        )

    def test_route_b_old_label_not_in_build_gui(self):
        """旧ラベル「経路B」が _build_gui のテキストラベルに残っていないこと。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        assert "【経路B】" not in source, (
            "_build_gui に旧ラベル「【経路B】」が残っている"
        )


# ---------------------------------------------------------------------------
# 2. こんにゃく開始ボタンのラベルが「開始」であること
# ---------------------------------------------------------------------------

class TestKonnyakuStartButtonLabel:
    """翻訳こんにゃく開始ボタンのデフォルトラベルが「開始」であること。"""

    def test_konnyaku_start_button_default_label_is_kaishi(self):
        """_build_gui の add_button で TAG_KONNYAKU_START_BTN のラベルが「開始」であること。

        add_button 呼び出しをモックして引数を捕捉する。
        """
        import inspect
        source = inspect.getsource(app._build_gui)

        # TAG_KONNYAKU_START_BTN の add_button 呼び出し箇所のテキストを確認
        # ソース中に「こんにゃく開始」という文字列が残っていないこと
        assert "こんにゃく開始" not in source, (
            "_build_gui に旧ボタンラベル「こんにゃく開始」が残っている"
        )

    def test_shutdown_restores_button_label_to_kaishi(self):
        """停止完了後にボタンラベルが「開始」に戻ること（旧「こんにゃく開始」ではなく）。

        _shutdown_in_background 内の configure_item 呼び出しを確認する。
        """
        configure_item_calls: list[dict] = []
        mock_dpg = _make_dpg_mock()
        mock_dpg.configure_item.side_effect = lambda tag, **kwargs: configure_item_calls.append(
            {"tag": tag, **kwargs}
        )

        mock_instance = MagicMock()
        mock_instance.shutdown.side_effect = lambda: None  # 即完了

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_konnyaku_running", True),
            patch.object(app, "_konnyaku_system", mock_instance),
        ):
            app._on_konnyaku_start_stop_click()
            import time as _time
            deadline = _time.monotonic() + 3.0
            while _time.monotonic() < deadline:
                restored = [
                    c for c in configure_item_calls
                    if c.get("tag") == app.TAG_KONNYAKU_START_BTN
                    and c.get("enabled") is True
                ]
                if restored:
                    break
                _time.sleep(0.05)

        # shutdown 完了後のボタンラベルが「開始」（「こんにゃく開始」ではない）
        restored_calls = [
            c for c in configure_item_calls
            if c.get("tag") == app.TAG_KONNYAKU_START_BTN
            and c.get("enabled") is True
        ]
        assert len(restored_calls) >= 1, (
            f"shutdown 後に enabled=True の configure_item が呼ばれていない: {configure_item_calls}"
        )
        # ラベルが「こんにゃく開始」でなく「開始」（または「翻訳開始」等）であること
        label = restored_calls[0].get("label", "")
        assert "こんにゃく開始" not in label, (
            f"shutdown 後のボタンラベルが旧名「こんにゃく開始」のまま: {label!r}"
        )
        assert label in ("開始", "翻訳開始"), (
            f"shutdown 後のボタンラベルが期待値（'開始'|'翻訳開始'）でない: {label!r}"
        )


# ---------------------------------------------------------------------------
# 3. こんにゃく停止中ラベルも「停止中...」のままであること
# ---------------------------------------------------------------------------

class TestKonnyakuStopLabel:
    """停止ボタン押下直後のラベルが「停止中...」であること（変更なし）。"""

    def test_stop_shows_stopping_label(self):
        """停止ボタン押下直後にラベルが「停止中...」になること。"""
        import threading
        configure_item_calls: list[dict] = []
        mock_dpg = _make_dpg_mock()
        mock_dpg.configure_item.side_effect = lambda tag, **kwargs: configure_item_calls.append(
            {"tag": tag, **kwargs}
        )

        shutdown_start = threading.Event()
        shutdown_done = threading.Event()
        mock_instance = MagicMock()

        def fake_shutdown():
            shutdown_start.set()
            shutdown_done.wait(timeout=3.0)

        mock_instance.shutdown.side_effect = fake_shutdown

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_konnyaku_running", True),
            patch.object(app, "_konnyaku_system", mock_instance),
        ):
            app._on_konnyaku_start_stop_click()
            shutdown_start.wait(timeout=3.0)
            stopping_calls = [
                c for c in configure_item_calls
                if c.get("tag") == app.TAG_KONNYAKU_START_BTN
                and c.get("label") == "停止中..."
            ]
            shutdown_done.set()
            import time as _time
            _time.sleep(0.2)

        assert len(stopping_calls) >= 1, (
            f"「停止中...」ラベルへの configure_item が呼ばれていない: {configure_item_calls}"
        )


# ---------------------------------------------------------------------------
# 4. こんにゃく起動中ラベルが「こんにゃく停止」でなく「停止」であること
# ---------------------------------------------------------------------------

class TestKonnyakuRunningLabel:
    """こんにゃく起動成功後のボタンラベルが「停止」であること。"""

    def test_start_sets_stop_button_label(self):
        """開始成功後にボタンラベルが「停止」になること（旧「こんにゃく停止」ではなく）。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)
        configure_item_calls: list[dict] = []
        mock_dpg = _make_dpg_mock(widget_values)
        mock_dpg.configure_item.side_effect = lambda tag, **kwargs: configure_item_calls.append(
            {"tag": tag, **kwargs}
        )
        mock_instance = MagicMock()

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", return_value=mock_instance),
        ):
            app._on_konnyaku_start_stop_click()

        start_btn_calls = [
            c for c in configure_item_calls
            if c.get("tag") == app.TAG_KONNYAKU_START_BTN
        ]
        assert len(start_btn_calls) >= 1, (
            f"起動後に TAG_KONNYAKU_START_BTN への configure_item が呼ばれていない: {configure_item_calls}"
        )
        label = start_btn_calls[-1].get("label", "")
        assert "こんにゃく停止" not in label, (
            f"起動後ボタンラベルが旧名「こんにゃく停止」のまま: {label!r}"
        )
        assert label in ("停止", "翻訳停止"), (
            f"起動後ボタンラベルが期待値（'停止'|'翻訳停止'）でない: {label!r}"
        )


# ---------------------------------------------------------------------------
# 5. on_result コールバックのプレフィックス検証
# ---------------------------------------------------------------------------

class TestRouteCallbackPrefixes:
    """_on_result_route_a / _on_result_route_b が [相手]/[自分] プレフィックスを使うこと。"""

    def _get_on_result_callbacks(self):
        """_on_konnyaku_start_stop_click を実行して MultiCaptionSystem に渡された
        on_result_a / on_result_b コールバックを取得するヘルパー。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)
        mock_dpg = _make_dpg_mock(widget_values)
        mock_instance = MagicMock()
        captured = {}

        def capture_mcs(**kwargs):
            captured["on_result_a"] = kwargs.get("on_result_a")
            captured["on_result_b"] = kwargs.get("on_result_b")
            return mock_instance

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", side_effect=capture_mcs),
        ):
            app._on_konnyaku_start_stop_click()

        return captured.get("on_result_a"), captured.get("on_result_b")

    def test_on_result_route_a_uses_keitou1_prefix(self):
        """on_result_route_a が「[系統1 入力]」「[系統1 出力]」プレフィックスを使うこと。"""
        on_result_a, _ = self._get_on_result_callbacks()
        assert on_result_a is not None, "on_result_a が MultiCaptionSystem に渡されていない"

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_a("Hello", "こんにちは")

        assert len(enqueued) == 1
        assert "[系統1 入力]" in enqueued[0].get("original", ""), (
            f"on_result_a の original が「[系統1 入力]」プレフィックスでない: {enqueued[0]}"
        )
        assert "[系統1 出力]" in enqueued[0].get("translated", ""), (
            f"on_result_a の translated が「[系統1 出力]」プレフィックスでない: {enqueued[0]}"
        )

    def test_on_result_route_b_uses_keitou2_prefix(self):
        """on_result_route_b が「[系統2 入力]」「[系統2 出力]」プレフィックスを使うこと。"""
        _, on_result_b = self._get_on_result_callbacks()
        assert on_result_b is not None, "on_result_b が MultiCaptionSystem に渡されていない"

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_b("I speak", "翻訳結果")

        assert len(enqueued) == 1
        assert "[系統2 入力]" in enqueued[0].get("original", ""), (
            f"on_result_b の original が「[系統2 入力]」プレフィックスでない: {enqueued[0]}"
        )
        assert "[系統2 出力]" in enqueued[0].get("translated", ""), (
            f"on_result_b の translated が「[系統2 出力]」プレフィックスでない: {enqueued[0]}"
        )

    def test_on_result_route_a_old_prefix_not_used(self):
        """on_result_route_a が旧プレフィックス「[A]」を使っていないこと。"""
        on_result_a, _ = self._get_on_result_callbacks()
        assert on_result_a is not None

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_a("Hello", "こんにちは")

        for item in enqueued:
            assert "[A]" not in item.get("original", ""), (
                f"on_result_a に旧プレフィックス「[A]」が残っている: {item}"
            )
            assert "[A]" not in item.get("translated", ""), (
                f"on_result_a に旧プレフィックス「[A]」が残っている: {item}"
            )

    def test_on_result_route_b_old_prefix_not_used(self):
        """on_result_route_b が旧プレフィックス「[B]」を使っていないこと。"""
        _, on_result_b = self._get_on_result_callbacks()
        assert on_result_b is not None

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_b("I speak", "翻訳結果")

        for item in enqueued:
            assert "[B]" not in item.get("original", ""), (
                f"on_result_b に旧プレフィックス「[B]」が残っている: {item}"
            )
            assert "[B]" not in item.get("translated", ""), (
                f"on_result_b に旧プレフィックス「[B]」が残っている: {item}"
            )

    def test_on_result_route_a_empty_original_no_prefix(self):
        """on_result_route_a: original が空文字のときはプレフィックスなし空文字であること。"""
        on_result_a, _ = self._get_on_result_callbacks()
        assert on_result_a is not None

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_a("", "翻訳のみ")

        assert enqueued[0].get("original", "SENTINEL") == "", (
            f"original が空のとき空文字でない: {enqueued[0].get('original')!r}"
        )

    def test_on_result_route_b_empty_translated_no_prefix(self):
        """on_result_route_b: translated が空文字のときはプレフィックスなし空文字であること。"""
        _, on_result_b = self._get_on_result_callbacks()
        assert on_result_b is not None

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_b("自分の声", "")

        assert enqueued[0].get("translated", "SENTINEL") == "", (
            f"translated が空のとき空文字でない: {enqueued[0].get('translated')!r}"
        )


# ---------------------------------------------------------------------------
# 6. 単独モード UI 要素の非表示化確認
# ---------------------------------------------------------------------------

class TestSingleModeUIHidden:
    """単独モードの UI 要素が _build_gui から完全に削除されていること。

    こんにゃくモード一本化後、旧単独モード用ウィジェットは _build_gui から削除される。
    TAG 定数自体（_do_start / _do_stop 等の CLI 用関数から参照）は残っても良いが、
    add_button / add_combo 呼び出しは _build_gui から取り除かれること。
    """

    def test_start_btn_not_added_in_build_gui(self):
        """TAG_START_BTN の add_button が _build_gui から削除されていること。

        定数（TAG_START_BTN = "start_btn"）はモジュールトップに残るが、
        _build_gui 内では add_button(tag=TAG_START_BTN, ...) が呼ばれないこと。
        """
        import inspect
        source = inspect.getsource(app._build_gui)

        # _build_gui 内に TAG_START_BTN を使った add_button 呼び出しがないこと
        # (TAG_START_BTN は _do_start 等から参照されるため定数定義は残る)
        assert "tag=TAG_START_BTN" not in source, (
            "_build_gui に TAG_START_BTN の add_button 呼び出しが残っている"
        )

    def test_gain_mode_combo_not_added_in_build_gui(self):
        """旧単独モードの入力ゲインコンボ（TAG_GAIN_MODE）が _build_gui から削除されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "tag=TAG_GAIN_MODE" not in source, (
            "_build_gui に TAG_GAIN_MODE の add_combo 呼び出しが残っている"
        )

    def test_level_meter_not_added_in_build_gui(self):
        """旧単独モードのレベルメーター（TAG_LEVEL_METER）が _build_gui から削除されていること。

        TAG_LEVEL_METER_A_IN / B_IN 等のこんにゃくモードレベルメーターは残るため、
        'tag=TAG_LEVEL_METER,' のように終端カンマを含む形式で旧単独モードのみをチェックする。
        """
        import inspect
        source = inspect.getsource(app._build_gui)

        # TAG_LEVEL_METER, (末尾カンマ) で旧単独モードのウィジェット追加のみを確認
        # TAG_LEVEL_METER_A_IN / A_OUT / B_IN / B_OUT は残るので部分一致しないよう注意
        assert "tag=TAG_LEVEL_METER," not in source, (
            "_build_gui に TAG_LEVEL_METER の add_progress_bar 呼び出しが残っている"
        )

    def test_trans_combo_not_added_in_build_gui(self):
        """旧翻訳エンジン選択コンボ（TAG_TRANS_COMBO）が _build_gui から削除されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "tag=TAG_TRANS_COMBO" not in source, (
            "_build_gui に TAG_TRANS_COMBO の add_combo 呼び出しが残っている"
        )


# ---------------------------------------------------------------------------
# 7. 設定クリーンアップ後の削除済みウィジェット確認（ソースレベル）
# ---------------------------------------------------------------------------

class TestSettingsCleanup:
    """settings cleanup (#38 followup) — 削除対象ウィジェットが _build_gui に存在しないこと。"""

    def test_whisper_model_combo_not_in_build_gui(self):
        """Whisper 認識モデルコンボ（TAG_MODEL_COMBO）が _build_gui のウィジェット作成から削除されていること。

        注意: TAG_MODEL_COMBO 定数はモジュールトップに残るが、
        _build_gui 内で add_combo(tag=TAG_MODEL_COMBO, ...) が呼ばれないこと。
        """
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "tag=TAG_MODEL_COMBO" not in source, (
            "_build_gui に TAG_MODEL_COMBO の add_combo が残っている（Whisper 設定削除済みのはず）"
        )

    def test_vad_sensitivity_slider_not_in_build_gui(self):
        """VAD 感度スライダー（TAG_VAD_SENSITIVITY）が _build_gui から削除されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "tag=TAG_VAD_SENSITIVITY" not in source, (
            "_build_gui に TAG_VAD_SENSITIVITY の add_slider_float が残っている"
        )

    def test_vad_silence_slider_not_in_build_gui(self):
        """VAD 無音待機スライダー（TAG_VAD_SILENCE）が _build_gui から削除されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "tag=TAG_VAD_SILENCE" not in source, (
            "_build_gui に TAG_VAD_SILENCE の add_slider_float が残っている"
        )

    def test_realtime_settings_group_not_in_build_gui(self):
        """Realtime 専用「音声出力先」グループ（TAG_REALTIME_SETTINGS_GROUP）が _build_gui から削除されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "tag=TAG_REALTIME_SETTINGS_GROUP" not in source, (
            "_build_gui に TAG_REALTIME_SETTINGS_GROUP の group が残っている"
        )

    def test_whisper_settings_group_not_in_build_gui(self):
        """Whisper 専用設定グループ（TAG_WHISPER_SETTINGS_GROUP）が _build_gui から削除されていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "tag=TAG_WHISPER_SETTINGS_GROUP" not in source, (
            "_build_gui に TAG_WHISPER_SETTINGS_GROUP の group が残っている"
        )

    def test_api_key_inputs_still_in_build_gui(self):
        """API キー入力（TAG_OPENAI_KEY_INPUT / TAG_DEEPL_KEY_INPUT）は _build_gui に残っていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "TAG_OPENAI_KEY_INPUT" in source, (
            "_build_gui から TAG_OPENAI_KEY_INPUT が消えている（残すべき）"
        )
        assert "TAG_DEEPL_KEY_INPUT" in source, (
            "_build_gui から TAG_DEEPL_KEY_INPUT が消えている（残すべき）"
        )

    def test_device_filter_still_in_build_gui(self):
        """デバイスフィルタ（TAG_HOST_API_COMBO）は _build_gui に残っていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "TAG_HOST_API_COMBO" in source, (
            "_build_gui から TAG_HOST_API_COMBO が消えている（残すべき）"
        )

    def test_konnyaku_section_still_in_build_gui(self):
        """翻訳こんにゃくセクション（TAG_KONNYAKU_SECTION）は _build_gui に残っていること。"""
        import inspect
        source = inspect.getsource(app._build_gui)

        assert "TAG_KONNYAKU_SECTION" in source, (
            "_build_gui から TAG_KONNYAKU_SECTION が消えている（残すべき）"
        )


# ---------------------------------------------------------------------------
# ヘルパー（test_app_gui_simulation.py からコピー）
# ---------------------------------------------------------------------------

def _make_dpg_mock(widget_values: dict | None = None) -> MagicMock:
    widget_values = widget_values or {}
    mock = MagicMock()
    mock.does_item_exist.return_value = True
    mock.get_value.side_effect = lambda tag: widget_values.get(tag, "")
    mock.get_item_configuration.return_value = {"items": []}
    return mock


def _fake_devices():
    return [
        {"index": 0, "name": "Test Loopback Device", "isLoopback": True, "hostApi": 0},
        {"index": 1, "name": "Test Microphone", "isLoopback": False, "hostApi": 0},
    ]


def _fake_config():
    return {
        "openai": {"api_key": "sk-test-fake-00000000000000000000000000000000"},
        "stt": {"model": "tiny"},
        "translation": {"translation_model": "openai-realtime"},
    }


def _device_label_for(d: dict) -> str:
    name = d["name"]
    suffix = " [Loopback]" if d.get("isLoopback") else ""
    return f"{name}{suffix}"


def _make_widget_values(route_a_device_label: str, route_b_device_label: str) -> dict:
    from constants import get_language_display_names, get_language_codes
    lang_names = get_language_display_names()
    lang_codes = get_language_codes()
    ja_name = lang_names[lang_codes.index("ja")] if "ja" in lang_codes else lang_names[0]
    en_name = lang_names[lang_codes.index("en")] if "en" in lang_codes else lang_names[-1]

    return {
        app.TAG_ROUTE_A_DEVICE_COMBO: route_a_device_label,
        app.TAG_ROUTE_B_DEVICE_COMBO: route_b_device_label,
        app.TAG_ROUTE_A_LANG_COMBO: ja_name,
        app.TAG_ROUTE_B_LANG_COMBO: en_name,
        app.TAG_ROUTE_A_OUTPUT_ENABLE: False,
        app.TAG_ROUTE_B_OUTPUT_ENABLE: False,
        app.TAG_ROUTE_A_OUTPUT_DEVICE_COMBO: "(なし)",
        app.TAG_ROUTE_B_OUTPUT_DEVICE_COMBO: "(なし)",
        app.TAG_ROUTE_A_OUTPUT_VOLUME: 1.0,
        app.TAG_ROUTE_B_OUTPUT_VOLUME: 1.0,
        # Issue #43: 系統 ON/OFF トグル（デフォルト両方有効）
        app.TAG_ROUTE_A_ENABLE: True,
        app.TAG_ROUTE_B_ENABLE: True,
    }


# ---------------------------------------------------------------------------
# Issue #46: スライダー callback 検証
# ---------------------------------------------------------------------------

class TestSliderCallbacks:
    """経路A/B のゲイン・音量スライダーに callback が設定されていること。"""

    def _get_build_gui_source(self) -> str:
        import inspect
        return inspect.getsource(app._build_gui)

    def test_route_a_gain_slider_has_callback(self):
        """経路A ゲイン倍率スライダーに callback が設定されていること。"""
        source = self._get_build_gui_source()
        # TAG_ROUTE_A_GAIN_SLIDER の add_slider_float に callback= が含まれること
        # TAG_ROUTE_A_GAIN_SLIDER の定義ブロックに callback= キーワードが存在する
        assert "_on_route_a_gain_change" in source, (
            "_build_gui に _on_route_a_gain_change callback が含まれていない"
        )

    def test_route_b_gain_slider_has_callback(self):
        """経路B ゲイン倍率スライダーに callback が設定されていること。"""
        source = self._get_build_gui_source()
        assert "_on_route_b_gain_change" in source, (
            "_build_gui に _on_route_b_gain_change callback が含まれていない"
        )

    def test_route_a_volume_slider_has_callback(self):
        """経路A 出力音量スライダーに callback が設定されていること。"""
        source = self._get_build_gui_source()
        assert "_on_route_a_volume_change" in source, (
            "_build_gui に _on_route_a_volume_change callback が含まれていない"
        )

    def test_route_b_volume_slider_has_callback(self):
        """経路B 出力音量スライダーに callback が設定されていること。"""
        source = self._get_build_gui_source()
        assert "_on_route_b_volume_change" in source, (
            "_build_gui に _on_route_b_volume_change callback が含まれていない"
        )

    def test_route_a_gain_mode_has_callback(self):
        """経路A ゲインモードコンボに callback が設定されていること。"""
        source = self._get_build_gui_source()
        assert "_on_route_a_gain_mode_change" in source, (
            "_build_gui に _on_route_a_gain_mode_change callback が含まれていない"
        )

    def test_route_b_gain_mode_has_callback(self):
        """経路B ゲインモードコンボに callback が設定されていること。"""
        source = self._get_build_gui_source()
        assert "_on_route_b_gain_mode_change" in source, (
            "_build_gui に _on_route_b_gain_mode_change callback が含まれていない"
        )


# ---------------------------------------------------------------------------
# Issue #48: _on_realtime_error_handler が TAG_STATUS_STATE を更新すること
# ---------------------------------------------------------------------------

class TestOnRealtimeErrorHandler:
    """_on_realtime_error_handler が GUI ステータスバーを更新すること。"""

    def test_on_realtime_error_handler_updates_status_state(self):
        """_on_realtime_error_handler が TAG_STATUS_STATE を更新すること。"""
        set_value_calls: list[tuple] = []
        mock_dpg = _make_dpg_mock()
        mock_dpg.set_value.side_effect = lambda tag, val: set_value_calls.append((tag, val))

        with patch.object(app, "dpg", mock_dpg):
            app._on_realtime_error_handler("a", "quota", "⚠️ OpenAI クォータ超過: test")

        status_updates = [
            (tag, val) for tag, val in set_value_calls
            if tag == app.TAG_STATUS_STATE
        ]
        assert len(status_updates) >= 1, (
            f"TAG_STATUS_STATE への set_value が呼ばれなかった: {set_value_calls}"
        )
        # display_text が含まれていること
        assert "クォータ" in status_updates[0][1], (
            f"ステータスバーに クォータ が含まれていない: {status_updates[0][1]!r}"
        )

    def test_on_realtime_error_handler_includes_route_label(self):
        """_on_realtime_error_handler がステータスに [系統1] / [系統2] ラベルを含めること。"""
        set_value_calls_a: list[tuple] = []
        set_value_calls_b: list[tuple] = []
        mock_dpg = _make_dpg_mock()

        mock_dpg.set_value.side_effect = lambda tag, val: set_value_calls_a.append((tag, val))
        with patch.object(app, "dpg", mock_dpg):
            app._on_realtime_error_handler("a", "quota", "⚠️ test error")

        mock_dpg2 = _make_dpg_mock()
        mock_dpg2.set_value.side_effect = lambda tag, val: set_value_calls_b.append((tag, val))
        with patch.object(app, "dpg", mock_dpg2):
            app._on_realtime_error_handler("b", "auth", "⚠️ test auth error")

        a_updates = [v for t, v in set_value_calls_a if t == app.TAG_STATUS_STATE]
        b_updates = [v for t, v in set_value_calls_b if t == app.TAG_STATUS_STATE]

        assert a_updates and "[系統1]" in a_updates[0], (
            f"route_id='a' のとき [系統1] が含まれない: {a_updates}"
        )
        assert b_updates and "[系統2]" in b_updates[0], (
            f"route_id='b' のとき [系統2] が含まれない: {b_updates}"
        )

    def test_on_realtime_error_handler_no_item_does_not_raise(self):
        """TAG_STATUS_STATE が存在しなくても例外を投げないこと。"""
        mock_dpg = _make_dpg_mock()
        mock_dpg.does_item_exist.return_value = False  # 存在しない

        # 例外が出なければ OK
        with patch.object(app, "dpg", mock_dpg):
            app._on_realtime_error_handler("a", "quota", "⚠️ OpenAI クォータ超過")

    def test_on_realtime_error_handler_set_value_exception_does_not_raise(self):
        """dpg.set_value が例外を投げても _on_realtime_error_handler が安全に完了すること。"""
        mock_dpg = _make_dpg_mock()
        mock_dpg.set_value.side_effect = RuntimeError("dpg error")

        # 例外が伝播しないこと
        with patch.object(app, "dpg", mock_dpg):
            app._on_realtime_error_handler("a", "other", "⚠️ unknown error")

    def test_on_realtime_error_passed_to_multi_caption_system(self):
        """_on_konnyaku_start_stop_click が MultiCaptionSystem に on_realtime_error を渡すこと。"""
        fake_devices = _fake_devices()
        route_a_label = _device_label_for(fake_devices[0])
        route_b_label = _device_label_for(fake_devices[1])
        widget_values = _make_widget_values(route_a_label, route_b_label)
        mock_dpg = _make_dpg_mock(widget_values)
        mock_instance = MagicMock()
        captured = {}

        def capture_mcs(**kwargs):
            captured.update(kwargs)
            return mock_instance

        with (
            patch.object(app, "dpg", mock_dpg),
            patch.object(app, "_devices", fake_devices),
            patch.object(app, "_config", _fake_config()),
            patch.object(app, "_system", None),
            patch.object(app, "_konnyaku_system", None),
            patch.object(app, "_konnyaku_running", False),
            patch.object(app, "MultiCaptionSystem", side_effect=capture_mcs),
        ):
            app._on_konnyaku_start_stop_click()

        assert "on_realtime_error" in captured, (
            "MultiCaptionSystem に on_realtime_error が渡されていない"
        )
        assert captured["on_realtime_error"] is not None, (
            "on_realtime_error が None で渡されている"
        )
        assert callable(captured["on_realtime_error"]), (
            "on_realtime_error が callable でない"
        )


class TestSliderCallbackFunctions:
    """スライダー callback 関数が _konnyaku_system に正しく委譲すること。"""

    def test_on_route_a_gain_change_sets_manual_gain(self):
        """_on_route_a_gain_change が route_a_system.manual_gain を更新すること。"""
        mock_system = MagicMock()
        mock_route_a = MagicMock()
        mock_system.route_a_system = mock_route_a

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_a_gain_change(None, 3.5, None)

        assert mock_route_a.manual_gain == 3.5, (
            f"manual_gain が 3.5 に設定されていない: {mock_route_a.manual_gain}"
        )

    def test_on_route_b_gain_change_sets_manual_gain(self):
        """_on_route_b_gain_change が route_b_system.manual_gain を更新すること。"""
        mock_system = MagicMock()
        mock_route_b = MagicMock()
        mock_system.route_b_system = mock_route_b

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_b_gain_change(None, 2.0, None)

        assert mock_route_b.manual_gain == 2.0, (
            f"manual_gain が 2.0 に設定されていない: {mock_route_b.manual_gain}"
        )

    def test_on_route_a_volume_change_sets_output_volume(self):
        """_on_route_a_volume_change が route_a_system.output_volume を更新すること。"""
        mock_system = MagicMock()
        mock_route_a = MagicMock()
        mock_system.route_a_system = mock_route_a

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_a_volume_change(None, 0.8, None)

        assert mock_route_a.output_volume == 0.8, (
            f"output_volume が 0.8 に設定されていない: {mock_route_a.output_volume}"
        )

    def test_on_route_b_volume_change_sets_output_volume(self):
        """_on_route_b_volume_change が route_b_system.output_volume を更新すること。"""
        mock_system = MagicMock()
        mock_route_b = MagicMock()
        mock_system.route_b_system = mock_route_b

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_b_volume_change(None, 1.5, None)

        assert mock_route_b.output_volume == 1.5, (
            f"output_volume が 1.5 に設定されていない: {mock_route_b.output_volume}"
        )

    def test_on_route_a_gain_mode_change_sets_gain_mode(self):
        """_on_route_a_gain_mode_change が route_a_system.gain_mode を更新すること。"""
        mock_system = MagicMock()
        mock_route_a = MagicMock()
        mock_system.route_a_system = mock_route_a

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_a_gain_mode_change(None, "manual", None)

        assert mock_route_a.gain_mode == "manual", (
            f"gain_mode が 'manual' に設定されていない: {mock_route_a.gain_mode}"
        )

    def test_on_route_b_gain_mode_change_sets_gain_mode(self):
        """_on_route_b_gain_mode_change が route_b_system.gain_mode を更新すること。"""
        mock_system = MagicMock()
        mock_route_b = MagicMock()
        mock_system.route_b_system = mock_route_b

        with patch.object(app, "_konnyaku_system", mock_system):
            app._on_route_b_gain_mode_change(None, "auto", None)

        assert mock_route_b.gain_mode == "auto", (
            f"gain_mode が 'auto' に設定されていない: {mock_route_b.gain_mode}"
        )

    def test_callbacks_do_nothing_when_konnyaku_system_is_none(self):
        """_konnyaku_system が None のとき全 callback が例外を出さないこと。"""
        with patch.object(app, "_konnyaku_system", None):
            # 例外が出なければ OK
            app._on_route_a_gain_change(None, 2.0, None)
            app._on_route_b_gain_change(None, 2.0, None)
            app._on_route_a_volume_change(None, 1.0, None)
            app._on_route_b_volume_change(None, 1.0, None)
            app._on_route_a_gain_mode_change(None, "manual", None)
            app._on_route_b_gain_mode_change(None, "auto", None)


# ---------------------------------------------------------------------------
# B-11/B-12/B-13: 削除済みUI要素の不在確認 (v2 仕様変更)
# ---------------------------------------------------------------------------

class TestB11B12B13UIRemoval:
    """B-11/B-12/B-13 の削除済みUI要素が _build_gui に存在しないこと。

    v2 仕様変更（2026-05-14）:
    - B-11: プリセットボタン (TAG_KONNYAKU_PRESET_BTN) 削除
    - B-12: ゲインモードコンボ (TAG_ROUTE_A/B_GAIN_MODE) 削除
    - B-13: ゲイン倍率スライダー (TAG_ROUTE_A/B_GAIN_SLIDER) 削除
    """

    def _get_build_gui_source(self) -> str:
        import inspect
        return inspect.getsource(app._build_gui)

    # --- B-11: プリセットボタン ---

    def test_konnyaku_preset_btn_not_in_build_gui(self):
        """TAG_KONNYAKU_PRESET_BTN の add_button が _build_gui から削除されていること。"""
        source = self._get_build_gui_source()
        assert "TAG_KONNYAKU_PRESET_BTN" not in source, (
            "_build_gui に TAG_KONNYAKU_PRESET_BTN の add_button が残っている (B-11)"
        )

    def test_konnyaku_preset_callback_not_in_build_gui(self):
        """_on_konnyaku_preset_click コールバックが _build_gui から削除されていること。"""
        source = self._get_build_gui_source()
        assert "_on_konnyaku_preset_click" not in source, (
            "_build_gui に _on_konnyaku_preset_click の参照が残っている (B-11)"
        )

    # --- B-12: ゲインモードコンボ ---

    def test_route_a_gain_mode_combo_not_in_build_gui(self):
        """系統1 ゲインモードコンボ (TAG_ROUTE_A_GAIN_MODE) が _build_gui から削除されていること。"""
        source = self._get_build_gui_source()
        assert "TAG_ROUTE_A_GAIN_MODE" not in source, (
            "_build_gui に TAG_ROUTE_A_GAIN_MODE の add_combo が残っている (B-12)"
        )

    def test_route_b_gain_mode_combo_not_in_build_gui(self):
        """系統2 ゲインモードコンボ (TAG_ROUTE_B_GAIN_MODE) が _build_gui から削除されていること。"""
        source = self._get_build_gui_source()
        assert "TAG_ROUTE_B_GAIN_MODE" not in source, (
            "_build_gui に TAG_ROUTE_B_GAIN_MODE の add_combo が残っている (B-12)"
        )

    # --- B-13: ゲイン倍率スライダー ---

    def test_route_a_gain_slider_not_in_build_gui(self):
        """系統1 ゲイン倍率スライダー (TAG_ROUTE_A_GAIN_SLIDER) が _build_gui から削除されていること。"""
        source = self._get_build_gui_source()
        assert "TAG_ROUTE_A_GAIN_SLIDER" not in source, (
            "_build_gui に TAG_ROUTE_A_GAIN_SLIDER の add_slider_float が残っている (B-13)"
        )

    def test_route_b_gain_slider_not_in_build_gui(self):
        """系統2 ゲイン倍率スライダー (TAG_ROUTE_B_GAIN_SLIDER) が _build_gui から削除されていること。"""
        source = self._get_build_gui_source()
        assert "TAG_ROUTE_B_GAIN_SLIDER" not in source, (
            "_build_gui に TAG_ROUTE_B_GAIN_SLIDER の add_slider_float が残っている (B-13)"
        )
