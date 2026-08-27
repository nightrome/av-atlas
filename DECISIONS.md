# AV Atlas — design decisions and why

This is the "why", not the "how" — `PIPELINE.md` documents which script pulled
which venue; this file documents the reasoning behind choices that aren't
obvious from reading the code, so a future change doesn't accidentally undo
something that was deliberate. Internal notes, not shown on any published page.

## Repo / context-window hygiene

- **Never `Read` a large data JSON directly.** `papers_full.json` (~100MB) and
  `stats.json` (~2MB) are queried with `python -c` one-liners or `grep`, never
  loaded wholesale into an editing session's context. This is a *practice*,
  not something a file-layout change can substitute for — reorganizing files
  doesn't help if the habit is "open the whole file to look at one thing".
- **`data/stats.json` is gitignored**, same as `data/papers_full.json` already
  was. Both are fully regenerable (`merge_corpus.py` then `aggregate.py`) —
  tracking a ~2MB derived leaderboard dump in git just bloats every diff and
  commit with numbers that change on every corpus update, none of which is
  reviewable as a diff anyway. `build_public_site.py` still copies whatever's
  currently on disk into the published output, so this only affects what git
  tracks, not what deploys — always regenerate `stats.json` locally before
  publishing (`python scripts/aggregate.py`).
- Per-page JS (`nav.js`, `filters.js`, `sortable.js`) is already split out of
  the HTML files specifically so a change to shared logic touches one file,
  not eight. Each page's *own* inline `<script>` stays inline deliberately —
  it's page-specific, already small, and there's no build step to bundle
  further splits, so more files would mean more requests for no benefit.

## AV-relevance weighting (`classify.py`'s `av_weight`)

A binary core/adjacent split isn't enough: a paper titled "... for Autonomous
Driving" and a general-purpose paper that lists "autonomous driving" as one of
several downstream applications in its abstract both pass the core check, but
crediting them equally lets the second kind (huge citation counts earned for
something else entirely) outrank genuinely AV-focused work on every
leaderboard. This was caught concretely: "Depth Anything" (a general
depth-estimation foundation model, one abstract mention of AV as an example
application) was ranked #1 overall on raw citations.

The fix is a continuous weight (0–1) instead of a second binary flag:

- **Title match → 1.0.** A CVPR/ICCV/etc. paper that puts an AV-specific term,
  or the word "drive"/"driving"/"driver" (word-boundary matched, so
  "data-driven" never false-positives), in its own title is essentially never
  about something else. This is deliberately more lenient in the title than
  the phrase list used for abstracts — title real estate is scarce and
  authors don't waste it on tangential mentions.
- **Abstract-only match → 0.08 to 0.5, scaled by how many *distinct* AV
  concepts appear**, not how many times one repeats. One passing mention
  ("...applicable to robotics, autonomous driving, and AR...") scores low;
  several different AV-specific ideas discussed throughout scores higher,
  capped at 0.5 — an abstract-only signal should never outweigh a paper whose
  title is actually about AVs.
- **No match at all → 0.0**, and `av_relevance` becomes `adjacent` (same
  behavior as before this change).

Every ranking on the site — Top Papers, best-by-year/venue, and every
Researchers/Institutions/Countries/Categories/Venues leaderboard — sorts by
`citations_weighted = citations * av_weight`, not the raw count. The real
citation count is still shown (with an "AV %" column) so nothing is hidden,
it just can't dominate a ranking on borrowed citations.

**Numeric precision rule, because this bit us once:** citation counts —
including `citations_weighted` and `avg_citations` — are always rounded to
whole numbers, everywhere they're computed (`aggregate.py`, `filters.js`,
`timelines.html`). A figure like "213.68" or "4.3 cit./paper" reads as
falsely precise for what is, at bottom, a keyword heuristic; showing "214" or
"4" instead doesn't overstate its precision the same way. (`avg_citations`
briefly kept one decimal place after this rule was first introduced, but that
was inconsistent with the rest of the rule and was cut too.)

## `merge_corpus.py`'s authors_detail carry-over

`merge_corpus.py` rebuilds `papers_full.json` from scratch from
`data/venues/*.json` + `enriched.json` every time it runs. `enrich_core_authors.py`
(a separate, slow, OpenAlex-budget-limited script) patches `authors_detail`
(institution/country data) directly onto `papers_full.json` in place, not onto
either of those two sources. The first time these two facts collided, a
routine re-run of `merge_corpus.py` silently discarded hours of enrichment
progress — nothing errored, the output just quietly had less data than
before.

Fix: `merge_corpus.py` now reads the *previous* `papers_full.json` (if one
exists) before rebuilding, and re-applies any `authors_detail` it finds there
to matching papers that don't already have richer detail from `enriched.json`.
This makes reruns self-healing instead of a trap — the invariant "rerunning a
script should never make the data worse" now actually holds. A regression test
(`test_merge_corpus.py`) covers exactly this scenario.

## Background scripts: small batches, re-read before each one

`enrich_core_authors.py` originally loaded `papers_full.json` once into memory
and saved its own copy every 100 papers. That's what let the carry-over bug
above actually happen in practice: while one run sat in memory for hours, a
`merge_corpus.py` rerun updated the file on disk, and the next 100-paper save
overwrote those newer changes with the stale in-memory snapshot — observed as
`core_relevant` flip-flopping between two counts across consecutive
`aggregate.py` runs with no code changes in between.

