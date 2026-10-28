"""Advanced Persian/English anti-profanity + shared text normalization.

Multi-stage normalization and layered matching. Not perfect by design:
obfuscation space is infinite; goal is high catch-rate on common evasions
with controlled false positives for ordinary chat.
"""
from __future__ import annotations

import re
import unicodedata

# ── Base vocabularies ───────────────────────────────────────────────────
# Phrase-oriented entries (multi-token) checked after collapse.
_BASE_FA_WORDS = [
    # core
    'کیر', 'کص', 'کس', 'کون', 'کونت', 'کیرم', 'کیرت',
    'جنده', 'جنده‌', 'مادرجنده', 'خواهرجنده', 'ننهجنده',
    'حرامزاده', 'حرومزاده', 'حرام‌زاده', 'حروم‌زاده',
    'دیوث', 'دیووث', 'لاشی', 'اشغال', 'گوه', 'گه',
    'بی‌شرف', 'بیشرف', 'بیغیرت', 'بی‌غیرت',
    'گایید', 'گاییدن', 'میگام', 'بگا', 'بگام',
    'کونده', 'کصکش', 'کسخان', 'کیری',
    # family insults
    'ننتو', 'ننه‌تو', 'ننه', 'مادرتو', 'باباتو', 'بابات',
    'خارتو', 'خاهرتو', 'خواهرتو', 'خاهر',
    'ناموستو', 'ناموص', 'ناموس',
    # common compounds (normalized forms)
    'کسکش', 'کصکش', 'مادرقهوه', 'ننهقهوه',
]

_BASE_EN = [
    'fuck', 'fucker', 'fucking', 'shit', 'bitch', 'asshole', 'dick',
    'pussy', 'bastard', 'motherfucker', 'cunt', 'whore', 'slut',
]

# Tokens that alone are too common → only match inside insult patterns
_CONTEXT_ONLY = {
    'مادر', 'بابا', 'بابات', 'خواهر', 'خاهر', 'خار', 'ننه', 'گه', 'کس',
}

# Insult patterns after full collapse (no spaces): family + base roots
_COMPOUND_FA = [
    r'ننت', r'ننه‌?ت', r'مادرت', r'بابات', r'خاهرت', r'خواهرت', r'خارت',
    r'ناموست', r'ناموص',
    r'کیر', r'کص', r'کون', r'جنده', r'دیوث', r'گایید', r'میگام',
    r'کصکش', r'کسکش', r'کسخان', r'حروم?زاده', r'حرام‌?زاده',
]

_COMPOUND_RE = re.compile('|'.join(_COMPOUND_FA))

# Zero-width / invisible / format chars to strip
_INVISIBLE_RE = re.compile(
    r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad\u180e\u061c]'
)
# Combining marks (diacritics)
_DIACRITICS_RE = re.compile(r'[\u064b-\u065f\u0670\u06d6-\u06ed]')
# Decorative separators often used between letters
_SEP_CHARS = r'''[\s\.\*_~\-–—ـ·•،,;:!?؟«»"'`|\\\/\[\]\(\)\{\}<>@#\$%\^&+=۰-۹0-9\u060c\u061b]+'''

_AR_FA = str.maketrans({
    'ي': 'ی', 'ى': 'ی', 'ئ': 'ی',
    'ك': 'ک',
    'ة': 'ه', 'ۀ': 'ه',
    'ؤ': 'و',
    'إ': 'ا', 'أ': 'ا', 'آ': 'ا', 'ٱ': 'ا',
    'ة': 'ه',
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
})

_LEET = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't',
    '@': 'a', '$': 's',
})


def normalize_text(text: str) -> str:
    """Stage-1 normalize: unicode, arabic/persian, invisible, diacritics, case."""
    if not text:
        return ''
    t = unicodedata.normalize('NFKC', text)
    t = t.lower()
    t = t.translate(_AR_FA)
    t = t.translate(_LEET)
    t = _INVISIBLE_RE.sub('', t)
    t = _DIACRITICS_RE.sub('', t)
    t = t.replace('‌', '').replace('ـ', '')  # ZWNJ, tatweel
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def collapse_for_match(text: str) -> str:
    """Stage-2: remove separators/spaces so ک.ی.ر → کیر and ک س → کس."""
    t = normalize_text(text)
    # Drop common separators between letters
    t = re.sub(_SEP_CHARS, '', t)
    # Fully collapse letter runs for matching (کییییر → کیر)
    t = re.sub(r'(.)\1{2,}', r'\1\1', t)  # max 2 repeats
    return t


