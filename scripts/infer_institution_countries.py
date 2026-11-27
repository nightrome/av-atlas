#!/usr/bin/env python3
"""Suggest countries for institutions that have none, from the countries OpenAlex
gave their own authors.

An institution gets a suggestion when at least MIN_VOTES authors list it as their
only affiliation and have exactly one country code, and one country holds at least
MIN_SHARE of those votes. Universities stay in one place, so this is reliable for
them. Companies work in several countries, so their suggestions are printed for a
person to check and are only written with --include-industry.

Usage:
  py -3 scripts/infer_institution_countries.py            # print the suggestions
  py -3 scripts/infer_institution_countries.py --write    # add them to
                                                          # data/institution_countries.json
Never changes an entry that is already in the file.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

import aggregate as A  # noqa: E402

COUNTRIES_FILE = BASE / "data" / "institution_countries.json"
MIN_VOTES = 3
MIN_SHARE = 0.8


def collect_votes(papers):
    votes = defaultdict(Counter)
    for p in papers:
        for a in p.get("authors_detail") or []:
            codes = a.get("countries") or []
            affs = []
            for raw in a.get("affiliations") or []:
                name = A.normalize_institution(raw)
                if A.is_valid_institution(name) and name not in affs:
                    affs.append(name)
            if len(affs) == 1 and len(codes) == 1:
                votes[affs[0]][codes[0]] += 1
    return votes


def suggestions(votes, known):
    out = {}
    for inst, counter in votes.items():
        if inst in known:
            continue
        total = sum(counter.values())
        code, top = counter.most_common(1)[0]
        if total >= MIN_VOTES and top / total >= MIN_SHARE:
            out[inst] = (code, total, round(top / total, 2))
    return out


def main():
    write = "--write" in sys.argv
    include_industry = "--include-industry" in sys.argv
    papers = json.loads((BASE / "data" / "papers_full.json").read_text(encoding="utf-8"))
    raw = json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))
    known = set(A.institution_country_map(raw))
    found = suggestions(collect_votes(papers), known)
    added = {}
    for inst, (code, total, share) in sorted(found.items(), key=lambda kv: -kv[1][1]):
        industry = A.classify_institution_sector(inst) != "academic"
        print(f"{'?' if industry else ' '} {inst} -> {code} ({total} authors, {share:.0%})")
        if not industry or include_industry:
            added[inst] = code
    if write:
        raw.update({k: v for k, v in added.items() if k not in raw})
        COUNTRIES_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                  encoding="utf-8", newline="\n")
        print(f"Added {len(added)} countries to {COUNTRIES_FILE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
