"""Fuzzy payer-name similarity built from several complementary techniques.

* Token level (soft matching): exact, Jaro-Winkler (typos), normalised Levenshtein, prefix ("TECH" ->
  "Technologies") and abbreviation-subsequence ("PKG" -> "Packaging"). Bank narrations truncate and abbreviate
  names, so a whole-string metric alone is not enough.
* Coverage / precision: how much of the customer's distinctive name is explained by the narration and how much of
  the narration is explained by the name ("R K SHARMA CLINIC" only explains "Clinics" -> rejected).
* Character tri-gram TF-IDF cosine and token-set ratio as whole-string tie-breakers.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler, Levenshtein

LEGAL = {"PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "INC", "GMBH", "LLC", "CO", "CORP", "CORPORATION", "THE", "AND", "OF", "PLC", "SA"}


def tokens(text: str, drop_legal: bool = True) -> list[str]:
    toks = re.findall(r"[A-Z0-9]+", (text or "").upper())
    return [t for t in toks if not (drop_legal and t in LEGAL)]


def token_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    best = 0.0
    jw = JaroWinkler.similarity(a, b)
    if jw >= 0.9:
        best = max(best, min(jw, 0.97))
    lv = Levenshtein.normalized_similarity(a, b)
    if lv >= 0.8:
        best = max(best, lv)
    if len(short) >= 3 and long_.startswith(short):
        best = max(best, 0.9)
    if len(short) >= 2 and short != long_ and short[0] == long_[0]:
        it = iter(long_)
        if all(ch in it for ch in short):  # subsequence: PKG in PACKAGING, TECH in TECHNOLOGIES
            best = max(best, 0.8)
    return best


class TfidfIndex:
    """Character tri-gram TF-IDF over the customer-name corpus."""

    def __init__(self, corpus: list[str]):
        self.n = max(len(corpus), 1)
        df: Counter = Counter()
        for doc in corpus:
            df.update(set(self._grams(doc)))
        self.idf = {g: math.log((1 + self.n) / (1 + c)) + 1 for g, c in df.items()}
        self.default_idf = math.log(1 + self.n) + 1

    @staticmethod
    def _grams(text: str) -> list[str]:
        s = f"  {' '.join(tokens(text))} "
        return [s[i : i + 3] for i in range(len(s) - 2)]

    def vec(self, text: str) -> dict[str, float]:
        tf = Counter(self._grams(text))
        return {g: c * self.idf.get(g, self.default_idf) for g, c in tf.items()}

    def cosine(self, a: str, b: str) -> float:
        va, vb = self.vec(a), self.vec(b)
        dot = sum(w * vb.get(g, 0.0) for g, w in va.items())
        na = math.sqrt(sum(w * w for w in va.values()))
        nb = math.sqrt(sum(w * w for w in vb.values()))
        return dot / (na * nb) if na and nb else 0.0


def payer_similarity(payer_text: str, customer_name: str, index: TfidfIndex | None = None) -> tuple[float, dict]:
    narr = [t for t in tokens(payer_text) if len(t) >= 2]
    cust = tokens(customer_name)
    if not narr or not cust:
        return 0.0, {"reason": "no payer text"}
    weights = [2.0 if i == 0 else 1.0 for i in range(len(cust))]  # first word is the brand
    used: set[int] = set()
    matched_weight = 0.0
    first_matched = False
    for ci, ct in enumerate(cust):
        best, best_j = 0.0, -1
        for j, nt in enumerate(narr):
            if j in used:
                continue
            s = token_sim(nt, ct)
            if s > best:
                best, best_j = s, j
        if best >= 0.75:
            used.add(best_j)
            matched_weight += weights[ci] * best
            if ci == 0:
                first_matched = True
    coverage = matched_weight / sum(weights)
    explained = sum(1 for nt in narr if any(token_sim(nt, ct) >= 0.8 for ct in cust)) / len(narr)
    tfidf = index.cosine(payer_text, customer_name) if index else 0.0
    tset = fuzz.token_set_ratio(" ".join(narr), " ".join(cust)) / 100
    score = 0.55 * coverage + 0.15 * explained + 0.15 * tfidf + 0.15 * tset
    if not first_matched:
        score *= 0.6  # brand word not found: generic words ("Clinics", "Retail") must not carry a match
    return min(score, 1.0), {"coverage": round(coverage, 2), "explained": round(explained, 2), "tfidf": round(tfidf, 2), "token_set": round(tset, 2)}