Fix, and the pattern to follow for any future long-running script that
patches this file: re-read `papers_full.json` from disk at the start of every
small batch (`BATCH_SIZE = 20`), process just that batch, write immediately
after. The window in which this process's view of the file can go stale is
now one batch, not one run. Also tracks consecutive lookup failures and exits
early past a threshold (`MAX_CONSECUTIVE_FAILURES = 30`) instead of grinding
through thousands of doomed requests once OpenAlex's rate/daily budget is
exhausted — a long streak of failures is a budget signal, not evidence this
particular batch of papers is unusually hard to find.

## Two concurrent backfills, one shared file: side files + a single merge step

Wanted `fetch_citations_openalex.py` and `build_citation_graph.py` to run at
the same time (one is OpenAlex-rate-limited, the other isn't, so running both
keeps total progress moving instead of sitting idle waiting on a budget). The
first version had both directly read-batch-write `papers_full.json`, same
pattern as `enrich_core_authors.py`. That's safe for *one* long-running
writer (each batch re-reads fresh, see the section above) but not two at
once: each only re-reads at the start of its own batch, so if both happen to
flush within the same few-second window, one silently clobbers the other's
just-written batch. Caught this before it caused real damage — both were
launched, stopped again within seconds once the race was noticed, and
neither had reached its first write yet.

Fix: each backfill writes only to its own exclusively-owned side file
(`data/citations_openalex.json`, `data/citation_graph.json`) and never
touches `papers_full.json`. `apply_citation_sources.py` is the only script
that writes citation data onto `papers_full.json`, and it's a single,
short-lived process — safe to run anytime, including while either backfill
is still going, since it only *reads* their side files. General pattern for
any future pair of scripts that might run concurrently: never have two
long-running processes both own writes to the same shared file, even with a
"re-read before each batch" safeguard — give each its own output and merge
in one place instead.

## Removing the citation-crawl pilot silently emptied the world map

Not caught until the user noticed the Countries map had "lost all color."
Root cause: the citation-crawl pilot (`data/enriched.json`, excluded from
the corpus in an earlier change -- see the pilot-exclusion section above)
was, until then, the *only* part of the corpus where a paper could have both
country data (from its from-the-start author/institution detail) and a
citation count (Scholar-sourced) at the same time. Every other citation
source added since then (`enrich_core_authors.py` for affiliations,
`fetch_citations_openalex.py` / `build_citation_graph.py` for citations)
fills in one or the other, not both together for the same papers -- so
removing the pilot dropped the countries-with-a-citation-count overlap to
*zero*, and every country tile rendered at the same flat "0 citations" color.

Two lessons: (1) a page can look broken even when every individual data
source and script is working exactly as designed, if the *combination* they
depend on stops existing -- worth actually checking cross-field overlap
(`sum(1 for p in papers if p['countries'] and p['citations_weighted'] is not
None)`) after a change that touches either author or citation enrichment, not
just checking each field's coverage in isolation. (2) `apply_citation_sources.py`
+ `aggregate.py` need to actually be rerun for a citation backfill's progress
to reach the live site -- the backfill scripts writing their side files isn't
enough on its own, and it's easy to forget this step exists since the two
backfills run unattended in the background.

## In-corpus citations: fetching and matching are two separate phases

`build_citation_graph.py` originally fetched a PDF, matched its references
against the corpus, and kept only the matches — a reference to a paper not
yet in the corpus was silently discarded. That's real information loss: the
corpus keeps growing (more venues, more years), so a reference that doesn't
match today plausibly will once the paper it points to gets pulled later —
but the discard-on-no-match design meant recovering that edge required
re-downloading and re-parsing the same PDF from scratch.

Fixed by splitting fetching from matching. Fetching (`fetch_phase`, and
`fetch_affiliations_arxiv.py`'s own ar5iv fetch) saves every extracted raw
reference entry to its own file, matched or not, and is skipped on future
runs once a paper's raw list is saved. Matching (`match_phase`) is pure
local string comparison against the current corpus and reruns in full every
single time this script is invoked, fetch or no fetch — cheap enough to
always redo, and it means a corpus expansion is picked up automatically by
the next scheduled run, with zero new network requests for papers already
scanned. `data/citation_graph.json` is now purely the *output* of the match
phase (fully overwritten each run), not something matches are appended to
incrementally.

Same reasoning extends to using ar5iv's bibliography (see
`fetch_affiliations_arxiv.py`) as a second raw-reference source alongside
the CVF-PDF one: it's a strictly better source where it's available (real
`<li>`-per-entry structure vs. PDF-layout-dependent text splitting) and,
since that page is already being downloaded for affiliations, adding it
cost no extra network traffic at all.

## Improving av_weight: a learned classifier, not an LLM

The abstract-only tier of `av_weight_and_relevance` (0.08 / 0.2 / 0.5 by
distinct AV-term count) is the weakest part of the classifier -- those
numbers were hand-picked, not derived from anything. Considered an LLM pass
over each abstract instead; rejected for this project specifically: no paid
API (a standing preference, see [[feedback_no_paid_apis]] in memory), and a
local model on this machine (8-core CPU, no discrete GPU) would take
1.5-4 days for the full ~66k-paper corpus -- only a bounded subset (the
~2,340 abstract-only-matched papers the tier logic actually applies to)
is realistically feasible, and even then loses the auditability every other
part of this pipeline has: `classify.py` can be read top to bottom and you
know exactly why any paper scored what it did, which isn't true of an LLM's
per-paper judgment at scale.

