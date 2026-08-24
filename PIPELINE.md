# AV Atlas data pipeline

How the corpus in `data/` was built, script by script. Written down here so the
fetch scripts (many, built iteratively) can be pruned to just what's actually
load-bearing, without losing the record of how each venue was pulled.

## Per-venue scripts (current, in use)

| Venue | Script | Source | Notes |
|---|---|---|---|
| CVPR / ICCV / WACV | `fetch_cvf_history.py` (uses `fetch_cvf.py`) | openaccess.thecvf.com | Full title+authors+abstract per paper, but only 2020 onward for WACV -- CVF Open Access has no earlier WACV editions at all. CVPR 2018-2020 and WACV 2013-2019 fall back to `fetch_dblp_listing.py` (DBLP, title+authors only, no abstract); CVPR's is CVF's `?day=all` listing 500ing for those three years specifically ("Error 1525: Incorrect DATE value"), WACV's is the source gap above. |
| ECCV | `fetch_ecva_history.py` | ecva.net | Only 2018/2020/2022/2024 exist there (biennial mirror) — ecva.net itself has no earlier years, confirmed by fetching its own listing page; not something an alternate fetch script can work around. |
| NeurIPS | `fetch_neurips_history.py` (uses `fetch_neurips.py`) | proceedings.neurips.cc | Listing link format changed around 2020 (`-Abstract.html` vs `-Abstract-Conference.html`); handled by one regex that matches both. |
| CoRL | `fetch_corl_history.py` | proceedings.mlr.press | PMLR volume number per year is hardcoded (`CORL_VOLUMES` dict) — not derivable from the year, had to be looked up per edition. |
| ICRA / IROS | `fetch_github_paper_lists.py` | Community-maintained GitHub paper lists | IEEE Xplore itself returns HTTP 418 to any direct request (bot-blocked). OpenAlex (`fetch_ieee_openalex.py`, still used for ICRA2022/IROS2021-2022's title+authors+abstract) has since moved to a paid/budget-limited API (confirmed 2026-08-17: `"Insufficient budget"`, `dailyRemainingUsd: 0`) and per this project's no-paid-APIs rule isn't used to widen coverage further. Found via `hrjp/ICRA-IROS-PaperList`, an index of per-year community repos (title+authors only, no abstract) covering 2019-2025 for both conferences — three different repo-family formats (plain bullets under `## Category` headings, a comma-separated-authors markdown table, a semicolon-separated `Last, First` table), each confirmed by reading the raw file directly since assuming one family's format from another's led to real corruption during development (see the script's own docstring and tests/test_fetch_github_paper_lists.py). Every paper carries `source_url` (the exact repo it came from) through merge_corpus.py onto papers_full.json, shown on paper.html. 2013-2018 and 2026 still have no known source. |
| RSS / ICLR / AAAI | `fetch_dblp_listing.py` | DBLP | Same title+authors+year-only, no-abstract tier as the CVPR gap-fill above, but the primary source here for all three, not a fallback. RSS's own proceedings site (roboticsproceedings.org) has titles/authors but no reliable volume-to-year mapping from the site itself; ICLR's OpenReview bulk API now requires a browser-solvable challenge (`403 ChallengeRequiredError`); AAAI's ojs.aaai.org archive page is JS-rendered, not scrapable via a plain HTTP fetch. DBLP indexes all three with a stable per-year URL (`dblp.org/db/conf/<key>/<key><year>.html`), same page structure the CVPR gap-fill already parsed. |
| ICML / BMVC / ACCV / ICPR / ICASSP / ICIP | `fetch_dblp_listing.py` | DBLP | Found not by a coverage gap in the venue list itself, but by mining `data/reference_lists_cvf.json`/`reference_lists_arxiv.json` (raw extracted reference text from this corpus's own papers) for parenthetical venue codes cited often but not yet covered (user-requested) -- ICML alone showed 207 raw citations. Same generic DBLP path as RSS/ICLR/AAAI; ACCV/ICPR are DBLP multi-part years (`accv2024-1.html` .. `-N.html`), already handled by the existing ECCV-style pagination fallback in `fetch_year()`. ICML and BMVC are fully fetched (2012-2024); ACCV only reached 2012 and ICPR/ICASSP/ICIP haven't started -- DBLP began returning `RemoteDisconnected` mid-fetch (confirmed via a direct connection test, not something retries could paper over) after this session's cumulative request volume, so the remaining fetches are paused rather than retried immediately. `fetch_common.py`'s `fetch()` now also retries on a bare `ConnectionError`, not just `HTTPError`, after this was caught losing an in-progress 15-year ICML fetch on year 14. |
| TOG (ACM Trans. on Graphics) | `fetch_dblp_listing.py --journal` | DBLP | Same reference-mining discovery as above; not yet fetched (see the note above). |

## Citation-crawl pilot (kept separate, not part of the corpus)

`enrich.py` + `fetch_abstracts.py` built `data/enriched.json`: ~43 papers found by
following citations *out* of nuScenes/KITTI/Waymo (`data/seeds.json`, `data/raw/`)
via Google Scholar, then enriched with OpenAlex authorship + arXiv/Crossref author-
list verification.

**No longer merged into `papers_full.json`.** It was a different, non-uniform
sampling method — a paper's presence depended on whether it happened to cite one
of ~100 seed dataset papers, not on being published at one of the eight venues
everything else is drawn from. Mixing the two made "how was this paper found" an
invisible variable across the corpus. `data/enriched.json` still exists on disk
for reference (it has the only real institution/country/Scholar-citation data
collected from the start) but nothing on the site reads it anymore.

## Merge, classify, enrich, aggregate

- `merge_corpus.py` — dedupes by normalized title across every `data/venues/*.json`
  file (not `enriched.json`, see above), classifies each paper's `category` (topic)
  and `av_relevance` (`core`/`adjacent`) via `classify.py`, writes `data/papers_full.json`.
- `classify.py` — keyword-matching category assignment (`data/categories.json`,
  a living taxonomy, not fixed) + AV-relevance decided by an explicit
  AV-specific phrase list (`AV_RELEVANCE_TERMS`), independent of category —
  category keywords are generic CV/robotics terms that also match plenty of
  non-AV papers, so category membership alone was never a valid relevance signal.
- `enrich_core_authors.py` — the venue-listing pulls only ever captured a plain
  author-name string, not affiliations, so institution/country stats were built
  from just the ~15 citation-crawl-pilot papers that happened to have real
  author detail. This backfills OpenAlex author/institution/country data for
  every `av_relevance == "core"` paper (not the full ~66k corpus — no reason to
  spend OpenAlex's rate budget on papers that were never going to be ranked).
- `fetch_affiliations_arxiv.py` — a second, independent source for the same
  `authors_detail` field, for core papers OpenAlex hasn't reached yet.
  Resolves each paper's arXiv ID via arXiv's own search API, then parses
  ar5iv's full-text HTML rendering for each author's institution (no rate
  limit like OpenAlex, but only covers papers with an arXiv preprint that
  has affiliations in its LaTeX source). ar5iv gives institution *names*,
  not ISO country codes — `data/institution_countries.json` is a small
  hand-curated map (real web lookups, not guessed) from name to country
  code, applied by `apply_affiliations_arxiv.py`. An affiliation not yet in
  the map still shows up on the Institutions page, just contributes no
  country until someone adds it. Writes to its own side file
  (`data/affiliations_arxiv.json`), same race-avoidance reasoning as the
  citation backfills below — `apply_affiliations_arxiv.py` is the single
  writer onto `papers_full.json`, and only applies to papers OpenAlex
  hasn't already enriched (OpenAlex's data is richer, never overwritten).
- `aggregate.py` — reads `data/papers_full.json`, writes `data/stats.json`
  (what the UI actually consumes). Leaderboards rank `core`-only.

## Citation counts

The site only ever shows one citation count per paper: `in_corpus`, how many
other papers already in this corpus cite it (see Methodology and
DECISIONS.md's "Citation counts: dropped OpenAlex as a source entirely"
entry). `papers_full.json` stores counts in `citations_by_source`
(`{"in_corpus": {"count": N, "updated": "..."}, ...}`); `citation_count()`
(`aggregate.py`) reads only `citations_by_source.in_corpus` and nothing
else — no fallback to any external provider, for ranking or for display.

No script writes an OpenAlex (or Semantic Scholar, or Scholar) citation
count into `papers_full.json` anywhere in the pipeline. `fetch_ieee_openalex.py`
(ICRA/IROS's only source) fetches title/authors/abstract from OpenAlex but
does not capture its `cited_by_count`. This used to be an exception — a flat
`citations` field on ICRA/IROS papers that `citation_count()` migrated into
`citations_by_source.openalex` as a legacy fallback — but that fallback let
a paper rank in the top 50 by an external count while displaying 0
citations client-side (in-corpus only is ever shown), a real user-reported
bug. Removed at the source, not just from the ranking logic, so it can't
recur: the field is stripped from `papers_full.json` and no longer fetched.

- `build_citation_graph.py` / `fetch_affiliations_arxiv.py` — a different
  signal: how much AV papers in this corpus cite *each other*, not a global
  count. Two raw-reference sources feed one match step:
    - `build_citation_graph.py` downloads the PDF for each `core`
      CVPR/ICCV/WACV paper (CVF-hosted, no rate limit) and extracts the
      references section (pdfplumber) — noisy, PDF-layout-dependent text.
    - `fetch_affiliations_arxiv.py` (see above) already fetches ar5iv's
      full-text HTML for affiliations; since that page is already in hand,
      it also extracts ar5iv's cleanly-structured bibliography (one
      `<li class="ltx_bibitem">` per entry) at zero extra network cost.
      Covers any venue with an arXiv preprint, not just CVF.
    Both write their raw, unmatched reference lists to their own file
    (`data/reference_lists_cvf.json`, `data/reference_lists_arxiv.json`) —
    fetching and matching are deliberately separate phases.
    `build_citation_graph.py`'s match phase runs on every invocation (even
    if it fetched nothing new that run) and rematches every saved reference
    list against the *current* corpus, so a reference to a paper that isn't
    indexed yet automatically starts matching once that paper is pulled
    later — no re-fetching needed, just a rerun.
