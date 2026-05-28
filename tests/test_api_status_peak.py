"""
tests/test_api_status_peak.py

/api/status レスポンスに route_a_peak_level / route_b_peak_level 等を追加する
テスト（api-status-add-peak-level 案件）。

テストケース:
  1. test_status_includes_route_a_peak_level  : フィールド存在確認（route_a）
  2. test_status_includes_route_b_peak_level  : フィールド存在確認（route_b）
  3. test_route_a_peak_zero_when_system_none  : _konnyaku_system=None → 0 を返す
  4. test_route_a_peak_zero_when_route_a_none : route_a_system=None → 0 を返す
  5. test_route_a_peak_returns_audio_peak     : モック audio_peak=12345 → 12345 を返す

RED フェーズ: 現状コードにはフィールドが存在しないため全件 FAIL を期待する。
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_test_client():
    """Flask テストクライアントを構築して返す。"""
    flask_app = app._make_flask_app()
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def _mock_dpg():
    """dpg をモックする。does_item_exist=False で全ウィジェット無効にする。"""
    m = MagicMock()
    m.does_item_exist.return_value = False
    m.get_value.return_value = ""
    return m


def _make_mock_route_system(audio_peak: int = 0) -> MagicMock:
    """audio_peak と JSON シリアライズ可能な属性を持つ CaptionSystem モックを返す。

    api_status ハンドラは route_b_system.state.name と route_b_system.audio_gate_open も
    参照するため、JSON シリアライズ可能な値を返すようにする。
    """
    route = MagicMock()
    route.audio_peak = audio_peak
    # route_b_state = route_b_system.state.name → 文字列
    route.state.name = "IDLE"
    # route_b_audio_gate = route_b_system.audio_gate_open → bool
    route.audio_gate_open = False
    return route


def _make_mock_konnyaku_system(
    route_a_peak: int = 0,
    route_b_peak: int = 0,
    route_a_none: bool = False,
    route_b_none: bool = False,
) -> MagicMock:
    """MultiCaptionSystem モックを返す。

    route_a_none=True の場合は route_a_system を None にする。
    """
    ks = MagicMock()
    ks.route_a_system = None if route_a_none else _make_mock_route_system(route_a_peak)
    ks.route_b_system = None if route_b_none else _make_mock_route_system(route_b_peak)
    return ks


# ---------------------------------------------------------------------------
# テスト: /api/status レスポンスのフィールド存在
# ---------------------------------------------------------------------------

class TestStatusPeakFields:
    """/api/status レスポンスに route_a_peak_level 等のフィールドが含まれること。"""

    def test_status_includes_route_a_peak_level(self):
        """/api/status レスポンスに route_a_peak_level キーが含まれること。

        現状コードにはこのフィールドが存在しないため RED (FAIL) を期待する。
        実装後に GREEN になる。
        """
        client = _make_test_client()
        ks = _make_mock_konnyaku_system(route_a_peak=100)
        with patch("app.dpg", _mock_dpg()), \
             patch.object(app, "_konnyaku_system", ks), \
             patch.object(app, "_system", None):
            response = client.get("/api/status")
        data = json.loads(response.data)
        assert "route_a_peak_level" in data, (
            "/api/status レスポンスに route_a_peak_level が含まれること。"
            f"実際のキー: {list(data.keys())}"
        )

    def test_status_includes_route_b_peak_level(self):
        """/api/status レスポンスに route_b_peak_level キーが含まれること。

        現状コードにはこのフィールドが存在しないため RED (FAIL) を期待する。
        実装後に GREEN になる。
        """
        client = _make_test_client()
        ks = _make_mock_konnyaku_system(route_b_peak=200)
        with patch("app.dpg", _mock_dpg()), \
             patch.object(app, "_konnyaku_system", ks), \
             patch.object(app, "_system", None):
            response = client.get("/api/status")
        data = json.loads(response.data)
        assert "route_b_peak_level" in data, (
            "/api/status レスポンスに route_b_peak_level が含まれること。"
            f"実際のキー: {list(data.keys())}"
        )


# ---------------------------------------------------------------------------
# テスト: _konnyaku_system / route_X_system が None のとき 0 を返す
# ---------------------------------------------------------------------------

class TestStatusPeakWhenNone:
    """_konnyaku_system または route_X_system が None のとき peak = 0 を返すこと。"""

    def test_route_a_peak_zero_when_system_none(self):
        """_konnyaku_system=None のとき route_a_peak_level = 0 を返すこと。

        500 エラー回避のための None ガード動作確認。
        """
        client = _make_test_client()
        with patch("app.dpg", _mock_dpg()), \
             patch.object(app, "_konnyaku_system", None), \
             patch.object(app, "_system", None):
            response = client.get("/api/status")
        assert response.status_code == 200, (
            f"_konnyaku_system=None のとき 500 エラーにならないこと。"
            f"status_code={response.status_code}"
        )
        data = json.loads(response.data)
        assert data.get("route_a_peak_level") == 0, (
            "_konnyaku_system=None のとき route_a_peak_level=0 であること。"
            f"実際の値: {data.get('route_a_peak_level')}"
        )
        assert data.get("route_a_peak_pct") == 0, (
            "_konnyaku_system=None のとき route_a_peak_pct=0 であること。"
            f"実際の値: {data.get('route_a_peak_pct')}"
        )

    def test_route_a_peak_zero_when_route_a_none(self):
        """route_a_system=None のとき route_a_peak_level = 0 を返すこと。

        _konnyaku_system は存在するが route_a_system が None のケース。
        """
        client = _make_test_client()
        ks = _make_mock_konnyaku_system(route_a_none=True)
        with patch("app.dpg", _mock_dpg()), \
             patch.object(app, "_konnyaku_system", ks), \
             patch.object(app, "_system", None):
            response = client.get("/api/status")
        assert response.status_code == 200, (
            f"route_a_system=None のとき 500 エラーにならないこと。"
            f"status_code={response.status_code}"
        )
        data = json.loads(response.data)
        assert data.get("route_a_peak_level") == 0, (
            "route_a_system=None のとき route_a_peak_level=0 であること。"
            f"実際の値: {data.get('route_a_peak_level')}"
        )


# ---------------------------------------------------------------------------
# テスト: audio_peak の値が正しく反映されること
# ---------------------------------------------------------------------------

class TestStatusPeakValue:
    """audio_peak の値が /api/status レスポンスに正しく反映されること。"""

    def test_route_a_peak_returns_audio_peak(self):
        """route_a_system.audio_peak=12345 のとき route_a_peak_level=12345 を返すこと。

        また route_a_peak_pct が 12345 * 100 // 32767 = 37 であることも確認する。
        """
        client = _make_test_client()
        ks = _make_mock_konnyaku_system(route_a_peak=12345, route_b_peak=0)
        with patch("app.dpg", _mock_dpg()), \
             patch.object(app, "_konnyaku_system", ks), \
             patch.object(app, "_system", None):
            response = client.get("/api/status")
        data = json.loads(response.data)
        assert data.get("route_a_peak_level") == 12345, (
            "route_a_system.audio_peak=12345 のとき route_a_peak_level=12345 であること。"
            f"実際の値: {data.get('route_a_peak_level')}"
        )
        expected_pct = 12345 * 100 // 32767
        assert data.get("route_a_peak_pct") == expected_pct, (
            f"route_a_peak_pct={expected_pct} であること（12345 * 100 // 32767）。"
            f"実際の値: {data.get('route_a_peak_pct')}"
        )

    def test_route_b_peak_returns_audio_peak(self):
        """route_b_system.audio_peak=20000 のとき route_b_peak_level=20000 を返すこと。

        また route_b_peak_pct が 20000 * 100 // 32767 = 61 であることも確認する。
        （受入条件外の追加テスト: route_b の値確認）
        """
        client = _make_test_client()
        ks = _make_mock_konnyaku_system(route_a_peak=0, route_b_peak=20000)
        with patch("app.dpg", _mock_dpg()), \
             patch.object(app, "_konnyaku_system", ks), \
             patch.object(app, "_system", None):
            response = client.get("/api/status")
        data = json.loads(response.data)
        assert data.get("route_b_peak_level") == 20000, (
            "route_b_system.audio_peak=20000 のとき route_b_peak_level=20000 であること。"
            f"実際の値: {data.get('route_b_peak_level')}"
        )
        expected_pct = 20000 * 100 // 32767
        assert data.get("route_b_peak_pct") == expected_pct, (
            f"route_b_peak_pct={expected_pct} であること（20000 * 100 // 32767）。"
            f"実際の値: {data.get('route_b_peak_pct')}"
        )


# ---------------------------------------------------------------------------
# テスト: 既存フィールドが後方互換で残っていること
# ---------------------------------------------------------------------------

class TestStatusBackwardCompat:
    """既存フィールドが変更後も残っていること（後方互換チェック）。"""

    def test_existing_fields_preserved(self):
        """既存フィールド audio_peak, audio_peak_pct, audio_chunks_per_sec が維持されること。"""
        client = _make_test_client()
        ks = _make_mock_konnyaku_system()
        with patch("app.dpg", _mock_dpg()), \
             patch.object(app, "_konnyaku_system", ks), \
             patch.object(app, "_system", None):
            response = client.get("/api/status")
        data = json.loads(response.data)
        for field in ("audio_peak", "audio_peak_pct", "audio_chunks_per_sec",
                      "konnyaku_running", "route_b_state", "route_b_audio_gate"):
            assert field in data, (
                f"後方互換フィールド '{field}' が維持されること。"
                f"実際のキー: {list(data.keys())}"
            )