Landed on a small linear classifier instead, trained on hand-labeled
examples: `select_labeling_candidates.py` draws a stratified sample (even
counts across the three current tiers, not a flat random sample, so the
label set actually covers the range) from those 2,340 papers;
`build_labeling_tool.py` stamps them into a self-contained HTML tool
(`label_relevance.html`, gitignored -- generated, not source) for a human to
label Core/Adjacent one at a time, no server needed. Once labeled, the plan
is to train on scikit-learn locally (instant on this hardware for a dataset
this size) but bake the resulting coefficients back into `classify.py` as a
plain weighted-term dict rather than shipping a pickled model -- same
"grep-able, no black box" property the keyword list already has, just with
weights that came from real labeled examples instead of guesses. Title-match
(-> weight 1.0) is untouched; this only targets the abstract-only path.

## Second opinion: a local LLM, graded against hand labels, not trusted blind

After the first classifier attempt (see above) turned up 15 of 28 AV terms
with zero training examples, decided to also try a local LLM as an
independent second opinion rather than only gathering more hand labels one
at a time. Installed Ollama + `qwen2.5:7b-instruct` (~4.7GB, one-time
download) specifically because it runs locally, at no per-call cost, on
this machine's CPU (~8s/paper warm -- no discrete GPU, see the earlier
hardware check this session) -- consistent with the standing no-paid-API
preference. `fetch_llm_relevance_labels.py` labels four pools in priority
order (the 65-paper hand-labeled eval set first, then the abstract-only
tier pool this whole effort is about, then bounded zero-match and
title-match samples), so an interrupted run still produced the most useful
work first.

Two things kept deliberately separate: `data/relevance_labels.json` (human
ground truth) is never written by the LLM script, and
`data/relevance_labels_llm.json` (LLM output, tagged per-record with model
name/pool/timestamp) is never read by anything that treats it as ground
truth. `evaluate_llm_relevance.py` grades the LLM only on the
"eval_groundtruth" pool -- the exact 65 papers a human already labeled -- so
the accuracy number is a real comparison, not the LLM grading its own
homework on papers nobody has independently checked. The LLM's labels on
the other three pools are a candidate training set, not assumed-correct
data, until/unless the eval-pool accuracy says they're trustworthy.

Prompt criteria came directly from the user, not invented: a paper is
"core" only if AV technology is DIRECTLY APPLIED, not merely
potentially-applicable (a generic object-detection paper doesn't count just
because AV systems use object detection); using an AV-specific dataset
(nuScenes/KITTI/Waymo/Argoverse/BDD100K/CARLA) is strong evidence; the bare
phrase "autonomous vehicle" appearing once is a hint, not sufficient alone.
Spot-checked against MonoDepth2 (a general depth-estimation paper that lists
autonomous driving as one of several applications) before the full run --
correctly labeled "adjacent" with that exact reasoning.

## Client-side citation-source switch, not a server rebuild

Wanted a way to pick a single citation source (OpenAlex only, in-corpus
only) and see it applied consistently everywhere, without regenerating
`stats.json` per choice -- that would mean re-running `aggregate.py` (and
redeploying) for every reader who wants a different view, which doesn't
scale and isn't even meaningful for a personal display preference.

Instead: `stats.json` already carries every paper's full `citations_by_source`
(one count per source, not just the blended pick) -- so the source can be
chosen entirely client-side. `filters.js`'s `applyCitationSource(stats)`
mutates `p.citations`/`p.citations_weighted` on every paper in place,
right after `stats.json` is fetched and before anything renders. Every page
already reads those two fields directly off `all_papers` (see "Filters
compute client-side" above) rather than a server-precomputed leaderboard, so
this one function is the only change most pages needed -- one extra
`.then(applyCitationSource)` in the fetch chain.

Two real gaps caught while wiring this up, not by inspection alone:
- **`top_papers`** is a server-side slice of the same list as `all_papers`,
  but after `JSON.parse` a paper appearing in both is two independent JS
  objects, not shared references -- mutating one array left the other
  showing stale numbers. `applyCitationSource` mutates both.
- **`best_by_year`** (shown on the Papers page) was a server-precomputed
  field using the server's blended priority, which wouldn't move when the
  client-side source changed. Replaced with a client-side recomputation
  from `all_papers`, mirroring `aggregate.py`'s own logic (skip papers with
  no citation data, pick the highest `citations_weighted` per year).
- **ICRA/IROS's citation counts** (~379 papers, from `fetch_ieee_openalex.py`)
  predate `citations_by_source` and only ever lived in the old flat
  `citations` field -- that data IS OpenAlex's `cited_by_count`, just
  untagged. Without migrating it, picking "OpenAlex only" would have
  silently shown zero data for most of what OpenAlex actually covers.
  `citations_by_source_for_client()` folds it in at the point `stats.json`
  is written (`papers_full.json` itself is left alone).

The preference is saved to `localStorage` (a personal display setting, not
part of any shareable URL) and set from a picker on the About page.
`nav.js` shows a small badge when a non-default source is active, reading
`localStorage` directly rather than depending on `filters.js`'s exports --
`nav.js` loads before `filters.js` on every page, and duplicating one lookup
was simpler than reordering every page's script tags.

## Citations: `None` vs `0`

