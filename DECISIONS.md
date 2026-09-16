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

`classify.py` decides AV vs. non-AV from title + abstract text, in layers
that each push only one way:

1. Hard scope filters (mechanical/hardware-only papers, non-road platforms like
   aerial/underwater/legged robots) → non-AV.
2. Keyword floor: an AV-specific phrase anywhere, or a standalone driving word
   in the title → AV. A floor later layers can add to but never override.
3. A small linear model (`data/relevance_model.json`: per-phrase weights plus a
   threshold, trained on hand labels) → AV, only for papers the keyword
   floor missed.
4. A local-LLM "it's AV" verdict, folded in as a promotion-only signal.

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

## Misc vs uncategorized

`classify_paper()` (`classify.py`) used to leave every paper that matched no
category keyword as `"uncategorized"`, whether or not it was AV-relevant —
`categories.json`'s own `_readme` called this out as deliberate ("a signal the
taxonomy needs a new category, not that the paper doesn't have one"). In
practice this meant 3,647 of 25,664 AV papers (14.2%) sat in `"uncategorized"`,
which `categories.html` hides from the ranked table and shows once as a
footnote instead — a large, permanently-growing slice of the AV corpus with no
real visibility (user-requested: "make sure very few papers are uncategorized
... discern uncategorized papers from misc papers").

The fix splits the same fallback in two by `av_relevance`, computed right
before the split so both values are in hand: an AV paper falls to `"misc"`
(it already matched an explicit AV-relevance phrase, so it's confirmed
on-topic — not fitting a specific category doesn't make it any less real, so
it gets a normal, ranked, browsable bucket like any of the other 30) while a
non-AV paper still falls to `"uncategorized"` (relevance itself is the
weaker signal there, and non-AV papers are never shown in the ranked UI
anyway, so tracking a missing category for them isn't useful the same way).
This is a pure post-processing split of the existing fallback, not a new
matching path — nothing about how a category keyword match is scored
changed. Confirmed on real data: after this change, AV-side `"uncategorized"`
is exactly 0 (all of it moved to `"misc"`), and `"misc"` never appears on a
non-AV paper.

`"misc"` is intentionally NOT listed in `categories.json`'s `categories`
array — it isn't keyword-matched, so it can't be extended by adding keywords
the way a real category can, and listing it there would invite exactly that.
Alongside this split, ~215 papers that used to fall through were reclaimed
into real categories by extending several categories' keyword lists with
phrasings a direct audit of the (then-3,647-paper) uncategorized set showed
were common but unmatched — plurals ("traffic lights control" vs the
existing "traffic light control"), synonyms ("scene understanding" alongside
"segmentation"), and missing but unambiguous terms ("kalman filter",
"valet parking", "emergency braking", "ramp merging", "lane change",
"temporal logic", "driver activity"/"activity recognition"). `"misc"` at
13.4% of AV papers was, at that point, the single largest bucket, larger than
any one real category — a sign the taxonomy still had room to grow, not that
the job was finished (see the next two entries for what came out of actually
growing it, which brought `"misc"` down to 12.8%).

## Surveys/reviews are a paper-type gate, same tier as Datasets

A direct audit of `"misc"` (spot-checking `index.html?category=misc`, sorted
by citations to prioritize what's most visible) found "A Survey of X" among
its most-cited entries and, checked corpus-wide, 541 AV papers with
`\bsurvey\b|\breview\b` in the title — already scattered across every one of
the other 30 categories by keyword luck, the exact "topic keywords describe
what a paper covers, not what it IS" problem `dataset-benchmark-paper`
already exists to solve for dataset papers (see `presents_dataset()`).
`is_survey_or_review_paper()` (`classify.py`) is the same kind of title gate,
checked right after the dataset and radar gates (so a radar survey still
lands in `radar-perception` and a dataset survey still lands in
`dataset-benchmark-paper`, both by design) and before the topic-keyword
ranking. It's a bare-word check (`survey`/`review`, not a curated phrase
list) because auditing all 541 matches turned up only 2 false positives —
both the *other* sense of "survey" (a questionnaire, not a literature
review): "An Online Survey" and "...a driver activity survey" — both
explicitly excluded rather than guessed at generically. `"survey-review-paper"`
carries no real keywords of its own in `categories.json` (unlike
`dataset-benchmark-paper`, which also catches a weaker abstract-only
mention) — not worth the added complexity for a paper type this reliably
title-detectable.

## Explainability had to be a last-resort category, not a normal one

Added alongside Surveys/Reviews (same audit, same user request) to cover the
"Interpretable X" / "Explainable X" cluster in `"misc"` — but a first attempt
adding it as a normal competing category (like all the other real ones)
immediately reproduced, in a new form, exactly the bare-word problem
`categories.json`'s own `_readme` warns about for keyword *phrases*: 193
papers landed in `"explainability"`, but most of them weren't from
`"misc"` at all -- they were pulled OUT of a more specific, already-correct
category. Confirmed on real data: "Hint-AD: Holistically Aligned
Interpretability in End-to-End Autonomous Driving" (an end-to-end-driving
paper) and "Interpretable Self-Aware Neural Networks for Robust Trajectory
Prediction" (a motion-prediction paper) both lost their specific category,
because an XAI-flavored paper's abstract repeats "interpretable"/"explainable"
several times as a matter of course, and `score_category()`'s raw occurrence
count has no defense against a short, frequently-repeated word outscoring a
topic phrase that only appears once or twice. A multi-word phrase (the
project's usual mitigation) doesn't fix this one, because the concept
genuinely doesn't have a longer, more specific phrasing to prefer.

Fixed by giving `explainability` a different PRIORITY, not different
keywords: `LAST_RESORT_CATEGORY_IDS` (`classify.py`) pulls it out of the
normal ranking pass entirely and only ranks it in a second pass, run only
when the first pass matched nothing at all. This isn't a scoring tiebreak —
a last-resort category is never even evaluated against a paper that a
normal category already claims, so no repeat count can ever let it win one
away. Dropped the count from 193 to 57 (all previously-miscategorized
papers listed above went back to their original correct category), which is
much closer to what a category meant to catch only the "misc" residue
should look like. Keywords stay in `categories.json` as the single source of
truth; only the ranking-pass membership is hardcoded, in one small
`frozenset` in `classify.py`.

## An LLM category guess is the last thing consulted, weaker than any keyword

`fetch_llm_category_labels.py` handles the residue neither keyword matching
nor a real abstract can reach: title-only papers (DBLP-sourced venues never
had abstracts — see "DBLP-sourced venues have no abstracts") still stuck in
`"misc"` after every keyword-based path, including the last-resort tier
above, has had its turn. `classify_paper()` only ever consults it when
`category` is still `"uncategorized"` at that point — a single local
model's single-title guess must never outrank a real keyword match the way
`explainability`'s bare words accidentally did (see above), so this isn't
folded into the ranking at all, just checked as the very last fallback
before the misc/uncategorized split.

Spot-checking the first batch found real value ("A Statistical GPS Error
Model for Autonomous Driving" → `mapping-localization`, "Real-Time
Prediction of Multi-Class Lane-Changing Intentions" → `motion-prediction`)
alongside real imprecision ("Traffic-Responsive Control Technique for
Fully-Actuated Coordinated Signal..." → `control`, when the paper is about
traffic-SIGNAL control and belongs in `traffic-flow-management` — the
model sees only each category's `{id, label}` pair, not enough to
disambiguate "control" the vehicle-dynamics sense from "control" the
traffic-signal sense every time). Accepted as a known, bounded tradeoff:
this tier only ever touches papers that had zero topic signal at all
before it ran, so a right-ish-but-imprecise guess is still a net
improvement over an unbroken "misc", and it's the weakest, most clearly
provisional signal in the whole classification stack — never promoted
above a real keyword match, and revisit the prompt's category descriptions
if a specific confusion like this one turns out to be common rather than
one-off.

`category: null` (the model's own "none of these fit") is written to
`data/category_labels_llm.json` and deliberately excluded from what
`classify.py` reads back — a real, useful answer (don't force a category
that doesn't exist), but one that must resolve to the same "misc" outcome
as a paper this pass hasn't looked at yet, not something worth
distinguishing at the classification layer.

## Object Detection and Mapping & Localization split by sensor/task, not merged

Both were the two largest categories by a wide margin (2,020 and 2,062
papers respectively) — user-requested: split them for a more even
distribution, the same way Datasets/Surveys/Explainability already carve
distinct concerns out of a crowded taxonomy rather than growing it flatter.

`object-detection` → `object-detection-2d` / `object-detection-3d`, split
on sensor modality: bare "2D"/"3D" mentions turned out to be nearly useless
as a keyword signal (of 2,020 papers, only 35 said "2D" explicitly — it was
historically the unmarked default, so nobody writes it), but LiDAR/point-
cloud/BEV/voxel language is a reliable proxy: 3D-modality papers
overwhelmingly also say "3D" explicitly (confirmed on real data — "3D
Object Detection", "LiDAR 3D Vehicle Detection", etc.), and 2D/camera-based
ones use the shared generic detection vocabulary without it. Result: 1,282
/ 1,132, about as even as this kind of split gets. `radar-perception`'s
existing gate (checked before all normal categories) still claims
radar-based detection first, so "radar" was deliberately left out of the
3D keyword list — including it would never fire on anything that gate
hasn't already taken.

`mapping-localization` → `mapping` / `localization`: a cleaner conceptual
line (HD maps/lane info vs. SLAM/odometry/pose) than a genuinely even one —
605 / 1,361 on real data, localization being the naturally larger,
more heavily-researched half. Kept anyway: half the size of one bucket
beats a single one, and the two are legitimately different reader
interests (someone hunting for HD-map papers doesn't want the SLAM
literature mixed in).

## The 2D/3D object-detection split needed its own tie-break, not a global one

The split above created a structural collision the original single category
never had: `object-detection-2d`'s bare `'object detection'` keyword is a
literal substring of every one of `object-detection-3d`'s specific phrases
(`'3d object detection'`, `'lidar object detection'`, `'point cloud object
detection'`, ...). A genuinely 3D paper's title therefore always ties 2D on
`rank()`'s title-score check (both match), and then whichever OTHER,
dimensionality-unrelated 2D keyword ("detector", "bounding box") also
happens to appear in the abstract wins the combined-score tiebreak for 2D
regardless. Confirmed on real data: "Center-Based 3D Object Detection and
Tracking" (CenterPoint), DETR3D, Voxel R-CNN, PolarFormer, HDNET, and ~385
more all landed in `object-detection-2d` this way despite an explicit "3D
Object Detection" in their own title.

`rank()`'s own docstring already describes the intended tiebreak order as
title match, then longest matching keyword, then id — but the actual tuple
checks combined score before longest-match, so the doc and the code
disagreed. Tried the literal fix (reorder the tuple so max_len outranks
combined_score globally) and measured its blast radius before trusting it:
6,878 of 235,288 papers (~3%) would reclassify, across dozens of unrelated
category pairs (`llm-vlm-driving` vs `reinforcement-learning`, `segmentation`
vs `general-cv-ml-method`, ...) with no way to review that many changes
against real judgment. Rejected as far too broad for what only one category
pair actually needed.

Fixed with a narrow, explicit override instead, right after the normal
ranking loop: if a paper lands on `object-detection-2d` AND
`object-detection-3d` matched the title at least as well AND 3D's longest
matching keyword is more specific (longer) than 2D's, use 3D. Scoped to
exactly the ~388 papers where this exact ambiguity exists (spot-checked: all
genuinely 3D by title), leaving `rank()`'s general tiebreak — and every
other category pair — untouched.

## `load_llm_category_labels()` must re-validate against the live taxonomy

The split above orphaned `data/category_labels_llm.json`: 65 entries still
said `object-detection`, 96 still said `mapping-localization`, both ids that
no longer exist in `categories.json`. `load_llm_category_labels()` had no
reason to distrust its own file, so `classify_paper()` applied a dead id
verbatim wherever it was the last fallback — surfacing on the live site as a
literal "Mapping Localization (40)" / "Object Detection (33)" row in every
page's category filter and in the Categories ranking table and Insights'
category-correlation matrix (`categoryLabel()`'s fallback title-cases
whatever id it doesn't recognize, rather than hiding it).

Fixed by passing the current set of valid ids (built once in
`merge_corpus.py`'s `main()`, from the same `taxonomy` list already loaded
for keyword ranking) into `load_llm_category_labels()`, which now drops any
entry whose `category` isn't in that set — the same "fail safe back to
misc/uncategorized" behavior an absent or `null` entry already gets, not a
one-time cleanup of the JSON file. This has to hold for every future
category split or rename too: the LLM-label file is long-lived cached
output from an expensive crawl, re-running it isn't a data-quality fix on
its own, and nothing else in the pipeline re-checks its contents against the
taxonomy that produced it.

## Derived data is not tracked; `gh-pages` is a single squashed commit

`data/stats.json`, `data/stats_non_av.json`, and the abstract shards are
gitignored — cheaply regenerable from `data/papers_full.json` by `aggregate.py`
alone (well under a minute), and tracking a leaderboard dump bloats every diff
with numbers that change on every corpus update and aren't reviewable anyway.
`deploy.py` force-pushes `gh-pages` as one orphan commit each time rather than
committing on its history: the branch is 100% generated output, and appending
multi-MB non-delta snapshots would grow the repo forever.

**`data/papers_full.json` and `data/citation_graph.json` are a different
case, and "fully regenerable" used to overstate what's actually true of
them.** `merge_corpus.py` only *classifies* papers from scratch correctly
(that part really is fully regenerable, from the tracked `data/venues/*.json`
+ `classify.py`); it carries author affiliations, `citations_by_source`,
abstracts and arXiv links forward from whatever `papers_full.json` already
exists, rather than re-deriving them. On a checkout with no prior
`papers_full.json` — confirmed directly, not assumed: a fresh clone rebuilt
this way produces the right paper list and classification but zero author,
institution, or country data — recovering that enrichment for real means
re-running the CVF/arXiv affiliation scrapers and the Semantic
Scholar/ORCID lookups, which is exactly the "weeks of crawling plus API
keys" `build_data_release.py`'s own docstring describes elsewhere.
`citation_graph.json` is a second, separate gap: `aggregate.py` reads its
`edges` directly for the disruption index and the citation-graph coverage
table, and losing that file alone (even with `papers_full.json` intact)
silently blanks those features on the next rebuild — nothing in
`papers_full.json`'s carry-forward list protects it. Neither file is
tracked in git (see the size/diff reasoning above), so right now each one
has exactly one live copy and no backup: whichever machine holds the only
`papers_full.json` with real enrichment in it is a single point of
failure. The fix is to snapshot both files somewhere durable after every
deploy, not to keep believing a fresh rebuild reproduces them.

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

## Finding the backup release by tag needs a list-and-filter, not the tags endpoint

`backup_corpus.py` / `restore_corpus.py` look up the `corpus-backup` release by
tag using `GET /repos/.../releases` and filtering client-side, not
`GET /repos/.../releases/tags/corpus-backup` (the endpoint that looks like the
right one). GitHub's "get release by tag" only resolves *published* releases —
a **draft** release has no real git tag ref, so that endpoint 404s even when a
draft with that tag_name exists. Confirmed on the live repo: with the old
tags-endpoint lookup, `get_or_create_release` always 404'd and fell through to
"create new release", so every `backup_corpus.py` run over this project's
history silently created a fresh draft instead of reusing the existing one —
4 duplicate `corpus-backup` drafts had piled up on GitHub, directly
contradicting this module's own "one fixed tag, one asset, always replaced"
docstring claim. `restore_corpus.py` had the same tags-endpoint lookup with
no fallback at all, so it was flatly broken -- every restore attempt raised
an uncaught `HTTPError: 404` instead of downloading anything, confirmed
live. Fixed by listing all releases and matching on `tag_name`
client-side; `backup_corpus.py` also now deletes any stale duplicates it
finds beyond the most recent, so a repo affected by the old bug self-heals
on its next backup rather than needing manual cleanup.

## Every crawler processes its queue most-cited-first, not corpus order or random

`fetch_common.by_citations()` (by `citations_by_source.in_corpus.count`, NOT
the top-level `citations` field -- confirmed dead, 0 of 25,639 AV papers have
it set, including nuScenes) is now the shared ordering for every incremental
crawler's pending queue: `mine_abstracts.py`, `fetch_abstracts_
semanticscholar.py`, `enrich_av_authors.py` (already had its own copy of this,
now consolidated), `fetch_affiliations_arxiv.py` (ditto), `fetch_arxiv_links.py`,
`fetch_cvf_affiliations.py`, `build_citation_graph.py`, `fetch_s2_author_ids.py`,
`fetch_semanticscholar_citing.py`, `fetch_llm_category_labels.py`, and
`classify_code_links_llm.py` (previously alphabetical by normalized title, a
tuple-sort accident). User-requested: with every external source in this
pipeline rate-limited or budget-capped (see PIPELINE.md), a run that gets cut
off partway through should already have enriched the papers readers actually
encounter -- top-cited lists, comparison pages, leaderboards -- not whichever
paper happened to load first from its source venue file.

Two crawlers were explicitly asked about and kept the OLD behavior anyway,
by user choice, not oversight:
- `fetch_s2_author_ids.py` -- an earlier "sort by author-count" ordering was
  reverted for skewing ORCID/S2-ID coverage toward big-collaboration papers;
  citation count risks the same skew (a highly-cited paper often has a large
  author list too). Switched to most-cited-first anyway, user-confirmed,
  accepting that reopened trade-off.
- `fetch_semanticscholar_citing.py` -- this one previously WAS citation-sorted,
  then was reverted after a confirmed, self-defeating bug: a paper (or whole
  venue) with zero in-corpus citations *because it's never been queried yet*
  always sorts last under that rule, so it can never earn a citation to rise
  in priority -- confirmed stuck this way ("IV and arXiv still have no
  citations") after a run well over a third of the way through the corpus.
  Switched back anyway, user-confirmed, accepting that long-tail venues may
  go unqueried again.

Deliberately NOT switched, and shouldn't be: `select_labeling_candidates.py`,
`select_near_threshold_candidates.py`, `train_relevance_classifier.py`,
`fetch_llm_relevance_labels.py`/`_v2.py`, and `audit_code_links_llm.py` --
these all feed or evaluate the relevance classifier and need an unbiased
random draw, not a priority order; citation-sorting them would systematically
skew training/eval data toward older, more established, already-popular
papers.

## A raw DBLP-suffixed author name in papers_full.json is not a live bug

Investigating the top of the most-cited-authors list surfaced names like
"Andreas Geiger 0001" in `papers_full.json`'s raw `authors` field (a
DBLP-assigned disambiguation suffix, present on 2,377 distinct base names /
several thousand raw occurrences across the corpus). Before writing a fix,
checked the actual output this feeds: `aggregate.py`'s `clean_author_name()`
already strips a trailing `\s+\d{4}` unconditionally (`DBLP_DISAMBIG_SUFFIX_
RE`), confirmed on `stats.json` -- "Andreas Geiger 0001" appears nowhere in
`all_papers[].authors`, and the real "Andreas Geiger" entry already carries
the correct, unified 7,098-citation/56-paper total. No fragmentation reaches
the live site. Recorded here specifically so this doesn't get "fixed" again
from the same starting point -- this is the second time this session a raw-
`papers_full.json` diagnostic almost drove a change that the processed
output already handled (see the institution-alias audit above); the lesson
holds: check `stats.json`/`stats_detail.json` (what the site actually reads)
before treating a `papers_full.json` string as evidence of a live bug.

## `flag_ambiguous_authors.py`'s heuristics false-positive on prolific lab researchers

Checked its 4 unconfirmed "review"-tier candidates from the current top-60-
by-citations against Google Scholar directly (Hongyang Li, Long Chen, Hang
Zhao, Zheng Zhu) rather than leaving them in the queue. All four are real,
single, extremely prolific researchers in the fast-moving, highly
collaborative modern AV/world-model research community (OpenDriveLab/
Shanghai AI Lab-adjacent): Hang Zhao's papers consistently share one email
(hangzhao@mail.tsinghua.edu.cn); Hongyang Li and Long Chen recur as each
other's co-authors across a large, coherent, recent (2023-2026) end-to-end-
driving publication cluster; Zheng Zhu's papers form one coherent "driving
world models" research thread. None marked ambiguous in
`scholar_profiles.json` -- doing so would incorrectly warn on a real
researcher's legitimate, unified profile.

This is a real, generalizable false-positive mode worth naming: the script's
structural signals (disjoint co-author clusters, papers/active-year,
category spread -- see its own docstring) were tuned against the classic
failure case (a common name silently blending 2-3 unrelated academics), not
against a newer pattern this corpus's most recent years are full of: one
person embedded in a large, fast-publishing lab, co-authoring across many
loosely-connected sub-teams and projects, which structurally looks exactly
like "several disjoint collaboration circles" even though it's one person.
A common surname plus high recent output should be weighed as a real prior
on "prolific lab researcher," not just "possible collision," when the
corpus's own co-author graph forms one connected, thematically coherent
recent cluster this consistently. Not changed here (needs care to avoid
under-flagging the opposite, real case); worth revisiting the scoring
itself if this pattern keeps showing up at the top of future review runs.

## Whole-corpus citations: the feature already existed, only the data didn't

User-requested: integrate "citations from the whole corpus, not just AV
papers" and make sure every place showing a citation number says clearly
which one it is. Before building anything, checked what already exists --
`paper.html` already shows exactly this, in two adjacent stat tiles: "Cited
by (all papers)" (`paper.citations`, i.e. `citations_by_source.in_corpus.
count`) and "Cited by (AV papers)" (`paper.citing_papers.length`, the
subset of those citers that are themselves AV papers), each with its own
explanatory tooltip. `apply_citation_sources.py`'s `in_corpus_counts()`
already counts every edge in `citation_graph.json` unconditionally, with no
AV filter -- confirmed on real data before touching anything: nuScenes
already showed 2,742 "all papers" vs 1,938 "AV papers", a real, live gap
this design was already built to represent.

What was actually missing was upstream of all that: `build_citation_graph.py`
only ever scanned `av_relevance=="AV"` papers' own reference lists (both its
own CVF-PDF fetch and `fetch_affiliations_arxiv.py`'s ar5iv-HTML side
channel), so `citation_graph.json`'s edges could only ever contain AV
citers -- "all papers" was structurally capped at "the AV slice" no matter
how the counting code was written. Fixed at the actual source: `main()`
now builds its CVF title pool from the whole corpus (34,352 more papers,
prioritized most-cited-first same as everywhere else -- see fetch_common.
by_citations), not just the AV subset. `citations_by_source.in_corpus` and
`citing_papers`/`paper.citations` keep their exact existing meaning and
code, unchanged -- they just get fed a more complete graph as this crawl
(now running) works through the backlog over the coming days.

A parallel `in_corpus_all`/`citations_all` field was drafted and then
reverted before shipping, once this was understood -- it would have
duplicated the existing `in_corpus`/`citing_papers` pair under new names
AND (worse) changed `in_corpus`'s own counting to exclude non-AV citers,
which is backwards from what "Cited by (all papers)" has always meant.
Recorded here so the same duplicate isn't built again from the same
starting confusion. The arXiv/ar5iv side (`fetch_affiliations_arxiv.py`,
~55,534 more non-AV papers with a known arxiv_url) is NOT yet widened the
same way -- its reference extraction is a side effect of its own
expensive per-paper LLM affiliation-extraction work, which non-AV papers
don't need at all, so widening it naively would waste that LLM cost on
~55k papers for a reference list alone. Left as a follow-up needing its
own leaner reference-only path, not done here.

## Manually-sourced abstracts for the top-cited gap

User-requested: look up missing information for the most-cited papers
specifically. Took the highest-in-corpus-citation AV papers still missing
an abstract (topped by KITTI's two seed papers, 1,901 and 1,336 citations)
and checked each one by hand against its own publisher page (IEEE Xplore,
ACM DL, CMU RI publications) via a real browser session, not a script --
every one of these had already been queried by `fetch_abstracts_
semanticscholar.py`'s own API call and come back with no abstract on file
there either, so this wasn't a case of "the crawler hasn't reached it yet."
13 verbatim abstracts recovered this way and applied directly to
`papers_full.json`, stamped `abstract_source: "manual-publisher-page"` so
this batch's provenance stays distinguishable from every automated source.

Confirms the same finding as the IEEE Xplore entry above (a real browser
renders the abstract even though scripted access gets `418`'d) applied at
small, human-in-the-loop scale rather than automated: this is a one-time,
bounded (13-paper) patch, not a crawler, and won't be repeated as one --
see that entry for why turning this into an automated scraper stays off
the table regardless of how well it would work technically.

## In-corpus citation counts vs. real-world (Google Scholar) counts

User-requested: estimate how accurate this corpus's own citation counts
are against Google Scholar. Sampled 15 AV papers spanning three orders of
magnitude (nuScenes at 2,742 in-corpus down to a 5-citation T-ITS paper),
looked each one up on Google Scholar by hand, and compared:

| Paper | In-corpus | Scholar | Ratio |
|---|---:|---:|---:|
| nuScenes (2020) | 2,742 | 12,016 | 22.8% |
| KITTI benchmark suite (2012) | 1,901 | 21,670 | 8.8% |
| CARLA (2017) | 1,808 | 10,615 | 17.0% |
| KITTI dataset, IJRR (2013) | 1,336 | 14,092 | 9.5% |
| Waymo Open Dataset (2020) | 1,250 | 5,952 | 21.0% |
| CenterPoint (2021) | 742 | 3,295 | 22.5% |
| MV3D (2017) | 476 | 4,797 | 9.9% |
| TransFusion (2022) | 283 | 1,532 | 18.5% |
| 3D Object Proposals (2015) | 204 | 1,595 | 12.8% |
| IntentNet (2018) | 127 | 589 | 21.6% |
| SphereFormer (2023) | 71 | 383 | 18.5% |
| SuperDepth (2019) | 38 | 290 | 13.1% |
| Synthetic-data segmentation (2019) | 20 | 350 | 5.7% |
| Declarative metamorphic testing (2022) | 10 | 79 | 12.7% |
| Trajectory prediction, T-ITS (2022) | 5 | 120 | 4.2% |

This corpus's own count captures roughly **5-23%** of a paper's true
citation count -- never close to complete (by design: it only counts
citations from papers whose reference list this pipeline has actually
scanned, not all of academic literature), but not wildly inconsistent
either. Two real patterns, not just noise:

- **How AV-core vs. adjacent-field a paper is matters more than its raw
  citation count.** The two lowest ratios (4.2%, 5.7%) are a T-ITS traffic-
  engineering paper and a domain-adaptation/segmentation paper -- both cited
  heavily by fields this corpus's ~20 tracked venues barely touch (general
  traffic engineering, general semantic segmentation). The KITTI papers
  themselves score surprisingly low (8.8%, 9.5%) for the same reason despite
  being foundational AV datasets: an enormous share of their real citers are
  generic computer-vision/robotics papers with nothing to do with
  autonomous driving specifically, sitting outside this corpus's scope by
  construction, not by a gap in coverage.
- **No strong bias by citation-count tier itself** -- a highly-cited paper
  (nuScenes, 22.8%) and a modestly-cited one (IntentNet, 21.6%) can land at
  nearly the same ratio; the venue/topic-fit pattern above dominates over
  sheer popularity.

Not a reason to change how citations are computed or labeled -- the "Cited
by (all papers)"/"Cited by (AV papers)" pair on paper.html already states
plainly that both numbers are corpus-internal, never an external database
(see the entry below). This is a sanity check on that existing honesty, not
a finding that anything needs fixing: a reader who wants a truer
"how-cited-is-this-in-the-world" number already has an explicit, correct
signal (the tooltip) that this isn't it.

## `build_citation_graph.py`'s PDF text extraction: pdfplumber -> PyMuPDF, plus a local PDF archive

The overnight widened citation-graph crawl (see the entry above) was running
at ~23 sec/paper -- confirmed for real, not estimated, by cross-checking
`data/reference_lists_cvf.json`'s growth against wall-clock time over a
~21-hour run (only 3,200 of 32,686 CVF papers done; the laptop having slept
part of that night inflated the wall-clock total further, but didn't explain
the per-paper rate itself). Root cause: `fetch_pdf_text()`'s own docstring
claimed to "stop once past" the References section, but the loop actually
called the expensive `page.extract_text()` on every page from page 1 onward
just to search each page's text for the word "References" -- no early stop
of the expensive work ever happened, matching the recurring "Could not get
FontBBox from font descriptor" pdfplumber warnings seen throughout the log
(consistent with slow font-parsing overhead, not with network waiting).

Fixed by switching the PDF backend from `pdfplumber` to `pymupdf` (`import
pymupdf`, `page.get_text()` in place of `page.extract_text()` -- confirmed
already installed locally, 1.28.2, before switching; added to
`scripts/requirements.txt`). Directly measured on identical downloaded PDF
bytes (network time excluded, so this isolates parsing only): 2.67 sec vs.
0.13 sec for the same 10-page 2021 CVPR paper -- roughly 20x, in line with
PyMuPDF's general reputation for bulk text extraction. `fetch_cvf_affiliations.
py` (page-1-only extraction, a much smaller per-paper cost) was deliberately
left on pdfplumber -- not the bottleneck this was about, no reason to touch
a working, unrelated call site while chasing this one.

Verified equivalent output quality, not just equivalent speed, before
trusting it: ran both the old pdfplumber path and the new pymupdf path
against the exact same downloaded PDF bytes for two different papers (an
old 2013 single-column paper and a modern 2021 two-column paper) and
confirmed byte-for-byte-equivalent reference-section detection and near-
identical extracted text and reference-entry counts in both cases,
including a pre-existing (unrelated, not introduced by this change)
`split_reference_entries()` quirk on both papers -- a two-column PDF's text
sometimes interleaves in a way that its `[N] `-without-a-period regex
doesn't split into separate entries, and a chart's axis-label text can
occasionally trip the "References" heading search early. Both quirks
reproduce identically under the old and new backend, so this is a pre-
existing corpus/regex limitation to note, not a regression from this
change -- left as-is, out of scope for a PDF-speed/archiving request.

Also added (separately requested, same message: "make sure to save all pdfs
locally in a gitignored folder so we might process them later for other
things"): every downloaded CVF PDF is now archived to `data/pdfs_cvf/
<title-key>.pdf` (gitignored -- a many-GB personal cache, not source data),
written *before* parsing so even a PDF that fails to parse still leaves the
raw bytes on disk. This is a general-purpose local cache for future
reprocessing, not something the current pipeline reads back -- nothing else
in the pipeline depends on this directory existing or being complete.

One known gap, left as a deliberate choice rather than an oversight: the
~3,200 CVF papers the old (pre-fix) crawler run already marked `succeeded`
before this change won't be re-fetched under the normal pending-exclusion
logic in `fetch_phase()` (`succeeded` papers are always skipped), so they
won't retroactively gain an archived PDF just from re-running the crawler.
A deliberate backfill pass (temporarily ignoring `succeeded` to force a
re-download) would be needed to close that gap -- not done here since nothing
required it (their extracted references are already saved and correct; only
the raw-PDF archive itself would be incomplete for that subset).
