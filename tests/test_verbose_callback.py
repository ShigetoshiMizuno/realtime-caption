"""
tests/test_verbose_callback.py

PR2: GUI callback 詳細記録 — _verbose_callback デコレータのテスト

テスト対象:
  1. _verbose_state=False でオーバーヘッドなし（記録されない）
  2. _verbose_state=True で開始/終了が記録される
  3. 例外発生時に error ログが出る + 例外が再 raise される
  4. 所要時間が記録される
  5. デコレータ適用後も関数が正常動作（既存の dpg callback 動作不変）
  6. sender / app_data が記録される
  7. name=None のとき関数名から自動取得される
  8. _RPCHandler.do_GET / do_POST で RPC end が verbose に記録される
"""

import time
import tempfile
from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# ヘルパー: app モジュールの verbose 状態を一時的に変更するコンテキスト
# ---------------------------------------------------------------------------

class _VerboseContext:
    """テスト中だけ app._verbose_state と _app_verbose_path を差し替える。"""

    def __init__(self, state: bool, log_path: Path):
        self.state = state
        self.log_path = log_path
        self._orig_state = None
        self._orig_path = None

    def __enter__(self):
        import app
        self._orig_state = app._verbose_state
        self._orig_path = getattr(app, "_app_verbose_path", None)
        app._verbose_state = self.state
        app._app_verbose_path = self.log_path
        return self

    def __exit__(self, *args):
        import app
        app._verbose_state = self._orig_state
        app._app_verbose_path = self._orig_path


# ---------------------------------------------------------------------------
# 1. _verbose_state=False でオーバーヘッドなし（記録されない）
# ---------------------------------------------------------------------------

