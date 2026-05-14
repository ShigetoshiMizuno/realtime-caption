# 言語テーブル: (コード, 表示名) のリスト。1行追加で言語追加可能。
# 将来 zh/ko/es を追加するときはここにタプルを1行追記するだけ。
# OpenAI Realtime Translate の BCP-47 コードに準拠すること。
SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("ja", "日本語"),
    ("en", "English"),
    # ("zh", "中文"),      # 将来: 中国語
    # ("ko", "한국어"),    # 将来: 韓国語
    # ("es", "Español"),   # 将来: スペイン語
]


def get_language_display_name(code: str) -> str:
    """言語コードから表示名を取得する。未定義コードは code をそのまま返す。"""
    return next((name for c, name in SUPPORTED_LANGUAGES if c == code), code)


def get_language_codes() -> list[str]:
    """GUI コンボ等に使うコードリストを返す。"""
    return [code for code, _ in SUPPORTED_LANGUAGES]


def get_language_display_names() -> list[str]:
    """GUI コンボに表示する名前リストを返す。"""
    return [name for _, name in SUPPORTED_LANGUAGES]
