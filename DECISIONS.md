# AV Atlas — design decisions

The reasoning behind choices that aren't obvious from the code, so a future
change doesn't quietly undo something deliberate. `PIPELINE.md` documents which
script pulls which venue; this file is the "why".

## Citations are counted only between papers in the corpus

The site never shows an external citation count. `citation_count()`
(`aggregate.py`) reads only `citations_by_source.in_corpus` — how many other
corpus papers' scanned reference lists cite this one. An external figure
(OpenAlex `cited_by_count`, a Scholar number) measures impact on a different,
far larger population, and lets a paper famous for something unrelated dominate
every AV leaderboard. In-corpus graph coverage is partial and only grows, so
every count is a floor, not a final number — stated as a known gap on the About
page.

## `None` vs `0` for an unknown citation count

`citation_count()` returns `None`, not `0`, when no reference-list scan has
reached a paper yet. Treating "unknown" as "zero" drags every average toward
zero for the years and venues with sparse coverage. `aggregate.py`'s sort keys,
`best_by_year` / `best_by_venue` selection (a paper with no citation data can't
be "best"), and `filters.js`'s `aggregateByDimension` (an uncited paper counts
toward a group's paper count but not its average's denominator) all keep the
distinction. Display falls back to "—", never a misleading "0".

## AV-relevance is a layered, auditable classifier

`classify.py` decides core vs. adjacent from title + abstract text, in layers
that each push only one way:

1. Hard scope filters (mechanical/hardware-only papers, non-road platforms like
   aerial/underwater/legged robots) → `adjacent`.
2. Keyword floor: an AV-specific phrase anywhere, or a standalone driving word
   in the title → `core`. A floor later layers can add to but never override.
3. A small linear model (`data/relevance_model.json`: per-phrase weights plus a
   threshold, trained on hand labels) → `core`, only for papers the keyword
   floor missed.
4. A local-LLM "core" verdict, folded in as a promotion-only signal.

The classifier stays grep-able end to end — any paper's result can be explained
from the code. The LLM is a second opinion, graded against a hand-labeled eval
set and never trusted blind; it never writes the ground-truth label file.

## Numbers are shown as whole numbers

Citation counts and averages are rounded to integers everywhere they're
computed (`aggregate.py`, `filters.js`). "213.68 cit./paper" reads as precise;
the underlying signal is a keyword heuristic over a partial citation graph and
doesn't support that precision.

## Filters recompute rankings client-side from `all_papers`

`aggregate.py` ships the full `all_papers` array, not fixed top-N leaderboards.
`filters.js`'s `aggregateByDimension` rebuilds any ranking from whatever subset
the active filters leave behind. A server-truncated leaderboard can't be
re-sliced by a category / country / institution filter, which made every filter
a dead end on any page but the Overview.

## The citation-source view is a client-side toggle

`stats.json` carries every paper's full `citations_by_source` map, not just the
blended pick. `filters.js`'s `applyCitationSource(stats)` chooses one source and
mutates `p.citations` in place right after the fetch, before anything renders —
so switching sources never needs a `stats.json` rebuild or redeploy. Both
`all_papers` and `top_papers` are mutated (after `JSON.parse` a paper in both is
two objects); `best_by_year` is recomputed client-side for the same reason. The
preference lives in `localStorage`, not the URL.

## Abstracts are sharded out of `stats.json`

Every page fetches `stats.json`; only `paper.html` ever reads an abstract, one
at a time. Bundling ~19k abstracts into the shared payload cost every visitor
several MB of gzip. They live in `data/abstracts/shard-NN.json` (64 shards).
`shard_index()` is a plain djb2-hash-mod-64 of the title — no index file to keep
in sync, just the same hash on both ends. `paper.html` reimplements it in JS;
the two copies must stay byte-identical or every abstract silently 404s.
`test_aggregate.py`'s `TestShardIndex` pins the Python side.

## Venue names are aliased at the source

Sources report the same venue differently ("Advances in Neural Information
Processing Systems" vs. "NeurIPS"). `VENUE_ALIASES` in `aggregate.py` normalizes
them so a venue doesn't split into two rows. `venues.html` additionally merges a
few incidental one-off IEEE-journal placements into a single row — display only;
the `venue` field on each paper is untouched, so filtering by the real name
elsewhere still works.

## Institution names: local-LLM extraction, registry-anchored

Affiliation notes are prose ("...the Division of Robotics, Perception, and
Learning (RPL), KTH Royal Institute of Technology, Stockholm, Sweden"), not a
comma-separated list. Splitting on commas produced fragments — "Perception" as
its own institution — that downstream regex could never fully recover.
`institution_extraction_llm.py` hands the raw text to a local model and asks for
the institution name(s) directly. To stop the same place being re-invented as
"KTH" / "Royal Institute of Technology" / "KTH Royal Institute of Technology",
each call is shown a token-overlap shortlist from
`data/institution_registry.json` and must either return an existing canonical
name exactly or propose a new one, which is added back to the registry.
`data/institution_aliases_llm.json` is a one-time batched pass folding
pre-existing registry duplicates (diacritic and abbreviation variants) onto
canonical names, applied after the hand-typed `INSTITUTION_ALIASES`.

## Scholar profiles and photos: confirm or skip

A profile is saved to `data/scholar_profiles.json` only after a cross-check
against a real signal — a shared co-author on a specific corpus paper, or a
matching institution in the Scholar bio. Ambiguous same-name matches are left
unresolved. A wrong photo on the wrong person is worse than a missing one on a
site whose premise is being defensible about what it claims.

## DBLP-sourced venues have no abstracts

RSS, ICLR, and AAAI are pulled from DBLP (their own sites block scripted
access). DBLP has never carried abstracts, so `is_fully_processed()` requires
only title + year for these, and `classify.py` falls back to title-only keyword
matching. These papers carry a weaker relevance/category signal than the rest of
the corpus, documented as such under Methodology's "Known gaps".

## Derived data is not tracked; `gh-pages` is a single squashed commit

`data/papers_full.json`, `data/stats.json`, `data/stats_adjacent.json`, and the
abstract shards are gitignored — fully regenerable (`merge_corpus.py` then
`aggregate.py`), and tracking a leaderboard dump bloats every diff with numbers
that change on every corpus update and aren't reviewable anyway. Always rebuild
locally before publishing. `deploy.py` force-pushes `gh-pages` as one orphan
commit each time rather than committing on its history: the branch is 100%
generated output, and appending multi-MB non-delta snapshots would grow the repo
forever.

## By-hand data corrections are scripts, not one-off edits

Any manual fix to `papers_full.json` (an author merge, an institution-name
correction) goes in a tracked, tested, idempotent `repair_*.py` script wired
into `build_public_site.py` between `merge_corpus.py` and `aggregate.py` — so a
corpus rebuilt from scratch after data loss comes back with every correction
applied. Author-name fixes live in `KNOWN_NAME_FIXES` (`aggregate.py`) and
institution aliases in the tracked JSON maps, both re-applied on every build.

## One build command, always with tests

`build_public_site.py` runs the whole pipeline in order — rebuild corpus, apply
repairs, rebuild stats, run the full test suite, publish `public/` — and aborts
before publishing if any step fails. Crawler scripts write into
`data/papers_full.json` and stop there; folding every downstream step into the
one command (the same one `deploy.py` calls) makes "crawled but never published"
structurally impossible rather than a step to remember.