`citation_count()` returns `None`, not `0`, when no source reported a citation
figure for a paper. This distinction is load-bearing everywhere a citation
count is aggregated: treating "unknown" as "definitely zero" silently drags
every average toward zero for whichever years/venues/authors happen to have
sparse citation-data coverage (most of 2013–2020, for instance, since
CVF/DBLP/PMLR proceedings pages don't expose citation counts at all). Fixed
end-to-end: `aggregate.py`'s sort keys, `best_by_year`/`best_by_venue`
selection (a paper with no citation data can't be crowned "best"), and
`filters.js`'s `aggregateByDimension` (an uncited paper counts toward a
group's paper count but not its average's denominator) all treat `None`
distinctly from a real `0`. Frontend display falls back to "—", never a
misleading "0".

## Venue name aliasing and merging

Different sources report the same venue under different names (OpenAlex says
"Advances in Neural Information Processing Systems", the CVF/PMLR pulls say
"NeurIPS"). `VENUE_ALIASES` in `aggregate.py` normalizes these at the source
so the same venue doesn't silently split into two rows on every venue-grouped
view. Separately, `venues.html` merges a handful of one-off IEEE journal
placements (TPAMI, IEEE RA-L, IEEE T-IV, IEEE Comm. Surveys — a few papers
each, picked up incidentally by the citation-crawl pilot, not part of the 8
full-proceedings pulls this corpus is built around) into a single "IEEE
(other journals)" row specifically on that page, so a handful of near-empty
rows don't clutter the primary venue comparison. This merge is display-only —
the underlying `venue` field on each paper is untouched, so filtering by the
real venue name elsewhere still works.

## Why the IEEE source badge is text, not a hotlinked logo

Tried hotlinking IEEE Xplore's own favicon first (same pattern already used
for Google Scholar author photos — reference the source's own asset, don't
copy it). Confirmed via `curl` that IEEE's CloudFront blocks it outright
(403), and public favicon-proxy services (Google's `s2/favicons`, DuckDuckGo's
icon proxy) both fail to resolve `ieeexplore.ieee.org` to anything but a
generic fallback icon — so there's no reliable image to hotlink. Landed on a
small text badge in IEEE's own brand blue (`#00629B`) instead: no network
dependency, nothing to silently break again, and it doesn't claim to be their
logo asset.

## Scholar profile/photo lookups: confirm-or-skip, no guessing

Only ~50% of the top-50-researchers pass was completed by hand — for the
rest, a Scholar search either didn't surface a dedicated profile card or
surfaced multiple same-named candidates with no way to confirm which one
co-authored the actual paper in this corpus. In every case, the profile was
cross-checked against a real signal (the co-author list of a specific paper
they wrote, or a matching institution/field in their Scholar bio) before being
saved to `data/scholar_profiles.json`. Ambiguous matches were left unresolved
rather than guessed — a wrong photo/profile linked to the wrong person is
worse than a missing one, especially for a site whose whole premise is being
defensible about what it claims.

Lookups now run in periodic batches, working down the corpus by paper count
so the most-referenced authors get covered first, rather than only in one-off
passes. The confirm-or-skip rule above is unchanged — every save here still
requires a real cross-check signal (a shared co-author, a matching
institution), and a name with no confident match is left unresolved rather
than guessed at.

## ICRA/IROS re-included after cleaning stray HTML markup

Previously excluded from the published corpus (`EXCLUDED_VENUES` in
`merge_corpus.py`) over "data quality concerns on the OpenAlex-sourced pull."
On close review the actual defect was narrow and fixable: a handful of
titles/abstracts (icra2022: 3, iros2021: 2, iros2022: 4) carried raw HTML
markup straight from OpenAlex's own metadata (e.g. `R<sup>3</sup>LIVE`)
instead of plain text. Fixed by stripping tags both in the already-fetched
`data/venues/icra*.json`/`iros*.json` files and in `fetch_ieee_openalex.py`
itself (`strip_html()`, applied to title and reconstructed abstract) so future
pulls come out clean. The handful of entries genuinely missing an abstract
(~1% of each file) are dropped automatically by `aggregate.py`'s
`is_fully_processed()` gate like any other incomplete paper, no special
handling needed. `EXCLUDED_VENUES` removed from `merge_corpus.py`.

Coverage stays sparse (only ICRA2022, IROS2021/2022 — see PIPELINE.md) since
that's a property of OpenAlex's per-year source cataloging, not the markup
bug; widening it further would mean OpenAlex API calls for more years, which
is a real ask given citations were dropped as an OpenAlex dependency (below) —
worth doing only if the extra years' coverage justifies keeping the API
integration around for it.

## Citation counts: dropped OpenAlex as a source entirely, not just from the UI

