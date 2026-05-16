"""dpg コールバックの引数互換性をコントラクトテストで保証する。

dpg は callback を起動時 default_value 設定等で **引数 0 個** で呼ぶことがある。
このテストは全 `_on_*` コールバックが引数 0〜3 個のどの呼び出しでも
「引数不一致 TypeError」を出さないことを保証する。

PR #143 で発覚したバグの再発防止。

結合点:
  app._verbose_callback (デコレータ) × _on_* 関数群
配線チェック:
  1. @_verbose_callback() で装飾された _on_* がモジュールレベルに存在すること
  2. wrapper が 0〜3 引数を受け付けること (args は None で補完・過剰は切り落とし)
  3. dpg 標準シグネチャ (sender, app_data, user_data) に準拠していること

TypeError の分類:
  - 「引数不一致 TypeError」: "takes N positional arguments but M were given" /
    "missing N required positional arguments" → 契約違反（FAIL）
  - 「引数の型 TypeError」: float(None), int(None) 等 → 許容（関数内ロジックの問題）
"""

import inspect
import re
from unittest.mock import MagicMock, patch

import pytest
import app


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _get_dpg_callbacks() -> list:
    """app モジュールから dpg callback 関数群を抽出する。

    _on_* 命名規則の関数で、_verbose_callback デコレータ適用済みのものを対象とする。
    """
    callbacks = []
    for name in dir(app):
        if name.startswith("_on_") and callable(getattr(app, name)):
            callbacks.append(getattr(app, name))
    return callbacks


def _make_dpg_mock() -> MagicMock:
    """dpg の全メソッドを安全なモックに置き換えたオブジェクトを返す。

    does_item_exist は False を返す（デフォルト）ので、
    コールバック内の `if dpg.does_item_exist(tag):` ガードが全てスキップされる。
    """
    mock = MagicMock()
    mock.does_item_exist.return_value = False
    mock.get_value.return_value = None
    mock.get_item_label.return_value = ""
    mock.get_item_configuration.return_value = {"enabled": True}
    return mock


_ARG_MISMATCH_PATTERN = re.compile(
    r"takes \d+ positional argument"   # "takes N positional argument(s) but M were given"
    r"|missing \d+ required positional"  # "missing N required positional argument(s)"
)


def _is_arg_mismatch_typeerror(exc: TypeError) -> bool:
    """TypeError が「引数個数の不一致」によるものか判定する。

    float(None) / int(None) のような「型の不一致」は False を返す。
    """
    return bool(_ARG_MISMATCH_PATTERN.search(str(exc)))


# ---------------------------------------------------------------------------
# コントラクトテスト
# ---------------------------------------------------------------------------

class TestCallbackContract:
    """dpg コールバックの引数互換性契約をテストする。"""

    def test_at_least_one_callback_exists(self):
        """app モジュールに _on_* callback が 1 つ以上存在すること（メタ検証）"""
        callbacks = _get_dpg_callbacks()
        assert len(callbacks) >= 10, f"_on_* callback が少なすぎる: {len(callbacks)}"

    @pytest.mark.parametrize("nargs", [0, 1, 2, 3])
    def test_callbacks_accept_any_arg_count(self, nargs):
        """各 callback が 0〜3 引数のどれで呼ばれても「引数不一致 TypeError」を出さないこと。

        dpg はモックするため、コールバック内の dpg.does_item_exist 等が安全に動く。
        float(None) / int(None) 等の「型 TypeError」は許容する（関数内ロジック問題）。
        「引数個数不一致 TypeError」のみが契約違反。

        PR #143 の修正 (_verbose_callback wrapper の引数補完) が機能していることを確認。
        PR #143 を revert すると nargs=0 で必ず FAIL する。
        """
        callbacks = _get_dpg_callbacks()
        args = (None,) * nargs

        failures = []
        dpg_mock = _make_dpg_mock()
        with patch("app.dpg", dpg_mock):
            for cb in callbacks:
                try:
                    cb(*args)
                except TypeError as e:
                    if _is_arg_mismatch_typeerror(e):
                        # 引数個数不一致は契約違反
                        failures.append((cb.__name__, str(e)))
                    # 型 TypeError (float(None) 等) は許容
                except Exception:
                    # その他例外（ロジック側）は許容
                    pass

        assert not failures, (
            f"以下の callback が引数 {nargs} 個で「引数不一致 TypeError」:\n"
            + "\n".join(f"  {name}: {err}" for name, err in failures)
        )

    def test_callbacks_signature_matches_dpg_convention(self):
        """全 callback が `(sender, app_data, user_data)` または互換シグネチャか検証。

        dpg 標準は 3 引数。2 引数・1 引数のオプション引数付きも許容。
        wrapper の inspect.signature は wrapper 自体の *args シグネチャを返すため、
        内部 func のシグネチャを確認する。
        """
        callbacks = _get_dpg_callbacks()
        non_conforming = []
        for cb in callbacks:
            # functools.wraps で wrap されている場合 __wrapped__ が存在する
            func = getattr(cb, "__wrapped__", cb)
            try:
                sig = inspect.signature(func)
                params = list(sig.parameters.values())
                positional_params = [
                    p for p in params
                    if p.kind in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.VAR_POSITIONAL,
                    )
                ]
                # VAR_POSITIONAL (*args) は多数引数を受け付けるので OK
                has_var_positional = any(
                    p.kind == inspect.Parameter.VAR_POSITIONAL
                    for p in positional_params
                )
                if has_var_positional:
                    continue  # *args は無制限に受け付けるので合格

                # 必須 positional 引数の数
                required_count = sum(
                    1 for p in positional_params
                    if p.kind != inspect.Parameter.VAR_POSITIONAL
                    and p.default is inspect.Parameter.empty
                )
                # 全 positional 引数の数 (デフォルト値付き含む)
                total_positional = sum(
                    1 for p in positional_params
                    if p.kind != inspect.Parameter.VAR_POSITIONAL
                )
                # dpg は 0〜3 引数で callback を呼ぶので、
                # 必須引数 > 3 は過多、total_positional > 5 は過剰設計として警告
                if required_count > 3:
                    non_conforming.append((
                        cb.__name__,
                        f"必須引数 {required_count} 個 (dpg 最大 3 個を超過)",
                    ))
                elif total_positional > 5:
                    non_conforming.append((
                        cb.__name__,
                        f"引数 {total_positional} 個 過多",
                    ))
            except (ValueError, TypeError):
                pass  # 検査不能なら無視

        assert not non_conforming, (
            f"dpg 標準シグネチャと非互換:\n"
            + "\n".join(f"  {name}: {reason}" for name, reason in non_conforming)
        )
