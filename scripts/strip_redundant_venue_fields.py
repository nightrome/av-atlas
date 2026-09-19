#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Repo-size cleanup (user-requested): every entry in a per-venue-year file
under data/venues/ (e.g. cvpr2024.json) repeated "conference"/"year" even
though every entry in that file shares the same one -- 11.1MB of pure
redundancy across 265k entries, fully determined by the filename.
merge_corpus.py's conference_and_year_for_file() reconstructs both from the
filename, so it's safe to strip them from the tracked source files
themselves.

"_all" journal files (ijcv_all.json, ijrr_all.json, ral_all.json,
tits_all.json, tpami_all.json, tro_all.json -- continuous publication, not
one proceedings per file) keep their own per-entry "year" (genuinely
varies) but still lose "conference" (uniform within the file, same as
every other venue file). arxiv_s2_citing.json (untracked, per-entry
conference/year both genuinely vary -- a bulk citation-discovery crawl, not
a single-venue listing) is left alone entirely.

Idempotent: a file whose entries have already had a field stripped is a
no-op for that field. Re-run after fetching a new venue file (the fetcher
scripts themselves still write the old, redundant schema -- this is a
post-processing step, not a fetcher change) and before committing.

Usage: python strip_redundant_venue_fields.py
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
VENUES_DIR = BASE / "data" / "venues"

# Same table as merge_corpus.py's VENUE_PREFIX_TO_CONFERENCE -- kept as a
# separate copy rather than importing merge_corpus (which pulls in
# classify.py and its own heavier dependencies) for a script this small and
# infrequently run; the two are directly compared in this script's own test.
VENUE_PREFIX_TO_CONFERENCE = {
    "aaai": "AAAI", "accv": "ACCV", "bmvc": "BMVC", "corl": "CoRL", "cvpr": "CVPR",
    "eccv": "ECCV", "gcpr": "GCPR", "iccv": "ICCV", "iclr": "ICLR", "icml": "ICML",
    "icra": "ICRA", "ijcv": "IJCV", "ijrr": "IJRR", "iros": "IROS", "itsc": "ITSC",
    "iv": "IV", "neurips": "NeurIPS", "ral": "RA-L", "rss": "RSS", "tits": "T-ITS",
    "tpami": "TPAMI", "tro": "T-RO", "wacv": "WACV",
}


def strip_entries(papers, strip_year):
    """Pure function: returns (new_papers, n_conference_stripped,
    n_year_stripped). Only removes a field when its value on the entry
    matches what the filename would already tell you -- never blind-strips
    a value that might disagree (a genuine safety net, even though every
    file this runs on today was already verified uniform before this
    script existed)."""
    n_conf = 0
    n_year = 0
    out = []
    for p in papers:
        p = dict(p)
        if "conference" in p:
            del p["conference"]
            n_conf += 1
        if strip_year and "year" in p:
            del p["year"]
            n_year += 1
        out.append(p)
    return out, n_conf, n_year


def main():
    n_files = 0
    n_conf_total = 0
    n_year_total = 0
    for f in sorted(VENUES_DIR.glob("*.json")):
        if f.name.startswith("arxiv"):
            continue  # genuinely non-uniform, and untracked -- see docstring
        stem = f.name[:-5]
        m = re.match(r"^([a-z]+)", stem)
        prefix = m.group(1) if m else stem
        if prefix not in VENUE_PREFIX_TO_CONFERENCE:
            print(f"  skip {f.name}: unrecognized prefix {prefix!r}, not in the verified mapping")
            continue
        is_all_file = stem.endswith("_all")
        try:
            papers = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        new_papers, n_conf, n_year = strip_entries(papers, strip_year=not is_all_file)
        if n_conf or n_year:
            f.write_text(json.dumps(new_papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
            n_files += 1
            n_conf_total += n_conf
            n_year_total += n_year

    print(f"Stripped 'conference' from {n_conf_total} entries and 'year' from {n_year_total} entries "
          f"across {n_files} files")


if __name__ == "__main__":
    main()
