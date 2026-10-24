"""Arabic normalization and cross-script name candidates. Pure: no I/O."""

from __future__ import annotations

import re

NORMALIZE_SQL = r"""CREATE OR REPLACE FUNCTION normalize_arabic_text(input text) RETURNS text AS $$
BEGIN
    IF input IS NULL THEN RETURN NULL; END IF;
    RETURN regexp_replace(
        translate(
            input,
            E'أإآٱىة٠١٢٣٤٥٦٧٨٩ًٌٍَُِّْـ',
            E'اااايه0123456789'
        ),
        E'[\r\n]+', ' ', 'g'
    );
END;
$$ LANGUAGE plpgsql IMMUTABLE STRICT"""

_FROM = "أإآٱىة٠١٢٣٤٥٦٧٨٩ًٌٍَُِّْـ"
_TO = "اااايه0123456789"
_NORMALIZE = {
    ord(char): _TO[i] if i < len(_TO) else None for i, char in enumerate(_FROM)
}
_ARABIC = str.maketrans(
    {
        "ا": "",
        "و": "",
        "ي": "",
        "ء": "",
        "ؤ": "",
        "ئ": "",
        "ع": "",
        "ب": "b",
        "ت": "t",
        "ث": "t",
        "ج": "j",
        "ح": "h",
        "خ": "k",
        "د": "d",
        "ذ": "d",
        "ر": "r",
        "ز": "z",
        "س": "s",
        "ش": "s",
        "ص": "s",
        "ض": "d",
        "ط": "t",
        "ظ": "z",
        "غ": "g",
        "ف": "f",
        "ق": "k",
        "ك": "k",
        "ل": "l",
        "م": "m",
        "ن": "n",
        "ه": "h",
    }
)
_DIGRAPHS = {"kh": "k", "sh": "s", "th": "t", "dh": "d", "gh": "g"}
_VOWELS = str.maketrans("", "", "aeiouywʿʾʻʼ‘’'")


def normalize(text: str | None) -> str | None:
    """Match normalize_arabic_text, including NULL and newline handling."""
    if text is None:
        return None
    return re.sub(r"[\r\n]+", " ", text.translate(_NORMALIZE))


def _skeleton(token: str) -> str:
    token = re.sub(r"kh|sh|th|dh|gh", lambda match: _DIGRAPHS[match[0]], token)
    token = token.translate(_ARABIC).translate(_VOWELS).replace("q", "k")
    # A final h is taa marbuta (فاطمة, Fatima) as often as a real h; drop it in both scripts.
    return re.sub(r"(.)\1+", r"\1", token).removesuffix("h")


def name_keys(name: str) -> tuple[str, ...]:
    """Return lossy consonant keys, joining devotional abd compounds."""
    text = normalize(name).lower()
    text = re.sub(r"\babd[oue]l", "abd al ", text)
    tokens = re.findall(r"[a-zء-غف-يʿʾʻʼ‘’']+", text)
    keys = []
    compound = False
    for token in tokens:
        if token in {"al", "el", "ال"}:
            continue
        if token.startswith("ال"):
            token = token[2:]
        key = _skeleton(token)
        if not key:
            continue
        if compound:
            keys[-1] = re.sub(r"(.)\1+", r"\1", keys[-1] + key)
        else:
            keys.append(key)
        compound = token in {"abd", "عبد"}
    return tuple(keys)


def candidates(query: str, name: str) -> bool:
    """Propose ordered name matches; callers must corroborate before merging."""
    shorter, longer = sorted((name_keys(query), name_keys(name)), key=len)
    if not shorter:
        return False
    remaining = iter(longer)
    return all(any(key == other for other in remaining) for key in shorter)
