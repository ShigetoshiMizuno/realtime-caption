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

    def test_route_a_label_contains_aitehitomi(self):
        """経路A のラベルに「相手→自分」または「聞き取り字幕」が含まれること。

        _build_gui() の add_text 呼び出し部分を文字列検索で確認する。
        """
        import inspect
        source = inspect.getsource(app._build_gui)
        # 「相手→自分」または「聞き取り字幕」の表記が含まれること
        has_aitehitomi = "相手→自分" in source or "相手" in source
        assert has_aitehitomi, (
            "_build_gui に「相手→自分」や「相手」の系統名が含まれていない"
        )

    def test_route_b_label_contains_jibunkaraate(self):
        """経路B のラベルに「自分→相手」または「同時通訳」が含まれること。"""
        import inspect
        source = inspect.getsource(app._build_gui)
        has_jibunkaraate = "自分→相手" in source or "同時通訳" in source
        assert has_jibunkaraate, (
            "_build_gui に「自分→相手」や「同時通訳」の系統名が含まれていない"
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

    def test_on_result_route_a_uses_aite_prefix(self):
        """on_result_route_a が「[相手]」プレフィックスを使うこと。"""
        on_result_a, _ = self._get_on_result_callbacks()
        assert on_result_a is not None, "on_result_a が MultiCaptionSystem に渡されていない"

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_a("Hello", "こんにちは")

        assert len(enqueued) == 1
        assert "[相手]" in enqueued[0].get("original", ""), (
            f"on_result_a の original が「[相手]」プレフィックスでない: {enqueued[0]}"
        )
        assert "[相手]" in enqueued[0].get("translated", ""), (
            f"on_result_a の translated が「[相手]」プレフィックスでない: {enqueued[0]}"
        )

    def test_on_result_route_b_uses_jibun_prefix(self):
        """on_result_route_b が「[自分]」プレフィックスを使うこと。"""
        _, on_result_b = self._get_on_result_callbacks()
        assert on_result_b is not None, "on_result_b が MultiCaptionSystem に渡されていない"

        enqueued: list[dict] = []
        with patch.object(app, "_enqueue", side_effect=lambda cmd, **kw: enqueued.append({"cmd": cmd, **kw})):
            on_result_b("I speak", "翻訳結果")

        assert len(enqueued) == 1
        assert "[自分]" in enqueued[0].get("original", ""), (
            f"on_result_b の original が「[自分]」プレフィックスでない: {enqueued[0]}"
        )
        assert "[自分]" in enqueued[0].get("translated", ""), (
            f"on_result_b の translated が「[自分]」プレフィックスでない: {enqueued[0]}"
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
    """単独モードの UI 要素が show=False で非表示になっていること。

    _build_gui() の add_combo / add_button 呼び出しを mock して引数を捕捉し、
    TAG_START_BTN / TAG_DEVICE_COMBO / TAG_TRANS_COMBO 等が show=False で
    作られることを確認する。

    注意: これらを完全に show=False にするか削除するかは実装依存だが、
    TAG_START_BTN については「非表示化」することが仕様要件。
    """

    def test_start_btn_hidden_in_main_ui(self):
        """TAG_START_BTN が _build_gui で非表示グループに入っているか、削除されていること。

        ソースコードレベルで以下いずれかを確認する:
        - パターン1: add_button(tag=TAG_START_BTN, ...) が show=False グループ内にある
                     → ソースに 'show=False' と 'TAG_START_BTN' の両方が含まれる
        - パターン2: add_button 呼び出し自体が削除され TAG 定数のみ残っている
                     → 'add_button' の行に 'TAG_START_BTN' が含まれない
        """
        import inspect
        source = inspect.getsource(app._build_gui)

        # TAG_START_BTN の add_button が存在すること
        assert "TAG_START_BTN" in source, "_build_gui に TAG_START_BTN の定義がない"

        # 仕様: TAG_START_BTN は show=False の group 内に配置する（こんにゃくモードに統合）
        # ソース中に show=False と TAG_START_BTN の両方が含まれること
        in_build_gui_with_show_false = (
            'show=False' in source and 'TAG_START_BTN' in source
        )
        assert in_build_gui_with_show_false, (
            "_build_gui で TAG_START_BTN が show=False グループ内に配置されていない"
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
    }