- All three write to their own side file, never directly to
  `papers_full.json` — that lets them run at the same time without racing
  each other over the same multi-MB file (see their docstrings).
  `apply_citation_sources.py` is the single-writer merge step that folds
  `citations_openalex.json` and `citation_graph.json`'s matched edges into
  `papers_full.json`; run it after any of the backfills (or several in
  parallel) make progress, before `aggregate.py`.

## Known data-quality caveats (see also the Methodology page in the UI)

- OpenAlex's author→institution linking is occasionally wrong on its own data,
  independent of anything here — e.g. "InternetLab" (a Shanghai AI Lab
  collaboration hub) gets tagged country `BR` because OpenAlex has conflated it
  with an unrelated Brazilian NGO of a similar name. Not something this
  pipeline can correct without per-institution manual overrides.
- CVPR 2018-2020 have no abstracts (DBLP fallback, see above).
- ICRA/IROS coverage is sparse by design tradeoff, not a bug — see table above.

## Scripts removed as superseded (functionality fully absorbed by the above)

- `fetch_cvf_listing.py` — listing-only (no abstracts) CVF pull; superseded by
  `fetch_cvf_history.py` once abstracts were needed for every year anyway.
- `fetch_pmlr_listing.py` — listing-only CoRL pull; superseded by
  `fetch_corl_history.py`.
- `fetch_neurips_full.py` — single-year NeurIPS runner; `fetch_neurips_history.py`
  with the same start/end year does the same thing.
- `fetch_ecva_listing.py` — its own listing-only ECCV pull; `fetch_ecva_history.py`
  parses the listing itself rather than importing it (its docstring used to claim
  otherwise, a leftover from an early version — the import was already unused).
- `fetch_abstracts.py` — a one-off patch that backfilled abstracts into an
  already-collected `enriched.json` from before `enrich.py` captured them
  natively; not needed for any run going forward, `enrich.py` does this itself now.
