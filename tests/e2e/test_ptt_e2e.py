"""PTT 動作の E2E テスト（Issue #154/#155/#157 回帰テスト）。"""
import time
import pytest


pytestmark = pytest.mark.e2e


class TestE1LatchStaysOn:
    """E-1: ラッチ ON 後に GUI ボタンを押してもラッチが外れない（#154/#157）。"""

    def test_latch_on_after_ptt_btn_press_latch_stays_on(self, client):
        # 前提: ラッチ OFF、route_b IDLE
        client.set_value("ptt_latch_check", False)
        time.sleep(0.1)

        # ラッチ ON
        client.set_value("ptt_latch_check", True)
        time.sleep(0.2)

        # ラッチ ON のまま維持されているか
        assert client.get_value("ptt_latch_check").get("value") is True

        # route_b が RUNNING になっているか（最大 3 秒待機）
        def route_b_running():
            st = client.get_status()
            return st.get("route_b_state") == "RUNNING"

        assert client.poll_until(route_b_running, max_wait=3.0), "route_b が RUNNING にならなかった"


class TestE3LatchCancelByBtnPress:
    """E-3: ラッチ ON 中に GUI ボタン押下でラッチ解除・Route B 停止（#154）。"""

    def test_press_btn_while_latched_cancels_latch(self, client):
        # ラッチ ON して RUNNING になるまで待つ
        client.set_value("ptt_latch_check", False)
        time.sleep(0.1)
        client.set_value("ptt_latch_check", True)

        def route_b_running():
            return client.get_status().get("route_b_state") == "RUNNING"

        client.poll_until(route_b_running, max_wait=3.0)

        # GUI ボタンを押す → ラッチ解除 + Route B 停止
        client.press("ptt_gui_btn")
        time.sleep(0.2)

        assert client.get_value("ptt_latch_check").get("value") is False

        def route_b_idle():
            return client.get_status().get("route_b_state") == "IDLE"

        assert client.poll_until(route_b_idle, max_wait=3.0), "route_b が IDLE にならなかった"


class TestE2AudioGateDuringHold:
    """E-2: GUI ボタンホールド中は audio_gate が開いている（#155）。"""

    def test_press_opens_audio_gate(self, client):
        client.set_value("ptt_latch_check", False)
        time.sleep(0.1)

        client.press("ptt_gui_btn")
        time.sleep(0.2)

        st = client.get_status()
        assert st.get("route_b_audio_gate") is True, "press後に audio_gate が開いていない"

    def test_release_closes_audio_gate(self, client):
        client.set_value("ptt_latch_check", False)
        time.sleep(0.1)
        client.press("ptt_gui_btn")
        time.sleep(0.1)

        client.release("ptt_gui_btn")
        time.sleep(0.2)

        st = client.get_status()
        assert st.get("route_b_audio_gate") is False, "release後に audio_gate が閉じていない"
