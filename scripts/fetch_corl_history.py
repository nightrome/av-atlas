#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full CoRL history (2017-2025) with abstracts, from PMLR. The parsing and
fetching live in fetch_pmlr.py (shared with ICML); this is the CoRL entry
point kept for the existing docs and habits. CoRL year -> PMLR volume
mapping is PMLR_VOLUMES["CoRL"] in fetch_pmlr.py (PMLR volume numbers
aren't derivable from the year alone).

Years whose file already exists are skipped unless --force is given, so
adding a new edition doesn't refetch all the old ones.

Usage: python fetch_corl_history.py [START_YEAR [END_YEAR]] [--force]
"""
import sys

import fetch_pmlr

CORL_VOLUMES = fetch_pmlr.PMLR_VOLUMES["CoRL"]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    years = [a for a in argv if not a.startswith("-")]
    flags = [a for a in argv if a.startswith("-")]
    if not years:
        years = [str(min(CORL_VOLUMES)), str(max(CORL_VOLUMES))]
    fetch_pmlr.main(["CoRL", *years, *flags])


if __name__ == "__main__":
    main()