The citation-source picker was removed from the UI earlier (see "Citation
source: default to X" section) in favor of in-corpus-only citations, but
`fetch_citations_openalex.py` kept running and `apply_citation_sources.py`
kept merging its output into `citations_by_source.openalex` on every paper —
data fetched from OpenAlex's rate-limited API that nothing on the site ever
reads anymore. Deleted `fetch_citations_openalex.py` outright and simplified
`apply_citation_sources.py` to only merge the in-corpus citation graph.
OpenAlex is still used elsewhere for two things with no replacement source:
`enrich_core_authors.py`'s author/institution backfill, and the ICRA/IROS
venue-listing pull itself (IEEE Xplore blocks direct scraping — see
PIPELINE.md) — neither of those is a citation-count dependency, so removing
the citation one doesn't touch them.

## Citation counts: the ICRA/IROS OpenAlex exception was still there, and shipped a real bug

The entry above said OpenAlex was dropped "as a source entirely" — untrue for one path.
`fetch_ieee_openalex.py` (ICRA/IROS's only source, see PIPELINE.md) wrote OpenAlex's
`cited_by_count` onto a flat `citations` field, and `citation_count()` (`aggregate.py`) still
had it as a fallback after `citations_by_source.in_corpus`. That fallback fed the *ranking*
(`top_papers`, author/institution leaderboards, `best_by_year`/`best_by_venue`) while every
page's display always showed the in-corpus count only (`filters.js`'s `applyCitationSource`) —
so a paper could rank in the top 50 on an external number while displaying 0 citations.
User-reported: "top papers 38-50 all have no citations... they clearly are not top papers."

Fixed end-to-end, not just in the ranking function: `citation_count()` and
`citations_by_source_for_client()` now read only `citations_by_source.in_corpus`, no fallback
chain at all. `fetch_ieee_openalex.py` no longer captures `cited_by_count`. The flat
`citations`/`citations_updated` fields already on disk were stripped from `papers_full.json` and
the three cached ICRA/IROS venue-listing files — the site only ever ranks and displays its own
in-corpus citation graph now, with nothing left in the data that could feed a repeat of this bug.
(The citation-crawl pilot mentioned in earlier entries below, `enrich.py`/`data/enriched.json`,
has since been removed entirely rather than just excluded — see "Citation-crawl pilot workflow
removed" further down.)

## OpenAlex dropped from future venue expansion (paid API, not just paid citations)

While investigating widening ICRA/IROS coverage and pulling WACV/ECCV's pre-2020 years (also
OpenAlex-only, per the earlier decision above), a plain OpenAlex API call returned
`{"error":"Rate limit exceeded","message":"Insufficient budget...","dailyRemainingUsd":0}` —
OpenAlex has moved to a paid/budget-limited API since this app was built, and today's free
allotment was already spent by `enrich_core_authors.py` earlier in the session. Per this
project's no-paid-APIs rule, decided not to return to OpenAlex at all rather than wait for a
daily reset and hit the same wall repeatedly. ICRA/IROS stay at their current two years; WACV
pre-2020 and ECCV pre-2018 (also not on their primary sites — see PIPELINE.md) are not pursued.

## RSS/ICLR/AAAI added via DBLP, no abstracts, `is_fully_processed` relaxed

Wanted 2013-present coverage for these three (RSS, ICLR, AAAI), each hitting a source-specific
scraping block on its own primary site:
- **RSS** (roboticsproceedings.org): title/authors/PDF per paper exist, but the site's own pages
  give no reliable way to resolve a volume number (e.g. "rss10") to a publication year — tried
  inferring it from a global `grep` over the page (wrong: sorts/dedupes hrefs, destroying the
  volume-to-external-site pairing) before catching the mistake and switching to DBLP.
- **ICLR** (OpenReview): the bulk notes API now returns `403 ChallengeRequiredError` — a
  browser-solvable CAPTCHA-style challenge, not scriptable.
- **AAAI** (ojs.aaai.org): the archive page is JS-rendered (5.9KB of shell HTML with no content),
  not scrapable via a plain HTTP fetch.

DBLP indexes all three with the exact same per-year HTML structure already handled by
`fetch_dblp_cvpr_gaps.py` (the CVPR-2018-2020-gap fallback) — confirmed by testing its regex
against a live DBLP page for each venue before writing anything. Renamed that script to
`fetch_dblp_listing.py` and generalized it (year ranges, more conferences) rather than writing
three near-duplicate scripts.

DBLP has never had abstracts for anything, at any venue — so `aggregate.py`'s
`is_fully_processed()` gate (added earlier this session, previously required title + abstract +
year) had to be relaxed to just title + year, or these ~26,000 papers would classify fine but get
silently dropped at the very last step. `classify.py` already falls back to title-only keyword
matching when `abstract` is `None` (`abstract_l = abstract or ""`), so nothing else needed to
change — these papers just carry a weaker relevance/category signal than the rest of the corpus,
documented as such in Methodology's "Known gaps."

## Citation-crawl pilot workflow removed entirely

The pilot (`data/seeds.json` -> `data/raw/*.json` -> `enrich.py` ->
`data/enriched.json`) had already been excluded from `papers_full.json` for
a while (see "Removing the citation-crawl pilot silently emptied the world
map" above) but was kept on disk "for reference." Nothing ever read that
reference again, so it was just dead weight: a script, three data-quality
caveats' worth of comments explaining files a future reader would never
otherwise encounter, and a real risk of someone rerunning `enrich.py` by
habit and wondering why its output never shows up anywhere. Deleted
`data/seeds.json`, `data/raw/`, `scripts/enrich.py`, and `data/enriched.json`
outright, and removed `classify.py`'s now-orphaned standalone entry point
(it used to run directly against `data/enriched.json`; the only path that
matters now is `merge_corpus.py` importing `classify_paper()` and applying
it to `papers_full.json`).

## sql.js-httpvfs prototype: real numbers, mixed verdict

Prototyped replacing the Authors page's "download the whole stats.json up
front" model with a queryable SQLite file fetched over HTTP range requests
(`scripts/build_sqlite.py` builds it, `authors_sql_prototype.html` was the
pilot page, never linked from nav or deployed). Vendored `sql.js-httpvfs`
under `scripts/vendor/sqljs-httpvfs/` (self-hosted, required by the CSP).

**Confirmed working end to end**, with real measured numbers (Chrome, this
corpus, this schema): GitHub Pages' CDN genuinely supports HTTP range
requests; a narrow, indexed query cost ~420KB for the top-50 leaderboard
versus the full gzip transfer for the same rendered result, a real ~45x
reduction; second interactions were close to free (page 2 of the same query
cost 0 additional bytes, already cached).

**The catch:** SQLite's query planner did not handle the raw 3-way join
(paper_authors + papers + authors, grouped/ordered/limited) well at all, so
a genuine win needed a precomputed summary table that only covers the
unfiltered default view; a category/venue filter fell back to the expensive
live join. Net result for a full first render was a real but much more
modest ~5x improvement, not the ~45x the narrow-query number suggested.

**Bottom line:** the mechanism is real and GitHub-Pages-compatible, but a
genuine win needs schema/query engineering per query shape, not a drop-in
replacement. Every page with its own aggregate shape (Institutions, Venues,
Countries, Network) would need its own tuned summary table the same way
Authors did. Left as a local-only prototype rather than pursued further;
worth revisiting only if the stats.json transfer size becomes an active
problem, not because the idea is inherently better.

## Filters compute client-side from `all_papers`, not precomputed leaderboards

Early versions had `aggregate.py` precompute `top_authors`/`top_institutions`/
etc. as fixed top-N lists. That made every filter (category, venue, country,
institution) a dead end on any page except Overview, since a country/institution
filter can't re-slice a leaderboard that was already truncated server-side.
`filters.js`'s `aggregateByDimension` now recomputes rankings client-side from
the full `all_papers` array for whatever subset the active filters leave
behind — every page's filters compose with every other page's, uniformly,
instead of each page needing its own bespoke precomputed slice.

## Abstracts sharded out of `stats.json`, not bundled in

Every page fetches `stats.json` on load, but only `paper.html` ever reads
`paper.abstract` — one paper's abstract at a time. Bundling all ~19k
abstracts into the shared payload anyway cost every page ~7MB of gzip
transfer (measured: 19.2MB → 13.0MB after removing them) for a field almost
nobody's page ever touches, discovered while auditing what a first-time
visitor (e.g. from a social share) actually has to download before anything
renders.

Moved to `data/abstracts/shard-NN.json` (`aggregate.py`'s `ABSTRACTS_DIR`,
64 shards, ~125KB gzip each at the largest) instead of one `abstracts.json`
side file, since a single shared blob would still make `paper.html` pay the
full ~13MB weight to show one paper's text. `shard_index()` (a plain
djb2-hash-mod-64) picks a paper's shard from its title alone — no index
file to keep in sync, just the same hash computed on both ends.
`paper.html` re-implements the identical algorithm in JS (there's no shared
module between Python and JS in this app to put it in once); the two copies
must stay byte-for-byte identical or a shard mismatch silently makes every
abstract "not found". `scripts/tests/test_aggregate.py`'s `TestShardIndex`
pins the Python side's output against values cross-checked against a real
Node run of the JS copy — if that test ever needs updating, the JS copy in
`paper.html` needs the same update, not either one alone.

## Institution extraction switched from comma-split to local-LLM, registry-anchored

`fetch_affiliations_arxiv.py` used to pull the raw affiliation-note text out
of ar5iv's HTML and split it on commas (`clean_affiliations()`). Real
affiliation notes are prose, not a flat comma-separated field list — "Authors
are with the Division of Robotics, Perception, and Learning (RPL), KTH Royal
Institute of Technology, Stockholm, Sweden" — so the split produced fragments
no amount of downstream regex cleanup could fully recover (confirmed real
cost: "Perception" surviving as its own fake institution on the leaderboard,
plus a long tail of person-name/footnote/email/funding-credit fragments —
see `aggregate.py`'s `INVALID_INSTITUTIONS`/`PERSON_INITIAL_NAME_RE`/
`OBFUSCATED_EMAIL_RE`/`FUNDING_CREDIT_RE` for the patched-after-the-fact
symptoms this was producing). User-flagged: "extract them in the right way"
instead of extracting garbage and filtering it after.

Replaced with `institution_extraction_llm.py`: the raw, unsplit text is
handed to a local model (Ollama, same `qwen2.5:7b-instruct` setup already
used for AV-relevance labeling — see `fetch_llm_relevance_labels.py` — no new
infra, no per-call cost, consistent with the standing no-paid-API
preference) and asked to extract the real institution name(s) directly.
Verified against the actual real-world examples that motivated this: the
KTH RPL sentence above now correctly extracts just "KTH Royal Institute of
Technology"; "E. Eaton" (a neighboring author's name that leaked into an
affiliation field) correctly extracts nothing; "Huawei † Corresponding
author" correctly extracts just "Huawei".

