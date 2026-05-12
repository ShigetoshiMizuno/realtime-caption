"""
API キーの軽量難読化ユーティリティ。

b64: プレフィックスが付いている場合は Base64 デコードして平文を返す。
プレフィックスがない場合はそのまま返す（後方互換）。

注意: Base64 は難読化であり暗号化ではない。
      config.yaml に平文キーを直接書いた場合と同等のセキュリティ強度。
      バージョン管理 / 画面共有でのうっかり漏洩を防ぐことを目的とする。
"""

import base64
import logging

B64_PREFIX = "b64:"
logger = logging.getLogger(__name__)


def decode_api_key(value) -> str:
    """
    設定値から API キー平文を返す。

    - value が None / 空文字 / 非 str → "" を返す
    - "b64:<base64>" 形式 → Base64 デコードして返す
    - Base64 デコード失敗 → WARNING ログを出しプレフィックス以降の文字列をそのまま返す
    - それ以外 → そのまま返す（後方互換: 旧 config.yaml 対応）
    """
    if not value or not isinstance(value, str):
        return ""
    if value.startswith(B64_PREFIX):
        suffix = value[len(B64_PREFIX):]
        if not suffix:
            return ""
        try:
            return base64.b64decode(suffix.encode()).decode("utf-8")
        except Exception as e:
            logger.warning("API key decode failed (returning raw suffix): %s", e)
            return suffix
    return value


def encode_api_key(plaintext) -> str:
    """
    API キー平文を "b64:<base64>" 形式にエンコードして返す。

    - plaintext が None / 空文字 / 非 str → "" を返す
    """
    if not plaintext or not isinstance(plaintext, str):
        return ""
    encoded = base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
    return B64_PREFIX + encoded