class TestVerboseCallbackNoOp:
    """verbose=False のとき _verbose_callback は何も書かない。"""

    def test_no_write_when_verbose_false(self, tmp_path):
        """_verbose_state=False のとき、コールバックを呼んでもファイルに何も書かない。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("test_callback_noop")
        def my_cb(sender, app_data, user_data):
            return 42

        with _VerboseContext(False, log_file):
            result = my_cb("sender_id", "data_val", None)

        assert result == 42
        # ファイルが存在しないか、存在しても空
        if log_file.exists():
            assert log_file.read_text(encoding="utf-8") == ""

    def test_noop_means_original_func_called(self, tmp_path):
        """verbose=False でもオリジナルの関数は呼ばれる。"""
        import app

        calls = []

        @app._verbose_callback("noop_original_call")
        def my_cb(sender, app_data, user_data):
            calls.append((sender, app_data))
            return "returned"

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(False, log_file):
            result = my_cb("s", "d", None)

        assert calls == [("s", "d")]
        assert result == "returned"


# ---------------------------------------------------------------------------
# 2. _verbose_state=True で開始/終了が記録される
# ---------------------------------------------------------------------------

class TestVerboseCallbackStartEnd:
    """verbose=True のとき start / end ログが記録される。"""

    def test_start_logged(self, tmp_path):
        """verbose=True のとき [GUI_CALLBACK] start=<name> が記録される。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("my_start_test")
        def my_cb(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_cb("s1", "a1", None)

        content = log_file.read_text(encoding="utf-8")
        assert "GUI_CALLBACK" in content, f"GUI_CALLBACK がない: {content}"
        assert "start=my_start_test" in content, f"start= がない: {content}"

    def test_end_logged(self, tmp_path):
        """verbose=True のとき [GUI_CALLBACK] end=<name> が記録される。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("my_end_test")
        def my_cb(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_cb("s2", "a2", None)

        content = log_file.read_text(encoding="utf-8")
        assert "end=my_end_test" in content, f"end= がない: {content}"

    def test_start_before_end(self, tmp_path):
        """start ログが end ログより前に書かれる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("order_test")
        def my_cb(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        start_pos = content.find("start=order_test")
        end_pos = content.find("end=order_test")
        assert start_pos >= 0, "start が見つからない"
        assert end_pos >= 0, "end が見つからない"
        assert start_pos < end_pos, "start が end より後になっている"


# ---------------------------------------------------------------------------
# 3. 例外発生時に error ログが出る + 例外が再 raise される
# ---------------------------------------------------------------------------

class TestVerboseCallbackException:
    """例外発生時に error ログを出して例外を再 raise する。"""

    def test_error_logged_on_exception(self, tmp_path):
        """例外時に [GUI_CALLBACK] error=<name> が記録される。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("exception_test")
        def bad_cb(sender, app_data, user_data):
            raise ValueError("test error")

        with _VerboseContext(True, log_file):
            with pytest.raises(ValueError):
                bad_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "error=exception_test" in content, f"error= がない: {content}"

    def test_exception_reraise(self, tmp_path):
        """例外は再 raise される（握りつぶされない）。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("reraise_test")
        def bad_cb(sender, app_data, user_data):
            raise RuntimeError("must propagate")

        with _VerboseContext(True, log_file):
            with pytest.raises(RuntimeError, match="must propagate"):
                bad_cb("s", "a", None)

    def test_error_log_contains_error_type(self, tmp_path):
        """error ログに error_type=<ExceptionClass> が含まれる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("error_type_test")
        def bad_cb(sender, app_data, user_data):
            raise TypeError("type mismatch")

        with _VerboseContext(True, log_file):
            with pytest.raises(TypeError):
                bad_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "error_type=TypeError" in content, f"error_type= がない: {content}"

    def test_error_log_contains_error_msg(self, tmp_path):
        """error ログに error_msg=<repr> が含まれる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("error_msg_test")
        def bad_cb(sender, app_data, user_data):
            raise KeyError("missing_key")

        with _VerboseContext(True, log_file):
            with pytest.raises(KeyError):
                bad_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "error_msg=" in content, f"error_msg= がない: {content}"

    def test_end_not_logged_on_exception(self, tmp_path):
        """例外時は end= ではなく error= が記録される（end= は記録されない）。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("no_end_on_error")
        def bad_cb(sender, app_data, user_data):
            raise ValueError("oops")

        with _VerboseContext(True, log_file):
            with pytest.raises(ValueError):
                bad_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "end=no_end_on_error" not in content, f"error 時に end= が記録されている: {content}"


# ---------------------------------------------------------------------------
# 4. 所要時間が記録される
# ---------------------------------------------------------------------------

class TestVerboseCallbackDuration:
    """duration_ms フィールドが記録される。"""

    def test_duration_ms_in_end_log(self, tmp_path):
        """end ログに duration_ms=<n.nn> が含まれる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("duration_test")
        def slow_cb(sender, app_data, user_data):
            time.sleep(0.01)
            return None

        with _VerboseContext(True, log_file):
            slow_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "duration_ms=" in content, f"duration_ms= がない: {content}"

    def test_duration_ms_is_numeric(self, tmp_path):
        """duration_ms の値が数値として解釈できる。"""
        import app
        import re

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("duration_numeric")
        def my_cb(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        match = re.search(r"duration_ms=([0-9]+\.[0-9]+)", content)
        assert match is not None, f"duration_ms=<float> がない: {content}"
        duration = float(match.group(1))
        assert duration >= 0.0

    def test_duration_ms_in_error_log(self, tmp_path):
        """例外時の error ログにも duration_ms= が含まれる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("duration_error")
        def bad_cb(sender, app_data, user_data):
            raise ValueError("err")

        with _VerboseContext(True, log_file):
            with pytest.raises(ValueError):
                bad_cb("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "duration_ms=" in content, f"error 時の duration_ms= がない: {content}"


# ---------------------------------------------------------------------------
# 5. デコレータ適用後も関数が正常動作（既存の dpg callback 動作不変）
# ---------------------------------------------------------------------------

class TestVerboseCallbackPassthrough:
    """デコレータは passthrough — 既存の振る舞いを壊さない。"""

    def test_return_value_preserved(self, tmp_path):
        """デコレータを付けても戻り値が変わらない。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("passthrough_return")
        def my_cb(sender, app_data, user_data):
            return {"key": "value", "num": 42}

        with _VerboseContext(True, log_file):
            result = my_cb("s", "a", None)

        assert result == {"key": "value", "num": 42}

    def test_funcname_preserved(self, tmp_path):
        """functools.wraps により __name__ が保持される。"""
        import app

        @app._verbose_callback("funcname_test")
        def my_named_callback(sender, app_data, user_data):
            return None

        assert my_named_callback.__name__ == "my_named_callback"

    def test_extra_args_passed_through(self, tmp_path):
        """任意の引数（*args, **kwargs）がそのまま渡される。"""
        import app

        log_file = tmp_path / "verbose.txt"
        received = []

        @app._verbose_callback("args_passthrough")
        def my_cb(sender, app_data, user_data):
            received.append((sender, app_data, user_data))

        with _VerboseContext(True, log_file):
            my_cb("sender_val", "app_data_val", "user_data_val")

        assert received == [("sender_val", "app_data_val", "user_data_val")]

    def test_multiple_calls_work(self, tmp_path):
        """同じデコレータ付き関数を複数回呼んでも正常動作する。"""
        import app

        log_file = tmp_path / "verbose.txt"
        count = [0]

        @app._verbose_callback("multi_call")
        def my_cb(sender, app_data, user_data):
            count[0] += 1

        with _VerboseContext(True, log_file):
            my_cb("s", "a", None)
            my_cb("s", "b", None)
            my_cb("s", "c", None)

        assert count[0] == 3
        content = log_file.read_text(encoding="utf-8")
        # 3 回分の start= が記録される
        assert content.count("start=multi_call") == 3


# ---------------------------------------------------------------------------
# 6. sender / app_data が記録される
# ---------------------------------------------------------------------------

class TestVerboseCallbackSenderAppData:
    """sender / app_data が start ログに記録される。"""

    def test_sender_in_start_log(self, tmp_path):
        """start ログに sender=<value> が含まれる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("sender_test")
        def my_cb(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_cb("my_sender_tag", "my_app_data", None)

        content = log_file.read_text(encoding="utf-8")
        assert "sender=" in content, f"sender= がない: {content}"
        assert "my_sender_tag" in content, f"sender 値がない: {content}"

    def test_app_data_in_start_log(self, tmp_path):
        """start ログに app_data=<value> が含まれる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("app_data_test")
        def my_cb(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_cb("s", True, None)

        content = log_file.read_text(encoding="utf-8")
        assert "app_data=" in content, f"app_data= がない: {content}"

    def test_no_args_does_not_crash(self, tmp_path):
        """引数なしで呼ばれても（sender=None, app_data=None として）クラッシュしない。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("no_args_test")
        def my_cb():
            return "ok"

        with _VerboseContext(True, log_file):
            result = my_cb()

        assert result == "ok"


# ---------------------------------------------------------------------------
# 7. name=None のとき関数名から自動取得される
# ---------------------------------------------------------------------------

class TestVerboseCallbackAutoName:
    """name=None のとき関数名が自動使用される。"""

    def test_auto_name_from_funcname(self, tmp_path):
        """name=None のとき、関数名が start= に使われる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        # name を省略（None）
        @app._verbose_callback()
        def my_auto_named_callback(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_auto_named_callback("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "start=my_auto_named_callback" in content, f"自動名がない: {content}"

    def test_explicit_name_overrides_funcname(self, tmp_path):
        """明示的な name が指定されたとき、関数名ではなく name が使われる。"""
        import app

        log_file = tmp_path / "verbose.txt"

        @app._verbose_callback("explicit_name_override")
        def my_cb_with_different_funcname(sender, app_data, user_data):
            return None

        with _VerboseContext(True, log_file):
            my_cb_with_different_funcname("s", "a", None)

        content = log_file.read_text(encoding="utf-8")
        assert "start=explicit_name_override" in content
        # 関数名ではない
        assert "start=my_cb_with_different_funcname" not in content


# ---------------------------------------------------------------------------
# 8. RPC エンドポイントで duration_ms が verbose に記録される
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="Flask 移行により _RPCHandler.do_GET/do_POST は廃止 (issue #158)")
class TestRPCVerboseDuration:
    """_RPCHandler.do_GET / do_POST で RPC end が verbose に記録される。"""

    def _make_rpc_request(self, method: str, path: str, tmp_path: Path):
        """実際の HTTPServer を立てず、do_GET / do_POST を直接呼ぶ。
        dpg が必要なパスを避け、_enqueue もモックする。
        """
        import app
        from unittest.mock import MagicMock, patch

        handler = object.__new__(app._RPCHandler)
        handler.path = path
        handler.wfile = MagicMock()
        handler.headers = MagicMock()
        handler.headers.get = MagicMock(return_value="0")
        handler.rfile = MagicMock()

        def fake_send_json(data, status=200):
            pass

        handler._send_json = fake_send_json

        log_file = tmp_path / "verbose.txt"
        with _VerboseContext(True, log_file):
            with patch.object(app, "_system", None):
                with patch.object(app, "_is_running", False):
                    with patch.object(app, "_log_entries", []):
                        with patch.object(app, "_devices", []):
                            # _enqueue をモック（do_POST /api/stop で呼ばれる）
                            with patch("app._enqueue", MagicMock()):
                                # dpg アクセスが発生するパスは does_item_exist=False でショートカット
                                with patch("app.dpg") as mock_dpg:
                                    mock_dpg.does_item_exist.return_value = False
                                    try:
                                        if method == "GET":
                                            handler.do_GET()
                                        else:
                                            handler.do_POST()
                                    except Exception:
                                        pass

        return log_file

    def test_do_get_rpc_end_logged(self, tmp_path):
        """do_GET 呼び出し後に RPC end が verbose ログに記録される。"""
        log_file = self._make_rpc_request("GET", "/api/log", tmp_path)

        assert log_file.exists(), "verbose ファイルが存在しない"
        content = log_file.read_text(encoding="utf-8")
        # PR3 以降: "end GET <path>" 形式。RPC カテゴリと end キーワードを確認する
        assert "RPC" in content and ("end=" in content or "end GET" in content or "end POST" in content), (
            f"RPC end が記録されていない: {content}"
        )

    def test_do_post_rpc_end_logged(self, tmp_path):
        """do_POST 呼び出し後に RPC end が verbose ログに記録される。"""
        log_file = self._make_rpc_request("POST", "/api/stop", tmp_path)

        assert log_file.exists(), "verbose ファイルが存在しない"
        content = log_file.read_text(encoding="utf-8")
        # PR3 以降: "end POST <path>" 形式。RPC カテゴリと end キーワードを確認する
        assert "RPC" in content and ("end=" in content or "end GET" in content or "end POST" in content), (
            f"RPC end が記録されていない: {content}"
        )

    def test_do_get_duration_ms_logged(self, tmp_path):
        """do_GET の verbose ログに duration_ms= が含まれる。"""
        log_file = self._make_rpc_request("GET", "/api/devices", tmp_path)

        assert log_file.exists(), "verbose ファイルが存在しない"
        content = log_file.read_text(encoding="utf-8")
        assert "duration_ms=" in content, f"duration_ms= がない: {content}"


# ---------------------------------------------------------------------------
# 9. dpg が引数なしで callback を呼ぶケース（起動時の default_value 初期化）
# ---------------------------------------------------------------------------

class TestVerboseCallbackArgPadding:
    """dpg が引数なしで呼ぶ場合、wrapper が None で必要引数を補完する。"""

    def test_no_args_calls_func_with_none_padding(self, tmp_path):
        """wrapper() 引数なしで呼ばれたとき func(None, None, None) が呼ばれる。"""
        import app

        log_file = tmp_path / "verbose.txt"
        received = []

        @app._verbose_callback("padding_no_args")
        def my_cb(sender, app_data, user_data):
            received.append((sender, app_data, user_data))

        with _VerboseContext(False, log_file):
            my_cb()  # 引数なし

        assert received == [(None, None, None)], f"期待: [(None, None, None)], 実際: {received}"

    def test_one_arg_pads_remaining(self, tmp_path):
        """wrapper(sender) のみのとき func(sender, None, None) が呼ばれる。"""
        import app

        log_file = tmp_path / "verbose.txt"
        received = []

        @app._verbose_callback("padding_one_arg")
        def my_cb(sender, app_data, user_data):
            received.append((sender, app_data, user_data))

        with _VerboseContext(False, log_file):
            my_cb("s1")  # 1 引数

        assert received == [("s1", None, None)], f"期待: [('s1', None, None)], 実際: {received}"

    def test_full_args_passed_unchanged(self, tmp_path):
        """wrapper(s, a, u) と全引数渡すと、そのまま func に渡される。"""
        import app

        log_file = tmp_path / "verbose.txt"
        received = []

        @app._verbose_callback("padding_full_args")
        def my_cb(sender, app_data, user_data):
            received.append((sender, app_data, user_data))

        with _VerboseContext(False, log_file):
            my_cb("s1", "a1", "u1")

        assert received == [("s1", "a1", "u1")], f"期待: [('s1', 'a1', 'u1')], 実際: {received}"

    def test_extra_args_passed_through(self, tmp_path):
        """引数が必要数より多い場合、余分な引数もそのまま渡される。"""
        import app

        log_file = tmp_path / "verbose.txt"
        received = []

        @app._verbose_callback("padding_extra_args")
        def my_cb(sender, app_data, user_data, extra=None):
            received.append((sender, app_data, user_data, extra))

        with _VerboseContext(False, log_file):
            my_cb("s1", "a1", "u1", "extra_val")

        assert received == [("s1", "a1", "u1", "extra_val")], f"期待通りでない: {received}"