Registry-anchored to solve the obvious next problem an unconstrained LLM
extraction would have (user-flagged): the same real institution getting
re-invented as "KTH", "Royal Institute of Technology", and "KTH Royal
Institute of Technology" across different papers, fragmenting one
institution's leaderboard entry into three. `data/institution_registry.json`
holds every canonical name already confirmed; each extraction call is shown
a cheap token-overlap shortlist (`build_candidate_shortlist` — no LLM call,
just set intersection, so the prompt never has to carry the whole
multi-thousand-entry registry) of registry entries that might be what the
raw text refers to, and the model is asked to either return one of those
EXACTLY (including by acronym — "KTH" matching "KTH Royal Institute of
Technology" is exactly the case token-overlap alone can't bridge, which is
why the LLM sees the shortlist rather than this being purely a string-
matching problem) or propose a new canonical name. Every result — matched or
new — gets added back into the registry (`resolve_and_register`), so later
extractions in the same run (and every future run) have a growing set to
match against instead of compounding the duplication.

Cached by exact raw text (`data/affiliations_llm_extracted.json`), not by
paper — the same lab's boilerplate affiliation sentence repeats verbatim
across many of that lab's papers, so this keeps the actual number of LLM
calls bounded by vocabulary size, not occurrence count.

Known limitation, accepted rather than solved: the existing ~7,000 papers
already processed under the old comma-split never had their raw sentence
preserved (`clean_affiliations()` discarded it immediately after splitting),
so this only benefits newly-fetched papers until a deliberate re-crawl
re-fetches ar5iv pages for the existing backlog specifically to recover
their raw text. That re-crawl is real, comparable in size/duration to the
has_code_link recheck, and deliberately not run in the same session this
landed in (avoids a second writer racing the crawler already running against
the same files) — queued as follow-up work, not forgotten.

