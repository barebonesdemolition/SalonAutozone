"""
Forgiving search for parts and cars.

- Every word has to match somewhere (name, category, make, model…), in any order,
  so "toyota brake pads" finds "Brake pads (front) - Toyota Corolla".
- Common spellings and synonyms are understood: "break pad", "tyres", "benz", "vw".
- Plurals match singulars ("plugs" finds "plug").
- If nothing matches, words are corrected against the words actually used in
  listings ("altenator" -> "alternator") and the search is tried again.
"""
from __future__ import annotations

import difflib
import re

from sqlalchemy import and_, or_

# word -> the words to look for instead (any of them may match)
SYNONYMS = {
    "break": ["brake"], "breaks": ["brake"], "brakes": ["brake"],
    "breakpad": ["brake pad"], "breakpads": ["brake pad"], "brakepad": ["brake pad"], "brakepads": ["brake pad"],
    "tyre": ["tyre", "tire"], "tyres": ["tyre", "tire"], "tire": ["tire", "tyre"], "tires": ["tire", "tyre"],
    "rim": ["rim", "wheel"], "rims": ["rim", "wheel"],
    "shocks": ["shock"], "absorber": ["absorber", "shock"], "absorbers": ["absorber", "shock"], "struts": ["strut", "shock"],
    "headlamp": ["headlamp", "headlight", "head light"], "headlamps": ["headlamp", "headlight", "head light"],
    "headlights": ["headlight", "head light", "headlamp"], "bulbs": ["bulb"],
    "battery": ["battery", "batt"], "batteries": ["battery", "batt"],
    "oil": ["oil", "lubricant"], "coolant": ["coolant", "antifreeze"], "antifreeze": ["antifreeze", "coolant"],
    "gearbox": ["gearbox", "transmission"], "transmission": ["transmission", "gearbox"],
    "silencer": ["silencer", "exhaust", "muffler"], "muffler": ["muffler", "exhaust", "silencer"],
    "windscreen": ["windscreen", "windshield"], "windshield": ["windshield", "windscreen"],
    "bonnet": ["bonnet", "hood"], "hood": ["hood", "bonnet"],
    "benz": ["mercedes"], "merc": ["mercedes"], "mercedes": ["mercedes"], "vw": ["volkswagen"],
    "landcruiser": ["land cruiser", "landcruiser"], "rav": ["rav"], "chevy": ["chevrolet"],
    "okada": ["motorbike", "motorcycle", "okada"],
}

STOPWORDS = {"for", "a", "an", "the", "and", "of", "my", "to", "in", "with", "car", "parts", "part", "new", "used"}

# Words people commonly mistype that the listings might not contain yet.
KNOWN_WORDS = [
    "brake", "pad", "disc", "rotor", "caliper", "alternator", "starter", "radiator", "battery", "filter",
    "spark", "plug", "clutch", "gearbox", "transmission", "suspension", "shock", "absorber", "bearing",
    "headlight", "taillight", "bumper", "mirror", "wiper", "exhaust", "silencer", "injector", "pump",
    "belt", "timing", "engine", "gasket", "thermostat", "compressor", "condenser", "steering", "rack",
    "tyre", "wheel", "axle", "differential", "sensor", "coil", "fuel", "oil", "coolant",
    "toyota", "nissan", "honda", "hyundai", "mitsubishi", "mazda", "suzuki", "subaru", "lexus", "mercedes",
    "volkswagen", "peugeot", "ford", "hilux", "corolla", "camry", "prado", "rav4", "pajero", "tucson",
]


def words(q: str | None) -> list[str]:
    """Lower-case words from a search box, without punctuation or filler words."""
    q = (q or "").lower().replace("-", " ")
    q = re.sub(r"[^\w\s]", " ", q)
    return [w for w in q.split() if w and w not in STOPWORDS][:8]


def alternatives(word: str) -> list[str]:
    """Spellings to try for one word, including its singular."""
    alts = list(SYNONYMS.get(word, [word]))
    for a in list(alts):
        if len(a) > 3 and a.endswith("s") and not a.endswith("ss"):
            alts.append(a[:-1])
    seen, out = set(), []
    for a in alts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def condition(q: str | None, columns):
    """SQL condition: every word matches at least one column (with its alternatives)."""
    per_word = []
    for w in words(q):
        per_word.append(or_(*[col.ilike(f"%{alt}%") for alt in alternatives(w) for col in columns]))
    return and_(*per_word) if per_word else None


def correct(q: str | None, vocabulary: set[str]) -> str | None:
    """A corrected query if some words look like typos of known words, else None."""
    vocab = sorted(set(KNOWN_WORDS) | {v for v in vocabulary if len(v) >= 3})
    fixed, changed = [], False
    for w in words(q):
        if w in vocab or w in SYNONYMS or len(w) < 4:
            fixed.append(w)
            continue
        match = difflib.get_close_matches(w, vocab, n=1, cutoff=0.78)
        if match and match[0] != w:
            fixed.append(match[0])
            changed = True
        else:
            fixed.append(w)
    return " ".join(fixed) if changed else None


def vocabulary_from(texts) -> set[str]:
    out = set()
    for t in texts:
        out.update(words(t))
    return out
