#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finds author names that are probably spellings of one person and should share
one entry ("J. Doe" next to "Jane Doe").

The corpus matches authors by cleaned name string, so every spelling used by a
venue listing, OpenAlex, DBLP or a PDF becomes its own author. This module
decides which of those split entries to fold together. It is deliberately
conservative: two names are merged only when

  1. the surnames match after normalisation (case, diacritics, hyphens and
     dots ignored),
  2. the given names are compatible (equal, or an initial against a name
     starting with that letter, middle names may be dropped), and
  3. there is independent evidence it is one person: the same OpenAlex author
     id, the same ORCID, at least two shared co-authors, or the same
     institution in overlapping years.

For very common surnames (Wang, Li, Kim, ...) only an OpenAlex id or an ORCID
counts. Names flagged as shared by several people are never touched, and a
group is dropped if its members are not all compatible with each other (for
example an initial that fits two different given names).

Pure functions only; scripts/merge_author_name_variants.py feeds it the corpus.
"""
import re
import unicodedata
from collections import defaultdict

MIN_SHARED_COAUTHORS = 2


def _strip_marks(text):
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c))


def name_tokens(name):
    """Lowercase ASCII tokens of a name; hyphens, dots and spaces all split."""
    text = _strip_marks(name).lower().replace("ß", "ss")
    return [t for t in re.split(r"[^a-z]+", text) if t]


def name_key(name):
    """Looser than equality: only the normalised tokens. Two spellings that
    differ in diacritics, hyphen vs space or case share a key."""
    return " ".join(name_tokens(name))


def _tok_compatible(a, b):
    if a == b:
        return True
    if len(a) == 1 or len(b) == 1:
        return a[0] == b[0]
    return False


def names_compatible(a, b):
    """Same surname (last token) and compatible given names. A shorter given
    name list may leave out middle names, but the first one must fit."""
    ta, tb = name_tokens(a), name_tokens(b)
    if len(ta) < 2 or len(tb) < 2 or ta[-1] != tb[-1]:
        return False
    ga, gb = ta[:-1], tb[:-1]
    return all(_tok_compatible(x, y) for x, y in zip(ga, gb))


def is_initial_name(name):
    """True when the first given name is only an initial."""
    toks = name_tokens(name)
    return len(toks) >= 2 and len(toks[0]) == 1


def _fullness(name):
    toks = name_tokens(name)
    n_full = sum(1 for t in toks[:-1] if len(t) > 1)
    non_ascii = sum(1 for c in name if ord(c) > 127)
    return n_full, len(toks), non_ascii


_EMAILISH_RE = re.compile(r"[a-z]\.[a-z]")


def looks_junky(name):
    """A name with an e-mail local part glued on ("Jane Doe jane.doe")
    or a repeated word: never merged and never chosen as the display name."""
    toks = name_tokens(name)
    return bool(_EMAILISH_RE.search(name)) or len(set(toks)) < len(toks)


def drops_spelled_out_name(a, b):
    """True when one name has a spelled-out given name the other lacks
    entirely ("Jane Doe" vs "Jane Q Doe"). That may be one person, or
    two, or an Asian given name split in two; an initial standing in for the
    extra name ("Luis M. Bergasa") is fine."""
    ga, gb = name_tokens(a)[:-1], name_tokens(b)[:-1]
    if len(ga) < len(gb):
        ga, gb = gb, ga
    return any(len(t) > 1 for t in ga[len(gb):])


def pick_display_name(members, papers):
    """The fullest spelling: most spelled-out given names, then diacritics
    kept, then the most papers, then alphabetical (so the pick is stable)."""
    def rank(n):
        full, ntok, na = _fullness(n)
        return (-full, -ntok, -na, n == n.lower(), -papers.get(n, 0), n)
    return sorted(members, key=rank)[0]


def coauthor_key(name):
    """Surname + first initial, so a co-author spelled two ways still counts once."""
    toks = name_tokens(name)
    if len(toks) < 2:
        return None
    return toks[-1] + " " + toks[0][0]


def _years_overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


STRONG_SHARED_COAUTHORS = 4


def shared_evidence(sa, sb, common_surname, extra_name=False):
    """Why two compatible names look like one person, or None.

    Each side is a dict with "ids" (OpenAlex ids), "orcids", "coauthors"
    (coauthor_key set) and "inst_years" ({institution: (first, last)}).
    extra_name: one spelling has a spelled-out given name the other lacks,
    which asks for more: an ORCID or STRONG_SHARED_COAUTHORS co-authors."""
    if extra_name:
        orc = sa["orcids"] & sb["orcids"]
        if orc:
            return "same ORCID " + sorted(orc)[0]
        co = sa["coauthors"] & sb["coauthors"]
        if not common_surname and len(co) >= STRONG_SHARED_COAUTHORS:
            return "%d shared co-authors" % len(co)
        return None
    ids = sa["ids"] & sb["ids"]
    if ids:
        return "same OpenAlex id " + sorted(ids)[0]
    orc = sa["orcids"] & sb["orcids"]
    if orc:
        return "same ORCID " + sorted(orc)[0]
    if common_surname:
        return None
    co = sa["coauthors"] & sb["coauthors"]
    if len(co) >= MIN_SHARED_COAUTHORS:
        return "%d shared co-authors" % len(co)
    for inst, ya in sa["inst_years"].items():
        yb = sb["inst_years"].get(inst)
        if yb and _years_overlap(ya, yb):
            return "same institution (%s) in overlapping years" % inst
    return None


class _Union:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def find_merges(people, excluded, common_surnames=frozenset()):
    """people: {name: {"papers", "ids", "orcids", "coauthors", "inst_years"}}.
    excluded: names that must never be merged (shared or ambiguous names).

    Returns (merges, near_misses, groups). merges maps each variant to the display
    name of its group. near_misses is a list of
    {"a", "b", "reason"} for compatible pairs left alone."""
    buckets = defaultdict(list)
    for name in people:
        toks = name_tokens(name)
        if len(toks) >= 2 and name not in excluded and "(" not in name and not looks_junky(name):
            buckets[(toks[-1], toks[0][0])].append(name)

    edges, near = [], []
    for names in buckets.values():
        names = sorted(names)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if not names_compatible(a, b):
                    continue
                if b in people[a].get("together", ()) or a in people[b].get("together", ()):
                    near.append({"a": a, "b": b, "reason": "listed together on one paper, so two people"})
                    continue
                surname = name_tokens(a)[-1]
                common = surname in common_surnames or any(
                    t in common_surnames for t in name_tokens(a)[:1])
                extra = drops_spelled_out_name(a, b)
                why = shared_evidence(people[a], people[b], common, extra)
                if why:
                    edges.append((a, b, why))
                else:
                    near.append({"a": a, "b": b, "reason": (
                        "one name has an extra given name, needs an ORCID or 4 shared co-authors" if extra
                        else "common surname, needs an OpenAlex id or ORCID" if common
                        else "no shared id, ORCID, two co-authors or institution+years")})

    uf = _Union()
    for a, b, _ in edges:
        uf.union(a, b)
    groups = defaultdict(set)
    for a, b, _ in edges:
        groups[uf.find(a)].update((a, b))

    merges, why_by_pair = {}, {(a, b): w for a, b, w in edges}
    out = []
    for members in groups.values():
        members = sorted(members)
        bad = [(x, y) for i, x in enumerate(members) for y in members[i + 1:]
               if not names_compatible(x, y)]
        if bad:
            for a, b, _ in edges:
                if a in members:
                    near.append({"a": a, "b": b, "reason": (
                        "group is not consistent (%s and %s do not fit each other)" % bad[0])})
            continue
        shown = pick_display_name(members, {n: people[n]["papers"] for n in members})
        reasons = sorted({w for (a, b), w in why_by_pair.items() if a in members})
        for m in members:
            if m != shown:
                merges[m] = shown
        out.append({"name": shown, "variants": [m for m in members if m != shown],
                    "evidence": reasons})
    return merges, near, out