## Institution registry canonicalization pass (retroactive, LLM-batched)

The extraction fix above stops new duplication from being created; it does
nothing about the ~6,200 institution names already sitting in the registry
from before it existed, many of which are still-valid-but-differently-
spelled versions of the same real institution: diacritic variants ("ETH
Zurich" vs "ETH Zürich"), abbreviation vs. spelled-out forms ("TUM" / "TU
Munich" / "Technical University Munich" / "Technische Universität München"
all naming the same place), a department/campus prefix in front of an
identifiable parent ("Department of Electronics. University of Alcalá"),
and a corporate lab name that's really just its parent company ("Valeo.ai",
"Bosch Corporate Research", "Cross-Domain Computing Solutions"). User-
flagged with a long list of real examples, several of which independently
converged on the same author (Johannes Betz's own papers alone carried 8
different spellings of Technical University of Munich, confirmed on real
`papers_full.json` data) — the concrete illustration of why one-off aliases
don't scale here either: hand-typing ten more `INSTITUTION_ALIASES` entries
would only have covered the ten examples actually reported, not the
hundreds of similar unreported cases sitting in the same registry.

Solved the same way the invalid-institution flags were (see
`institution_flags_llm.json`'s own generating comment): one full read-through
of every currently-valid registry entry (3,484 names, invalid ones already
excluded), not a per-name local-LLM call — a local Ollama call takes ~20s
each on this hardware, which would be ~19 hours across the full list for a
one-time backfill; a single batched review pass is both faster and, freed
from a fixed per-call prompt budget, able to actually compare names against
each other rather than judge each in isolation. Produced
`data/institution_aliases_llm.json`, a flat `{variant: canonical}` map,
loaded once at import time into `INSTITUTION_ALIASES_LLM` and applied at the
end of `normalize_institution()`, after the hand-typed `INSTITUTION_ALIASES`
dict (same two-tier "hand-typed first, LLM-sourced fills in the rest"
pattern as `INVALID_INSTITUTIONS`/`INVALID_INSTITUTIONS_LLM`).

Conservative by construction: the review was explicitly told to leave a pair
unmapped when genuinely unsure rather than guess, and it did — e.g. "TU
Munich"/"University of Munich" (TUM vs. LMU, different schools with
overlapping short names), "Tokyo Institute of Technology"/"University of
Tokyo", and "Georgia Institute of Technology"/"University of Georgia" were
all correctly left as separate institutions despite superficial similarity.
Even so, spot-checking the first pass (structural checks for cycles/self-
maps, a random sample, and a systematic scan for any mapping that silently
dropped a diacritic or hyphen) caught ~15 genuinely wrong-direction entries
before this shipped — diacritics stripped the wrong way ("Koç University" ->
"Koc University", losing real information), a hyphen dropped from an
official brand name ("Mercedes-Benz" -> "Mercedes Benz"), and one over-broad
merge not actually requested ("NVIDIA Research" folded into "NVIDIA", which
would have erased a real distinction the existing test suite already
depended on). Fixed by hand rather than treated as acceptable noise, the
same "verify before trusting an LLM pass at scale" standard the earlier
invalid-institution flags were held to.

Retroactively resolves the "existing papers never had their raw sentence
preserved" limitation noted above for every case where the fragments
`clean_affiliations()` left behind are still individually valid institution
names (Betz's case exactly — TUM's various spellings all survived as
distinct valid entries, just never linked to each other) without needing the
queued re-crawl at all. It doesn't help the cases where the OLD comma-split
left genuine junk fragments (postal codes, person names, mid-sentence
prose) rather than a valid-but-differently-spelled institution name — that
class of fix still needs either the queued re-crawl or a raw-text
reconstruction pass, and remains queued, not solved by this.

## Reproducibility audit: mine_abstracts.py given a side file, glued-institution fix given a repair script

User-asked: "how much of the database is actually reproducible... did some
[manual edits] write directly to the files and therefore we cannot
reconstruct the database in the future?" A real audit, not a reassurance --
checked every file under `data/` against `.gitignore` and cross-referenced
each gitignored one against the script that's supposed to regenerate it.
Found two genuine gaps, both closed:

