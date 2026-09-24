# AV Atlas data pipeline

How the corpus in `data/` was built, script by script. It's written down here so
the fetch scripts (there are many, built one at a time) can be pruned to what is
actually needed without losing the record of how each venue was pulled.

## Current blockers and limits

### DBLP blocks scripted access (since 2026-09-13)

dblp.org now serves every `db/conf/...` and `db/journals/...` page, including
years that used to fetch fine, behind [Anubis](https://github.com/TecharoHQ/anubis),
a proof-of-work bot challenge. The response is `200 OK` with a JavaScript
challenge page ("Making sure you're not a bot!"), not a 403 or 503, so
`fetch_common.py`'s retry logic can't detect it or back off. We confirmed it on
a year we had never fetched (ACCV 2020) and one we had (ACCV 2018).

This is an anti-bot gate, not a rate limit. Getting past it would mean automating
around bot detection, which this project won't do.

Affected: every venue sourced from `fetch_dblp_listing.py` (RSS, ICLR, AAAI,
ICML, BMVC, ACCV, ICPR, ICASSP, ICIP, ITSC, IV, T-ITS, TOG), plus the DBLP
fallback years for CVPR (2018-2020) and WACV (2013-2019). Treat all of them as
frozen at what is already in `data/venues/`, and revisit only if DBLP's access
policy changes.

### OpenAlex and arXiv rate limits (observed 2026-09-14)

Two backfill scripts exist to collect data that these sources still give away
for free: `enrich_av_authors.py` (OpenAlex; about 15,900 AV papers still had no
`authors_detail`) and `mine_abstracts.py` (arXiv; about 2,000 AV papers still
lacked an abstract, 89% of them ITSC/IV/T-ITS). Both hit a wall in the same
session. We tested each with a bare request outside the scripts, and the two
walls turned out to be different.

**OpenAlex has a hard daily budget.** A direct `GET /works?search=...` (the
endpoint both `enrich_av_authors.py` and `fetch_ieee_openalex.py` use) returned:

```
HTTP 429, Retry-After: 23062
{"error":"Rate limit exceeded","message":"Insufficient budget. This request
costs $0.001 but you only have $0 remaining. Resets at midnight UTC. ...",
"dailyRemainingUsd":0,"prepaidRemainingUsd":0,"creditsRemaining":0}
```

It's the same wall `fetch_ieee_openalex.py` hit on 2026-08-17, so it applies to
OpenAlex's search endpoint in general, for everyone sharing this project's
`mailto`. The first `enrich_av_authors.py` run enriched 185 papers, then stopped
itself after 35 consecutive failures, having spent the day's remaining credit
down to $0. A second run about 20 minutes later got 0 of its first 40 attempts.
The budget resets at midnight UTC, so retrying sooner can't help, however the
requests are spaced or batched.

The project doesn't use paid APIs, so the answer is to accept that
`enrich_av_authors.py` makes a small amount of progress once per UTC day. A
midnight retry isn't worth scripting. Just re-run it now and then.

**arXiv is blocking us, but we haven't confirmed a daily reset.** A direct
`GET export.arxiv.org/api/query` returned `HTTP 429` with the body
`Rate exceeded.`, no `Retry-After` header and no reset time. Two `mine_abstracts.py`
runs about 20 minutes apart both failed on every request, even in the fast
ID-lookup pass, and both hit the script's own 20-failure guard within a minute.
arXiv only documents "no more than one request every three seconds" (already
`REQUEST_DELAY` in `mine_abstracts.py`) and not how long a block lasts. Public
reports range from a few minutes to several hours
([arXiv API Google Group](https://groups.google.com/a/arxiv.org/g/api/c/pNB3lnxf4mQ),
[API user's manual](https://info.arxiv.org/help/api/user-manual.html)). The
likely, unconfirmed cause is that earlier citation-graph and affiliation crawls
that week had already used up arXiv's tolerance for this machine's shared IP.
Waiting and retrying is the only lever. Don't build a retry that assumes a daily
reset.

**Our own network was also flaky that day.** About 90 minutes later a plain fetch
of `google.com` took 11 seconds, and `backup_corpus.py`'s upload to GitHub
Releases failed twice on a TCP write timeout. Once things settled, three real
title-search probes 3 seconds apart came back clean, and a full `mine_abstracts.py`
re-run finished its ID-lookup pass at 200/200 with no failures.

Both things were probably true: the arXiv block lifted, and part of what looked
like blocking was us failing to reach arXiv reliably. The clean `HTTP 429`
responses are real answers from arXiv's server. The interleaved
`The read operation timed out` and connection errors are what local instability
looks like, and they made the block look more total than it was. So when a block
looks total on one attempt, do a plain retest (not just the same script again)
before concluding it's still up.

### Other abstract sources we looked at

None were adopted.

- **Google** returned a bot-check page to this project's browser tooling on the
  first query, so we tried no scripted search after that. Ordinary web search
  works fine and is the right tool.
- **ResearchGate** returns 403 or redirects to its homepage without a login.
- **IEEE Xplore**, where most of the remaining gap sits, returns its known `418`
  to scripts. It does show the full abstract to a real browser session with no
  sign-in (confirmed on one paper), but we deliberately didn't pursue that. The
  `418` is IEEE's anti-bot signal, like DBLP's Anubis challenge, and driving a
  real browser at scale to get past it is the same kind of bypass we ruled out
  for DBLP.

### Finding arXiv links by hand

Web search works as a manual, one-title-at-a-time way to find arXiv links, but
only for one slice of the gap. Search result pages resist scripting as well:
Google served a bot check, DuckDuckGo's `html.duckduckgo.com` returns a challenge
page with no results, and Bing returns a full page whose results are injected by
JavaScript (we fetched a query with a known arXiv hit and found no `arxiv.org`
link in the markup). So this can't become an unattended `fetch_*.py` script like
every other source here.

We tried 40 titles from the missing-abstract pool, one query each, and checked
each candidate against the corpus's exact title. Three tempting candidates were
rejected at first: an "Attention-guided...Risky **Objects**" paper offered in place
of our "...Risky **Traffic Agents**" extended abstract, a "Constant
**Acceleration**" follow-up offered for the "Constant **Velocity**" original, and
"End-to-End Learned Event- and Image-based Visual Odometry", whose wording was
close but not exact for "End-to-end Learned Visual Odometry with Events and
Frames". The yield varied a lot by segment:

- **Older ITSC/IV-style papers** (before 2021, classical ITS and vehicle control):
  0 hits out of 16. Half were titles arXiv's search had never seen and half had
  already been searched and recorded as no match. These venues and this era
  seem to sit outside arXiv's posting culture, so it's a real absence, not a
  rate-limit gap. Don't expect `mine_abstracts.py` to recover much of this
  segment even after arXiv's block lifts.
- **Recent (2021+) ICLR/ICML/BMVC papers:** 0 hits out of 8, for a different
  reason. These venues host their own open-access copies (OpenReview, PMLR, the
  BMVA archive) and authors often don't cross-post to arXiv. All 8 resolved to
  the venue's own page.
- **Recent (2021+) IROS/ICRA/RA-L/T-ITS/ITSC/IJCV papers with an ML or perception
  flavor:** 5 hits out of 16 (about 31%): SSCBench (`2306.09001`), DriVLMe
  (`2406.03008`), Navya3DSeg (`2302.08292`), a mixed-traffic string-stability
  paper (`2309.01625`) and a trajectory-forecasting paper (`2503.04994`). This is
  the one segment where manual linking is worth the effort, and it has a
  preprint culture similar to CVPR and ICCV.

The 5 hits went into `data/arxiv_ids.json` (the side file `fetch_arxiv_links.py`
also writes) and were folded in with the existing `apply_arxiv_links.py`. No new
script was needed. Repeat this occasionally on the recent ML slice; it isn't
worth building a crawler for.

**Title matching is looser for the manual process.** Every scripted source
(`find_arxiv_id`, Semantic Scholar's title-match endpoint, ...) requires exact
normalized-title equality on purpose, because a fuzzy same-topic match could
silently attach the wrong paper. With a human, or an LLM checking one candidate
at a time and reading the actual method and authors, that risk is much smaller.
So the manual process also accepts a title that was clearly reworded between the
arXiv preprint and the camera-ready version, provided something independent
confirms it: a Semantic Scholar abstract for the same method, an identical author
byline, or a distinctive name unlikely to collide (Navya3DSeg, SSCBench, DriVLMe,
RAMP-VO). Under that standard:

- The "Risky Traffic Agents"/"Risky Objects" pair is accepted (`2209.07922`). It's
  the same attention-guided multistream fusion network with the same authors, and
  "Traffic Agents" is the wording the IEEE version settled on.
- The "Constant Velocity"/"Constant Acceleration" radar-odometry pair stays
  rejected however loose the title standard gets. External sources describe the
  acceleration paper as an explicit follow-up, so these are two papers with
  similar names, not one paper with two names.
- The third candidate turned out to be a bug worth more than a link. The corpus
  already had both titles as separate records: "Deep Visual Odometry with Events
  and Frames" (tagged 2023, from arXiv, with the right abstract and `arxiv_url`)
  and "End-to-end Learned Visual Odometry with Events and Frames" (tagged IROS
  2024, from a venue listing, enriched with neither), with an identical
  7-author byline. One real publication was being double-counted in every
  author, venue and citation rollup. None of `normalize_title()`'s heuristics
  (acronym-prefix stripping, plural-`s` collapsing, LaTeX and superscript folding)
  catches a swapped leading qualifier ("Deep" vs "End-to-end Learned"), so it got
  its own hand-verified entry in `KNOWN_DUPLICATE_TITLES` in `merge_corpus.py`.
  That follows the same "exact pair, not a broader pattern" precedent as
  `GLUED_INSTITUTION_SPLITS` and `INSTITUTION_ALIASES` in `aggregate.py`. We
  deliberately didn't add a general "strip any leading adjective" rule, because
  it could merge different papers that share a generic remainder.

## Per-venue scripts (current, in use)

| Venue | Script | Source | Notes |
|---|---|---|---|
| CVPR / ICCV / WACV | `fetch_cvf_history.py` (uses `fetch_cvf.py`) | openaccess.thecvf.com | Full title, authors and abstract, but WACV only from 2020 (CVF has no earlier editions). CVPR 2018-2020 and WACV 2013-2019 fall back to `fetch_dblp_listing.py` (title and authors only). For CVPR, that's because CVF's `?day=all` listing returns a 500 ("Error 1525: Incorrect DATE value") for those three years. |
| ECCV | `fetch_ecva_history.py` | ecva.net | Only 2018, 2020, 2022 and 2024 exist there (a biennial mirror). We confirmed on its own listing page that there are no earlier years, so a different fetch script can't help. |
| NeurIPS | `fetch_neurips_history.py` (uses `fetch_neurips.py`) | proceedings.neurips.cc | The listing link format changed around 2020 (`-Abstract.html` vs `-Abstract-Conference.html`). One regex matches both. |
| ECCV 2026 | `fetch_virtual_site.py` | eccv.ecva.net virtual-site JSON | Titles and authors only, until ecva.net adds a 2026 section (then switch to `fetch_ecva_history.py`). The poster pages have abstracts, but with the spaces at line breaks missing, so they aren't used. |
| CoRL | `fetch_corl_history.py` (uses `fetch_pmlr.py`) | proceedings.mlr.press | The PMLR volume number for each year is hardcoded in `PMLR_VOLUMES` in `fetch_pmlr.py`, because it can't be derived from the year. Existing years are skipped unless `--force` is given. Before September 2026 the listing parser skipped hyphenated slugs, which gave 14 papers in 2017-2024 the next paper's abstract and dropped 14 others; those years were refetched. |
| ICML 2025 | `fetch_pmlr.py ICML 2025` | proceedings.mlr.press (v267) | Title, authors and abstract, including the position paper track. |
| ICLR 2026 / ICML 2026 | `fetch_virtual_site.py --abstracts` | iclr.cc / icml.cc virtual-site JSON, plus one poster page per paper for the abstract | Main track only (and ICML's position papers). Blog posts and TMLR/JMLR presentations are skipped. See DECISIONS.md. The first pull (September 2026) stopped after about 800 poster pages per venue, at roughly 2.5 s a page; `--abstracts --force` picks up the rest (about 4,600 ICLR and 5,800 ICML pages). |
| ICRA / IROS | `fetch_github_paper_lists.py` | Community-maintained GitHub paper lists | See "ICRA and IROS" below. |
| RSS / ICLR (to 2025) / AAAI | `fetch_dblp_listing.py` | DBLP | Title, authors and year only, no abstracts. DBLP is the primary source here, not a fallback: RSS's own site has no reliable volume-to-year mapping, ICLR's OpenReview bulk API now needs a browser-solvable challenge (`403 ChallengeRequiredError`), and AAAI's ojs.aaai.org archive page is JavaScript-rendered. DBLP has a stable URL per year (`dblp.org/db/conf/<key>/<key><year>.html`). |
| ICML (to 2024) / BMVC / ACCV / ICPR / ICASSP / ICIP | `fetch_dblp_listing.py` | DBLP | See "ICML, BMVC, ACCV, ICPR, ICASSP and ICIP" below. Blocked, see above. |
| ITSC / IV | `fetch_dblp_listing.py` | DBLP | Fully fetched. Watch out for the `iv` mix-up described under "IV" below. |
| T-ITS (IEEE Trans. on Intelligent Transportation Systems) | `fetch_dblp_listing.py --journal` | DBLP | Fully fetched, using the same `--journal` volume-walking path as TOG. |
| TOG (ACM Trans. on Graphics) | `fetch_dblp_listing.py --journal` | DBLP | Found the same way as ICML and the others below. Not fetched yet, and blocked (see above). |

**ICRA and IROS.** IEEE Xplore returns HTTP 418 to any direct request. OpenAlex
(`fetch_ieee_openalex.py`) is still the source of title, authors and abstract for
ICRA 2022 and IROS 2021-2022, but it has moved to a paid, budget-limited API
(confirmed 2026-08-17: `"Insufficient budget"`, `dailyRemainingUsd: 0`), and the
project's no-paid-APIs rule means it isn't used to widen coverage. Everything else
comes from `hrjp/ICRA-IROS-PaperList`, an index of per-year community repos (title
and authors only, no abstracts) covering 2019-2025 for both conferences. The repos
use three different formats:

- plain bullets under `## Category` headings
- a markdown table with comma-separated authors
- a markdown table with semicolon-separated `Last, First` authors

We read the raw file for each family instead of assuming one family's format from
another, because that assumption caused real data corruption during development
(see the script's docstring and `tests/test_fetch_github_paper_lists.py`). Every
paper carries a `source_url` (the exact repo it came from) through
`merge_corpus.py` onto `papers_full.json`, and `paper.html` shows it. 2013-2018 and
2026 have no known source.

**ICML, BMVC, ACCV, ICPR, ICASSP and ICIP.** These were found by mining
`data/reference_lists_cvf.json` and `data/reference_lists_arxiv.json` (raw
reference text from the corpus's own papers) for parenthetical venue codes that
are cited often but not yet covered (a user request). ICML alone had 207 raw
citations. They use the generic DBLP path. ACCV and ICPR are multi-part on DBLP
(`accv2024-1.html` ... `-N.html`), which the existing ECCV-style pagination fallback
in `fetch_year()` already handles. ICML and BMVC are fully fetched (2012-2024),
ACCV reached 2012-2018 (2020, 2022 and 2024 are missing), and ICPR, ICASSP and
ICIP haven't started. One lasting fix came out of this: DBLP began dropping
connections mid-fetch (`RemoteDisconnected`) and lost an in-progress 15-year ICML
fetch on year 14, so `fetch_common.py`'s `fetch()` now retries on a bare
`ConnectionError` as well as `HTTPError`.

**IV.** DBLP's `conf/iv/` is not the IEEE Intelligent Vehicles Symposium. It's the
unrelated International Conference on Information Visualisation (its page is
entirely treemap, word-cloud and graph-layout papers). DBLP files the vehicles
symposium under `conf/ivs/`. Using `iv` pulled 1,131 Information Visualisation
papers under the "IV" label, and the real symposium's papers were never fetched.
A user spotted it ("IV has only 0.4% AV-relevant papers... it is literally called
Intelligent Vehicles"). It's fixed by mapping "IV" to `ivs` in `CONF_DBLP_PATH` in
`fetch_dblp_listing.py`.

## Merge, classify, enrich, aggregate

- **`merge_corpus.py`** removes duplicates by normalized title across every
  `data/venues/*.json` file, gives each paper a `category` (topic) and an
  `av_relevance` (AV or non-AV) using `classify.py`, and writes
  `data/papers_full.json`.
- **`classify.py`** assigns categories by keyword matching (`data/categories.json`,
  a living taxonomy, not a fixed one). AV relevance comes from an explicit list of
  AV-specific phrases (`AV_RELEVANCE_TERMS`) and is independent of category. The
  category keywords are generic computer vision and robotics terms that also match
  plenty of non-AV papers, so category membership was never a valid relevance
  signal.
- **`enrich_av_authors.py`.** The venue listings only ever gave a plain
  author-name string, no affiliations. This backfills OpenAlex author,
  institution and country data for every AV paper (`av_relevance == "AV"`), not
  the whole corpus, since there's no reason to spend OpenAlex's rate budget on
  papers that would never be ranked.
- **`fetch_affiliations_arxiv.py`** is a second, independent source of
  `authors_detail`, for AV papers OpenAlex hasn't reached. It finds each paper's
  arXiv ID through arXiv's search API, then parses ar5iv's full-text HTML for each
  author's institution. There's no rate limit like OpenAlex's, but it only covers
  papers with an arXiv preprint that has affiliations in its LaTeX source. ar5iv
  gives institution names, not country codes, so `data/institution_countries.json`
  is a small hand-curated map (real web lookups, not guesses) from name to country
  code, applied by `apply_affiliations_arxiv.py`. An affiliation missing from the
  map still appears on the Institutions page but adds no country until someone
  adds it. It writes its own side file (`data/affiliations_arxiv.json`) to avoid
  races, like the citation backfills below. `apply_affiliations_arxiv.py` is the
  only writer onto `papers_full.json`, and it only touches papers OpenAlex hasn't
  already enriched (OpenAlex's data is richer and is never overwritten).
- **`fetch_cvf_affiliations.py`** is a third free source, for CVF-hosted AV papers
  (CVPR, ICCV, WACV) that still have no `authors_detail` after the other two. It
  extracts institution-like phrases from page 1 of the paper's own CVF PDF. There's
  no per-author linkage, so every known author of the paper is credited with the
  full set found. That's coarser than the other two sources but still correct at
  the paper level. It writes `data/affiliations_cvf.json`, and
  `apply_cvf_affiliations.py` is the only writer onto `papers_full.json`, stamping
  `authors_detail_source="cvf-pdf"`.
- **`mine_abstracts.py`** backfills missing abstracts from arXiv, for AV papers
  whose venue only ever carried title and authors (DBLP-sourced venues never had
  abstracts; see DECISIONS.md, "DBLP-sourced venues have no abstracts"). It looks
  up the arXiv ID directly where `fetch_arxiv_links.py` already found one, and
  otherwise searches by title, with the same exact-match standard as
  `fetch_affiliations_arxiv.py`. A clean search with no match is recorded as
  exhausted and not retried. It writes `data/abstracts_arxiv.json`, and
  `apply_abstracts_arxiv.py` is the only writer onto the `abstract` and
  `abstract_search_exhausted` fields of `papers_full.json`. It never overwrites a
  real abstract from a richer source.
- **`fetch_abstracts_semanticscholar.py`** is a second source for the same gap,
  including the majority that `mine_abstracts.py` confirms has no arXiv preprint at
  all (T-ITS, ITSC and IV papers are often applied transportation engineering, not
  the arXiv-heavy ML crowd). Semantic Scholar indexes published venue metadata, not
  just preprints. One `/paper/search/match?fields=title,abstract` call per paper
  returns the title and abstract together, and it hit about 78% of the DBLP-sourced
  backlog when confirmed live. It uses the same API key and rate limit as
  `fetch_semanticscholar_citing.py`. It writes
  `data/abstracts_semanticscholar.json`, and `apply_abstracts_semanticscholar.py` is
  the only writer, with the same never-overwrite-a-real-abstract rule.
- **`fetch_llm_category_labels.py`** is for title-only papers still in "misc" after
  both abstract backfills (an abstract that doesn't exist anywhere can't be mined)
  and after every keyword path in `classify.py`. A local LLM makes a best guess at
  each title's category. It is the weakest signal `classify_paper()` consults, and
  is only reached when nothing else matched. `category: null` (the model's own
  "none of these fit") is a real answer and is deliberately not overridden. The
  script reads the category list from `categories.json` at run time, so it can't
  drift from the taxonomy.
- **`aggregate.py`** reads `data/papers_full.json` and writes `data/stats.json`,
  which is what the UI reads. Leaderboards rank AV papers only. It also writes
  `data/abstracts/shard-NN.json`, abstracts sharded out of `stats.json` so only
  `paper.html` pays for them (see DECISIONS.md).

## Citation counts

The site only ever shows one citation count per paper: `in_corpus`, the number of
other papers in this corpus that cite it (see the Methodology section and
DECISIONS.md). `papers_full.json` stores counts in `citations_by_source`
(`{"in_corpus": {"count": N, "updated": "..."}, ...}`). `citation_count()` in
`aggregate.py` reads only `citations_by_source.in_corpus`, with no fallback to any
external provider, for ranking or for display.

No script writes an OpenAlex, Semantic Scholar or Scholar citation count into
`papers_full.json`. `fetch_ieee_openalex.py` (the only source for ICRA and IROS)
fetches title, authors and abstract from OpenAlex but not its `cited_by_count`.

That used to be an exception. ICRA and IROS papers had a flat `citations` field
that `citation_count()` migrated into `citations_by_source.openalex` as a legacy
fallback. A paper could then rank in the top 50 by an external count while showing
0 citations on the page (which only shows in-corpus counts), a real user-reported
bug. It was removed at the source so it can't come back: the field is stripped
from `papers_full.json` and no longer fetched.

How papers in the corpus cite each other is a different signal from a global
count. Two raw-reference sources feed one matching step:

- `build_citation_graph.py` downloads the PDF of each AV CVPR, ICCV and WACV paper
  (CVF-hosted, no rate limit) and extracts the references section with pdfplumber.
  The text is noisy and depends on PDF layout.
- `fetch_affiliations_arxiv.py` (see above) already fetches ar5iv's full-text HTML
  for affiliations, so it also extracts ar5iv's cleanly structured bibliography
  (one `<li class="ltx_bibitem">` per entry) at no extra network cost. That covers
  any venue with an arXiv preprint, not just CVF.

Both write their raw, unmatched reference lists to their own files
(`data/reference_lists_cvf.json`, `data/reference_lists_arxiv.json`), because
fetching and matching are deliberately separate phases. The matching phase of
`build_citation_graph.py` runs on every invocation, even if nothing new was
fetched, and rematches every saved reference list against the current corpus. A
reference to a paper that wasn't indexed yet starts matching once that paper is
pulled, with no re-fetching, just a rerun.

All of these write to their own side files, never directly to `papers_full.json`,
so they can run at the same time without racing over the same multi-MB file (see
their docstrings). `apply_citation_sources.py` is the single-writer merge step. It
folds `citations_openalex.json` and the matched edges from `citation_graph.json`
into `papers_full.json`. Run it after any of the backfills make progress (or
several in parallel) and before `aggregate.py`.

## Known data-quality caveats

The About page in the UI covers these too.

- OpenAlex's author-to-institution linking is occasionally wrong in its own data,
  independent of anything here. For example, "InternetLab" (a Shanghai AI Lab
  collaboration hub) is tagged country `BR` because OpenAlex conflated it with an
  unrelated Brazilian NGO of a similar name. This pipeline can't fix that without
  per-institution manual overrides.
- CVPR 2018-2020 have no abstracts (DBLP fallback, see above).
- ICRA and IROS coverage is sparse as a deliberate tradeoff, not a bug (see the
  table above).

## Scripts removed as superseded

Their functionality is fully covered by the scripts above.

- `fetch_cvf_listing.py` was a listing-only CVF pull without abstracts.
  `fetch_cvf_history.py` replaced it once abstracts were needed for every year.
- `fetch_pmlr_listing.py` was a listing-only CoRL pull. `fetch_corl_history.py`
  replaced it.
- `fetch_neurips_full.py` was a single-year NeurIPS runner. `fetch_neurips_history.py`
  with the same start and end year does the same thing.
- `fetch_ecva_listing.py` was its own listing-only ECCV pull.
  `fetch_ecva_history.py` parses the listing itself and doesn't import it (its
  docstring used to say otherwise, left over from an early version; the import was
  already unused).
- `fetch_abstracts.py` was a one-off patch that backfilled abstracts into an
  already-collected `enriched.json` from before `enrich.py` captured them.
- `data/seeds.json` → `data/raw/*.json` → `enrich.py` → `data/enriched.json` was
  the original citation-crawl pilot, a different and non-uniform sampling method.
  It was excluded from `papers_full.json` for a while, then deleted outright,
  since nothing read it once excluded.
