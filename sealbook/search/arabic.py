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


# A hamza on the alif marks the name's own first letter (إلياس Elias, آلاء Alaa): the
# article ال is written with a bare alif, so only a bare one can be the article.
_HAMZA_AL = re.compile(
    r"(?<![\u0621-\u064a])[أإآ][\u064b-\u0652]*ل(?=[\u064b-\u0652]*[\u0621-\u064a])"
)


# Surname first with a separator, as passports and registers print it: "KASSAR /
# ADRIAN", "Kassar, Adrian", "العلي، يوسف". Each side is one to three words.
_SURNAME_FIRST = re.compile(
    r"\s*([^\W\d_]+(?:[\s'’-]+[^\W\d_]+){0,2})\s*[/,،]\s*"
    r"([^\W\d_]+(?:[\s'’.-]+[^\W\d_]+){0,2})\.?\s*"
)


def natural(name: str) -> str:
    """A name written surname first with a slash or comma, in natural order
    ("ADRIAN KASSAR"); any other name as written. Without a separator the written
    order is the name's own (Ali Hasan is not Hasan Ali)."""
    found = _SURNAME_FIRST.fullmatch(name)
    return f"{found[2]} {found[1]}" if found else name


def _options(name: str) -> tuple[tuple[str, ...], ...]:
    """Per name in `name`, the keys it may stand for, the likelier reading first."""
    text = normalize(_HAMZA_AL.sub("ل", natural(name))).lower()
    text = re.sub(r"\babd[oue]l", "abd al ", text)
    tokens = re.findall(r"[a-zء-غف-يʿʾʻʼ‘’']+", text)
    out: list[tuple[str, ...]] = []
    compound = False
    for token in tokens:
        if token in {"al", "el", "ال"}:
            continue
        keys = [_skeleton(token)]
        if token.startswith("ال"):
            # A bare alif and lam is the article (العلي, Ali) or, with the hamza left
            # unwritten, the name's own letters (الياس, Elias). After abd it is the
            # article.
            keys = [_skeleton(token[2:])] + ([] if compound else keys)
        keys = [k for k in dict.fromkeys(keys) if k]
        if not keys:
            continue
        if compound:
            out[-1] = tuple(
                re.sub(r"(.)\1+", r"\1", p + k) for p in out[-1] for k in keys
            )
        else:
            out.append(tuple(keys))
        compound = token in {"abd", "عبد"}
    return tuple(out)


def name_keys(name: str) -> tuple[str, ...]:
    """Return lossy consonant keys, joining devotional abd compounds. A bare ال
    gives its article reading; `candidates` and `same_keys` also try the other."""
    return tuple(keys[0] for keys in _options(name))


def _same(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return not set(a).isdisjoint(b)


def same_keys(a: str, b: str) -> bool:
    """The same names in the same order, as consonant keys (خالد, Khaled)."""
    x, y = _options(a), _options(b)
    return bool(x) and len(x) == len(y) and all(map(_same, x, y))


def candidates(query: str, name: str) -> bool:
    """Propose ordered name matches; callers must corroborate before merging."""
    shorter, longer = sorted((_options(query), _options(name)), key=len)
    if not shorter:
        return False
    remaining = iter(longer)
    return all(any(_same(key, other) for other in remaining) for key in shorter)
