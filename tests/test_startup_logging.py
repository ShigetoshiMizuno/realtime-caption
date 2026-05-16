"""
tests/test_startup_logging.py

issue #101: 起動進捗ログ可視化
_startup_step コンテキストマネージャーの単体テスト + app.py 結合確認
"""
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# プロジェクトルートを参照
_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# _startup_step のインポート確認
# ---------------------------------------------------------------------------

def _import_startup_step():
    """app.py から _startup_step をインポートして返す。未定義なら AttributeError。"""
    # app.py は dearpygui を import するため、テスト環境では直接 import できない場合がある。
    # importlib で安全に取得する。
    import importlib.util
    spec = importlib.util.spec_from_file_location("app_module", _ROOT / "app.py")
    # dearpygui が利用不可の環境ではスキップ
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (ImportError, ModuleNotFoundError) as e:
        pytest.skip(f"app.py をインポートできないためスキップ: {e}")
    return getattr(mod, "_startup_step")


# ---------------------------------------------------------------------------
# _startup_step 単体テスト
# ---------------------------------------------------------------------------

class TestStartupStepNormal:
    """正常完了パスのテスト"""

    def test_prints_start_message(self, capsys):
        """コンテキスト入時に '[STARTUP] <label> 開始 ...' が出力される"""
        _startup_step = _import_startup_step()
        with _startup_step("テストステップ"):
            pass
        captured = capsys.readouterr()
        assert "[STARTUP] テストステップ 開始 ..." in captured.out

    def test_prints_complete_message(self, capsys):
        """コンテキスト正常終了時に '[STARTUP] <label> 完了 (Xs)' が出力される"""
        _startup_step = _import_startup_step()
        with _startup_step("テストステップ"):
            pass
        captured = capsys.readouterr()
        assert "[STARTUP] テストステップ 完了 (" in captured.out
        assert "s)" in captured.out

    def test_complete_message_format(self, capsys):
        """完了メッセージが '[STARTUP] <label> 完了 (X.XXs)' の形式"""
        _startup_step = _import_startup_step()
        with _startup_step("設定ファイル読み込み"):
            pass
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        complete_lines = [l for l in lines if "完了" in l]
        assert len(complete_lines) == 1
        # 'X.XXs)' の形式であること
        import re
        assert re.search(r"\(\d+\.\d{2}s\)", complete_lines[0]), \
            f"完了メッセージに時間が含まれていない: {complete_lines[0]}"


class TestStartupStepError:
    """例外発生パスのテスト"""

    def test_reraises_exception(self):
        """例外が発生した場合に re-raise される"""
        _startup_step = _import_startup_step()
        with pytest.raises(ValueError, match="テストエラー"):
            with _startup_step("失敗ステップ"):
                raise ValueError("テストエラー")

    def test_prints_error_message(self, capsys):
        """例外発生時に '[STARTUP][ERROR] <label> 失敗 (Xs): <error>' が出力される"""
        _startup_step = _import_startup_step()
        with pytest.raises(RuntimeError):
            with _startup_step("失敗ステップ"):
                raise RuntimeError("something went wrong")
        captured = capsys.readouterr()
        assert "[STARTUP][ERROR] 失敗ステップ 失敗 (" in captured.out
        assert "something went wrong" in captured.out

    def test_error_message_format(self, capsys):
        """エラーメッセージが '[STARTUP][ERROR] <label> 失敗 (X.XXs): <error>' の形式"""
        _startup_step = _import_startup_step()
        with pytest.raises(Exception):
            with _startup_step("PyAudio 初期化"):
                raise Exception("init failed")
        captured = capsys.readouterr()
        import re
        assert re.search(r"\[STARTUP\]\[ERROR\] PyAudio 初期化 失敗 \(\d+\.\d{2}s\): init failed",
                         captured.out), f"エラーメッセージ形式が不正: {captured.out}"


class TestStartupStepTiming:
    """経過時間計測のテスト（time.monotonic を mock）"""

    def test_elapsed_time_in_complete_message(self, capsys):
        """mock した monotonic の差分が完了メッセージに反映される"""
        _startup_step = _import_startup_step()
        import app as app_module
        call_seq = [100.0, 100.42]  # start=100.0, end=100.42 → 0.42s
        with patch.object(app_module.time, "monotonic", side_effect=call_seq):
            with _startup_step("計測テスト"):
                pass
        captured = capsys.readouterr()
        assert "0.42s" in captured.out, f"0.42s が出力に含まれていない: {captured.out}"

    def test_elapsed_time_in_error_message(self, capsys):
        """例外時の mock した時間差がエラーメッセージに反映される"""
        _startup_step = _import_startup_step()
        import app as app_module
        call_seq = [200.0, 200.73]  # 0.73s
        with patch.object(app_module.time, "monotonic", side_effect=call_seq):
            with pytest.raises(Exception):
                with _startup_step("計測エラーテスト"):
                    raise Exception("timed error")
        captured = capsys.readouterr()
        assert "0.73s" in captured.out, f"0.73s がエラー出力に含まれていない: {captured.out}"


# ---------------------------------------------------------------------------
# app.py 結合確認テスト
# ---------------------------------------------------------------------------

class TestStartupStepWiring:
    """app.py に _startup_step が最低 4 箇所で使われていることを確認する"""

    def test_wiring_count_at_least_4(self):
        """app.py 内の _startup_step 呼び出し箇所が 4 以上ある"""
        app_py = _ROOT / "app.py"
        content = app_py.read_text(encoding="utf-8")
        # 定義行を除いた呼び出し箇所をカウント
        import re
        # 'with _startup_step(' の行をカウント（定義は contextmanager decorator の後の def 行なので除外）
        usage_lines = re.findall(r"with\s+_startup_step\s*\(", content)
        count = len(usage_lines)
        assert count >= 4, (
            f"_startup_step の使用箇所が {count} 件（期待: 4 件以上）。"
            f"app.py の main() 内の各起動ステップに _startup_step を追加してください。"
        )

    def test_startup_log_not_printed_outside_startup(self):
        """_startup_step の定義が app.py に存在する（import 可能）"""
        app_py = _ROOT / "app.py"
        content = app_py.read_text(encoding="utf-8")
        assert "def _startup_step" in content, \
            "app.py に _startup_step 関数が定義されていない"

    def test_startup_total_time_logged(self):
        """全体起動時間のログ出力コードが app.py に含まれる"""
        app_py = _ROOT / "app.py"
        content = app_py.read_text(encoding="utf-8")
        assert "[STARTUP] 全体起動時間:" in content, \
            "app.py に '[STARTUP] 全体起動時間:' の出力がない"

    def test_startup_step_count_in_main(self):
        """main() および _build_gui() で _startup_step が呼ばれる回数を確認

        追加計測対象（依頼仕様）:
          main() レベル: RPC サーバー起動 / PTT マネージャー初期化 / dpg.show_viewport()
          _build_gui() 内部: dpg.create_context / フォントロード / テーマ作成 /
                             ウィジェット追加 / dpg.create_viewport / dpg.setup_dearpygui
        合計 10 箇所以上の _startup_step が仕込まれていること。
        """
        import re
        app_py = _ROOT / "app.py"
        content = app_py.read_text(encoding="utf-8")
        usage_lines = re.findall(r"with\s+_startup_step\s*\(", content)
        count = len(usage_lines)
        assert count >= 10, (
            f"_startup_step の使用箇所が {count} 件（期待: 10 件以上）。"
            "main() / _build_gui() への計測ステップ細分化が不足しています。"
        )