**mine_abstracts.py had no side file.** Every other fetcher in this
pipeline (`fetch_affiliations_arxiv.py`, `fetch_cvf_affiliations.py`,
citation sources) writes to its own small `data/*.json` side file, folded
into `papers_full.json` by a separate `apply_*.py` -- so even if
`papers_full.json` is lost, the side file alone can reconstruct that slice
without re-crawling. `mine_abstracts.py` wrote straight into
`papers_full.json`'s `abstract`/`abstract_search_exhausted` fields directly,
by its own design ("no separate side file... no disambiguation step
downstream that needs the raw fetch preserved" -- true, but beside the
point: the raw fetch was still the ONLY record of potentially days of
rate-limited arXiv API work). Fixed by giving it the same shape: writes to
`data/abstracts_arxiv.json` only, `apply_abstracts_arxiv.py` (new) folds it
into `papers_full.json`, same "only fills gaps, safe to run anytime"
contract as `apply_affiliations_arxiv.py`. Backfilled the cache from
`papers_full.json`'s current state on introduction (121,802 abstracts +
8,321 confirmed-no-match markers) so this protects the whole project's
accumulated abstract-mining history, not just abstracts mined after this
change shipped.

**The glued-institution-string fix (see the entry above) was a one-off
`python -c` edit, not a script.** `papers_full.json` is gitignored and
"regenerable" is the whole premise of that being safe -- but an edit that
only ever touched the live file, with no tracked record of what it did,
breaks that premise for exactly the data it touched. `scripts/
repair_garbled_authors_detail.py` already established the right pattern
for this class of fix (a past session's own one-off repair, kept as a real,
tracked, tested, re-runnable script rather than a shell one-liner) --
`repair_glued_institution_strings.py` (new, with a test) follows it:
idempotent, a no-op if the glued strings are already gone, safe to re-run
after any future re-crawl that might reintroduce the same shape of garbled
text.

Not fully solved by this audit, left as accepted risk: the affiliations/
citation-graph/reference-list side files (`affiliations_arxiv.json`,
`affiliations_cvf.json`, `citation_graph.json`, `reference_lists_*.json`)
are gitignored and depend on external services (arXiv, CVF, OpenAlex)
staying available and unchanged to be re-fetched from scratch -- genuinely
regenerable in principle, but a real multi-day undertaking in practice, not
instant. `data/venues/*.json` (the actual proceedings listings, tracked)
remains the one dataset this whole pipeline cannot survive losing.

## Keep GitHub repo size small: gh-pages squashed to one commit per deploy

User-asked, after the reproducibility audit above surfaced how big `main`
and `gh-pages` actually are (144.6MB / 141.3MB working-tree, 112MB packed
`.git`): make the overall repo as small as possible going forward.

`gh-pages` is 100% generated build output -- `stats.json`/
`stats_adjacent.json` (the two biggest files by far, ~120MB combined) plus
sharded abstracts and the HTML/CSS/JS, all reproducible from `main`'s
tracked sources by rerunning the pipeline. Nobody has any reason to look at
an old commit's diff of a generated leaderboard dump. `deploy.py`
previously committed on top of `gh-pages`' existing history every deploy,
the normal git workflow but wrong for this branch specifically: `git
verify-pack` on the 24 accumulated deploy commits showed only 17 of 315
objects had ANY delta chain, meaning each deploy was adding a genuinely new,
largely non-delta-compressible multi-MB chunk to the repo forever, not
reusing space via similarity to the last snapshot.

Fixed: `deploy_gh_pages()` now always checks out a fresh orphan branch and
force-pushes a single commit, discarding gh-pages' prior history every
time, same practice `peaceiris/actions-gh-pages` and the `gh-pages` npm
package both default to for exactly this reason. gh-pages' contribution to
repo size is now flat at ~one snapshot regardless of future deploy count.
Old commits become unreachable on the remote and get reclaimed by GitHub's
own server-side maintenance (not instant, but not something this repo needs
to manage).

Not addressed here, left as a separate lever if repo size becomes a
problem again: `main`'s own size (144.6MB) is almost entirely
`data/venues/*.json`, genuinely-needed tracked source data, not build
output -- shrinking that means either a more compact on-disk format
(trades off the "reviewable as a diff" property these files are kept
verbose JSON for) or accepting the size as the real cost of keeping the
one dataset this whole pipeline can't regenerate.

## data/venues/*.json: dropped redundant per-entry "conference"/"year"

Follow-up to the two entries above. Every entry in a per-venue-year file
(e.g. `cvpr2024.json`) repeated the same `"conference": "CVPR", "year":
2024` -- 265,378 entries, 11.1MB of pure redundancy, fully determined by
the filename. `scripts/strip_redundant_venue_fields.py` removes both from
193,539 entries across the 194 per-venue-year files, and just `"conference"`
(kept `"year"`, which genuinely varies per entry) from the 6 continuous-
journal `*_all.json` files (`ijcv`/`ijrr`/`ral`/`tits`/`tpami`/`tro`).
`arxiv_s2_citing.json` (untracked, genuinely non-uniform per entry -- a bulk
citation-discovery crawl, not a single-venue listing) is untouched.
`merge_corpus.py`'s `conference_and_year_for_file()` reconstructs both from
the filename (a real, once-verified 1:1 prefix -> conference mapping, not
guessed), falling back to a per-entry field only when the file still has
one -- so this is safe to apply gradually; a not-yet-migrated venue file
keeps working exactly as before.

Real but modest: `data/venues/`'s git tree size dropped from 138.7MB to
131.1MB (~5.5%) -- smaller than the raw 11.1MB estimate since git already
compresses the removed text reasonably well too, same reasoning as the
gh-pages entry above. The fetcher scripts themselves (`fetch_cvf.py`,
`fetch_neurips.py`, ...) still write the old, redundant schema -- this is a
post-processing step, not a fetcher change -- so `strip_redundant_venue_
fields.py` needs a re-run after fetching a new venue file, before
committing it.
