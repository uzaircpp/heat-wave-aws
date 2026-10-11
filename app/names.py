"""Finding Delhi ward names in what people type or say: in English, Hinglish or Hindi (Devanagari),
with spelling slips ("zakir nager", "जाकिर नगर"). Ward names in the data are in English, so Hindi text is
transliterated to Latin letters first and both sides are compared in a loose phonetic form."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

from .data import Ward, city

_V = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o",
      "औ": "au", "ऑ": "o"}
_M = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o"}
_C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
      "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
      "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
      "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f"}
_NUKTA = {"क": "q", "ज": "z", "फ": "f", "ड": "r", "ढ": "rh", "ख": "kh", "ग": "g"}
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def transliterate(text: str) -> str:
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _C:
            cons = _C[ch]
            if i + 1 < n and text[i + 1] == "़":
                cons = _NUKTA.get(ch, cons)
                i += 1
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt in _M:
                out.append(cons + _M[nxt])
                i += 2
                continue
            if nxt == "्":
                out.append(cons)
                i += 2
                continue
            # inherent vowel, dropped at the end of a word (Hindi schwa deletion)
            end = not nxt or not DEVANAGARI.match(nxt) or nxt in "।"
            out.append(cons if end else cons + "a")
        elif ch in _V:
            out.append(_V[ch])
        elif ch in "ंँ":
            out.append("n")
        elif ch == "ः":
            out.append("h")
        elif ch == "।":
            out.append(" ")
        elif DEVANAGARI.match(ch):
            pass
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def phonetic(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z ]", " ", s)
    for a, b in (("ph", "f"), ("z", "j"), ("w", "v"), ("q", "k"), ("x", "ks"), ("ee", "i"), ("oo", "u"),
                 ("aa", "a"), ("ou", "u"), ("y", "i"), ("sh", "s"), ("kh", "k"), ("gh", "g"), ("th", "t"),
                 ("dh", "d"), ("bh", "b"), ("chh", "ch"), ("ai", "e"), ("ri", "r")):
        s = s.replace(a, b)
    s = re.sub(r"(.)\1+", r"\1", s)            # double letters
    s = re.sub(r"(?<=[a-z]{3})a\b", "", s)      # final schwa: "nagara" ~ "nagar"
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _index() -> list[tuple[str, int, Ward, float]]:
    out = []
    for w in city().wards:
        base = re.sub(r"\b(phase|ward|central|north|south|east|west|i|ii|iii)\b|-", " ", w.name.lower())
        full, short = phonetic(w.name), phonetic(base)
        for form, weight in ((full, 1.0), (short, 0.99)):
            if len(form) >= 4 and (weight == 1.0 or form != full):
                out.append((form, len(form.split()), w, weight))
    return out


def find_ward(text: str, cutoff: float = 0.86) -> Ward | None:
    """The ward named in the text, allowing Hindi script and small spelling differences."""
    latin = transliterate(text) if DEVANAGARI.search(text) else text
    words = phonetic(latin).split()
    best, best_score, best_len = None, cutoff, 0
    for form, nw, w, weight in _index():
        for size in {nw, nw + 1} - {0}:
            for i in range(0, max(1, len(words) - size + 1)):
                chunk = " ".join(words[i:i + size])
                if abs(len(chunk) - len(form)) > 4:
                    continue
                score = weight * (1.0 if chunk == form else SequenceMatcher(None, chunk, form).ratio())
                if score > best_score or (score == best_score and len(form) > best_len):   # "Rohini East" over "Rohini"
                    best, best_score, best_len = w, score, len(form)
    return best


def find_wards(text: str, cutoff: float = 0.9) -> list[Ward]:
    """Every ward named in the text, in the order they appear (for 'between A and B')."""
    latin = transliterate(text) if DEVANAGARI.search(text) else text
    words = phonetic(latin).split()
    hits: list[tuple[int, int, Ward]] = []
    for form, nw, w, weight in _index():
        for i in range(0, max(1, len(words) - nw + 1)):
            chunk = " ".join(words[i:i + nw])
            if abs(len(chunk) - len(form)) <= 3 and weight * SequenceMatcher(None, chunk, form).ratio() >= cutoff:
                hits.append((i, -len(form), w))
    hits.sort(key=lambda h: (h[0], h[1]))
    out, used_until = [], -1
    for i, _, w in hits:
        if i > used_until and w not in out:
            out.append(w)
            used_until = i + len(w.name.split()) - 1
    return out
