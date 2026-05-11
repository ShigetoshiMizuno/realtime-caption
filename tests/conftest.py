"""
tests/conftest.py

プロジェクトルートを sys.path に追加して、テストからルートモジュールを
インポートできるようにする。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