def soft_collapse(text: str) -> str:
    """Keep single spaces but strip punctuation separators."""
    t = normalize_text(text)
    t = re.sub(r'''[\.\*_~\-–—ـ·•،,;:!?؟«»"'`|\\\/\[\]\(\)\{\}<>@#\$%\^&+=]+''', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'(.)\1{2,}', r'\1\1', t)  # max 2 repeats
    return t


def _word_list_pattern(words: list[str]) -> re.Pattern | None:
    cleaned = []
    for w in words:
        w = normalize_text(w)
        w = re.sub(r'\s+', '', w)
        if len(w) < 2:
            continue
        cleaned.append(re.escape(w))
    if not cleaned:
        return None
    body = '|'.join(sorted(set(cleaned), key=len, reverse=True))
    return re.compile(body)


_BASE_COLLAPSED = _word_list_pattern(_BASE_FA_WORDS + _BASE_EN)


def contains_profanity(text: str, extra_words: list[str] | None = None) -> bool:
    """Return True if text likely contains insult after multi-stage analysis."""
    if not text or not str(text).strip():
        return False

    soft = soft_collapse(text)
    hard = collapse_for_match(text)
    hard1 = re.sub(r'(.)\1+', r'\1', hard)  # fully de-duplicated letters

    # 1) Direct collapsed dictionary match (hard and fully-deduped)
    for form in (hard, hard1):
        if not form or not _BASE_COLLAPSED:
            continue
        hit = _BASE_COLLAPSED.search(form)
        if not hit:
            continue
        token = hit.group(0)
        if token in _CONTEXT_ONLY:
            if _COMPOUND_RE.search(form):
                return True
        else:
            return True

    # 2) Soft (spaced) whole-word style for multi-word extras
    if extra_words:
        pat = _word_list_pattern(extra_words)
        if pat and (pat.search(hard) or pat.search(hard1)):
            return True

    # 3) Compound family insults on collapsed text
    # e.g. ن.ن.ت.و.گ.ا.ی.ی.د → ننتوگایید
    for form in (hard, hard1):
        if re.search(
            r'(ننت|نت|مادرت|بابات|خاهرت|خواهرت|خارت|ناموست).{0,8}(گای|گاید|کیر|کص|جنده|کس)',
            form,
        ):
            return True
        if re.search(r'(گای|گاید|کیر|کص|جنده).{0,8}(ننت|نت|مادرت|بابات)', form):
            return True

    # 4) Spaced letter form already handled by collapse; also check soft tokens
    soft_nospace = soft.replace(' ', '')
    if soft_nospace != hard and _BASE_COLLAPSED and _BASE_COLLAPSED.search(soft_nospace):
        hit = _BASE_COLLAPSED.search(soft_nospace)
        if hit and hit.group(0) not in _CONTEXT_ONLY:
            return True

    return False


def extract_urls(text: str) -> list[str]:
    """Extract likely URLs/domains from free text (for link lock)."""
    if not text:
        return []
    found: list[str] = []
    t = unicodedata.normalize('NFKC', text)
    # Strip zero-width between protocol pieces: h t t p : / /
    collapsed = _INVISIBLE_RE.sub('', t)
    patterns = [
        r'https?://[^\s<>\"\']+',
        r'www\.[a-zA-Z0-9][\w.-]*\.[a-zA-Z]{2,}[^\s<>\"\']*',
        r'(?:^|[\s(])((?:ble\.ir|t\.me|telegram\.me|instagram\.com|youtube\.com|youtu\.be|x\.com|twitter\.com)/[^\s<>\"\']+)',
        r'(?:^|[\s(])([a-zA-Z0-9][\w-]*\.(?:com|ir|net|org|info|me|io|co|app|dev|site|link|xyz|online|shop)(?:/[^\s]*)?)',
    ]
    for p in patterns:
        for m in re.finditer(p, collapsed, re.I):
            g = m.group(0) if m.lastindex is None else (m.group(1) or m.group(0))
            g = g.strip().strip('()[].,;:«»"\'')
            if g and g not in found:
                found.append(g)
    return found


def text_similarity(a: str, b: str) -> float:
    """Cheap similarity 0..1 for anti-repeat (normalized)."""
    if not a or not b:
        return 0.0
    x, y = collapse_for_match(a), collapse_for_match(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    # char bigram Jaccard
    def bigrams(s: str) -> set[str]:
        if len(s) < 2:
            return {s}
        return {s[i:i + 2] for i in range(len(s) - 1)}
    ba, bb = bigrams(x[:400]), bigrams(y[:400])
    if not ba or not bb:
        return 0.0
    inter = len(ba & bb)
    union = len(ba | bb)
    return inter / union if union else 0.0
