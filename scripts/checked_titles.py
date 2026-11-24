# -*- coding: utf-8 -*-
"""
Remembers which papers a bulk lookup has already looked for, by title, so no
paper is ever asked for twice and papers that appear later in a finished
venue and year still get their turn.

The bulk scripts (fetch_abstracts_openalex.py, fetch_abstracts_crossref.py,
fetch_affiliations_openalex.py) list a whole venue and year at once. They used
to remember only "this venue and year is done", which skipped papers added to
it later. They now store state["checked"][key] = the normalized titles that
were wanted when the listing ran, and only run again for titles not in there.
A key that an older run finished (it is in the legacy list) counts as having
checked every title wanted right now, so nothing already done is repeated.
"""


def left(state, key, wanted, legacy=()):
    """The titles of `wanted` that this key has not looked for yet."""
    checked = state.setdefault("checked", {})
    if key not in checked and key in legacy:
        checked[key] = sorted(wanted)
    return set(wanted) - set(checked.get(key, ()))


def mark(state, key, wanted):
    checked = state.setdefault("checked", {})
    checked[key] = sorted(set(checked.get(key, ())) | set(wanted))
