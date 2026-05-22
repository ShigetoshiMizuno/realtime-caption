"""UI 制御エンドポイントの E2E テスト（--test-mode で動作確認）。"""
import pytest


pytestmark = pytest.mark.e2e


class TestGetEndpoints:
    def test_status_ok(self, client):
        r = client.get_status()
        assert r.get("konnyaku_running") is True

    def test_tags_returns_dict(self, client):
        tags = client.tags()
        assert "TAG_PTT_GUI_BTN" in tags
        assert "TAG_PTT_LATCH_CHECK" in tags

    def test_exists_known_tag(self, client):
        # PTT GUI ボタンが GUI に存在するはず（test-mode でも GUI は起動）
        assert client.exists("ptt_gui_btn") is True

    def test_get_value_latch_check_initial_false(self, client):
        r = client.get_value("ptt_latch_check")
        assert r.get("value") is False


class TestPressRelease:
    def test_ptt_btn_press_returns_ok(self, client):
        r = client.press("ptt_gui_btn")
        assert r.get("ok") is True

    def test_ptt_btn_release_returns_ok(self, client):
        r = client.release("ptt_gui_btn")
        assert r.get("ok") is True

    def test_press_unknown_tag_returns_404(self, client):
        import requests
        r = requests.post(
            f"{client.base}/api/ui/press",
            json={"tag": "tag_does_not_exist"},
            timeout=5,
        )
        assert r.status_code == 404


class TestSetValue:
    def test_set_latch_check_true(self, client):
        r = client.set_value("ptt_latch_check", True)
        assert r.get("ok") is True
        val = client.get_value("ptt_latch_check").get("value")
        assert val is True

    def test_set_latch_check_false(self, client):
        client.set_value("ptt_latch_check", True)
        r = client.set_value("ptt_latch_check", False)
        assert r.get("ok") is True
        val = client.get_value("ptt_latch_check").get("value")
        assert val is False
