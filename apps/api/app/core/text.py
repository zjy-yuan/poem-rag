from __future__ import annotations

import hashlib
import re
import unicodedata

_WHITESPACE_OR_PUNCTUATION = re.compile(r"[\W_]+", re.UNICODE)


def normalize_lookup(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def normalize_content(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _WHITESPACE_OR_PUNCTUATION.sub("", normalized)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
