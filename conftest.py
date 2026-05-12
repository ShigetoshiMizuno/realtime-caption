"""
conftest.py — pytest 設定ファイル

プロジェクトルートを sys.path に追加し、realtime_translator 等の
トップレベルモジュールをテストから import できるようにする。
"""
import sys
from pathlib import Path

# プロジェクトルートを sys.path の先頭に追加
sys.path.insert(0, str(Path(__file__).parent))
