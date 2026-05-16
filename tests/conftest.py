"""
tests/conftest.py

プロジェクトルートを sys.path に追加して、テストからルートモジュールを
インポートできるようにする。
"""
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def reset_restart_locks(monkeypatch):
    """各テスト開始時に app._restart_locks を fresh Lock dict にリセットする。

    flaky 防止: 他のテストが lock を release し忘れた場合でも
    後続テストへの影響を防ぐ。monkeypatch によりテスト終了時に自動復元される。
    """
    try:
        import app as _app
        monkeypatch.setattr(
            _app,
            "_restart_locks",
            {"a": threading.Lock(), "b": threading.Lock()},
            raising=False,
        )
    except (ImportError, AttributeError):
        # app モジュールが読み込めない or _restart_locks が存在しない場合は skip
        pass
