# AV Atlas: design decisions

The reasoning behind choices that aren't obvious from the code, so a future change
doesn't quietly undo something deliberate. `PIPELINE.md` says which script pulls
which venue. This file is the "why".

## Citations are counted only between papers in the corpus

The site never shows an external citation count. `citation_count()` in
`aggregate.py` reads only `citations_by_source.in_corpus`: how many other corpus
papers' scanned reference lists cite this one. An external figure (OpenAlex's
`cited_by_count`, a Scholar number) measures impact on a different and far larger
population, and lets a paper that is famous for something unrelated dominate every
AV leaderboard. Coverage of the in-corpus graph is partial and only grows, so every
count is a floor, not a final number. The About page says so.

## The citation graph is re-keyed to match paper titles

`data/citation_graph.json` is keyed by a plain lowercase-and-digits version of each
title. `aggregate.py`'s own title key also drops a final "s", so that "Networks" and
"Network" count as one paper. Joined as they came, a paper whose title ends in a plural
word ("...against Missing Sensor Modalities") matched none of its edges, neither as the
citing nor as the cited paper. About 46% of all edges were being thrown away this way.

It showed up on UniBEV's paper page: "Cited by (all papers): 14" next to "Cited by (AV
papers): 0". The first number is stamped from the whole graph by
`apply_citation_sources.py`, the second is the list that survived the join. All 14
citers are AV papers.

`rekey_citation_graph()` puts the edges into the same key space as the titles when
`aggregate.py` loads the graph, so nothing has to be rebuilt. AV-to-AV citation links
went from about 90,600 to 161,600. Everything computed from them moves too: the citer
lists, dataset adoption on Insights, the disruption index and the self-citation counts.
Those numbers were low before, and they are now correct rather than different in meaning.

## `None` vs `0` for an unknown citation count

`citation_count()` returns `None`, not `0`, when no reference-list scan has reached
a paper yet. Treating "unknown" as "zero" drags every average toward zero for years
and venues with sparse coverage. These all keep the distinction:

- the sort keys in `aggregate.py`
- the `best_by_year` and `best_by_venue` selection (a paper with no citation data
  can't be "best")
- `aggregateByDimension` in `filters.js` (an uncited paper counts toward a group's
  paper count but not toward the denominator of its average)

The display shows "—", never a misleading "0".

## AV-relevance is a layered, auditable classifier

`classify.py` decides AV or non-AV from the title and abstract, in layers that each
push in only one direction:

1. Hard scope filters (mechanical or hardware-only papers, and non-road platforms
   such as aerial, underwater or legged robots) mean non-AV.
2. Keyword floor: an AV-specific phrase anywhere, or a standalone driving word in
   the title, means AV. Later layers can add to this but never override it.
3. A small linear model (`data/relevance_model.json`: per-phrase weights and a
   threshold, trained on hand labels) means AV, but only for papers the keyword
   floor missed.
4. A local-LLM "it's AV" verdict, used only to promote a paper.

The classifier can be searched end to end, and any paper's result can be explained
from the code. The LLM is a second opinion. It is graded against a hand-labelled
evaluation set, never trusted blindly, and never writes the ground-truth label file.

## Numbers are shown as whole numbers

Citation counts and averages are rounded to integers everywhere they're computed
(`aggregate.py`, `filters.js`). "213.68 cit./paper" looks precise, but the
underlying signal is a keyword heuristic over a partial citation graph, which
doesn't support that precision.

## Filters recompute rankings client-side from `all_papers`

`aggregate.py` ships the full `all_papers` array, not fixed top-N leaderboards, and
`aggregateByDimension` in `filters.js` rebuilds any ranking from whatever the active
filters leave. A leaderboard truncated on the server can't be re-sliced by a
category, country or institution filter. That made every filter a dead end on any
page except the Overview.

## The citation-source view is a client-side toggle

`stats.json` carries every paper's full `citations_by_source` map, not just the
blended pick. `applyCitationSource(stats)` in `filters.js` chooses one source and
changes `p.citations` in place right after the fetch and before anything renders, so
switching sources never needs a `stats.json` rebuild or a redeploy. Both
`all_papers` and `top_papers` are changed (after `JSON.parse`, a paper that appears
in both is two separate objects), and `best_by_year` is recomputed client-side for
the same reason. The preference is kept in `localStorage`, not in the URL.

## Abstracts are sharded out of `stats.json`

Every page fetches `stats.json`, but only `paper.html` ever reads an abstract, one at
a time. Bundling about 19,000 abstracts into the shared payload cost every visitor
several MB of gzip. They now live in `data/abstracts/shard-NN.json` (64 shards).

`shard_index()` is a plain djb2 hash of the title, modulo 64. There's no index file
to keep in sync, just the same hash on both ends. `paper.html` reimplements it in
JavaScript, and the two copies must stay byte-identical or every abstract silently
404s. `TestShardIndex` in `test_aggregate.py` pins the Python side.

## Venue names are aliased at the source

Sources report the same venue differently ("Advances in Neural Information
Processing Systems" vs "NeurIPS"). `VENUE_ALIASES` in `aggregate.py` normalizes them
so a venue doesn't split into two rows. `venues.html` also merges a few incidental
one-off IEEE-journal placements into a single row. That is display only: the `venue`
field on each paper is untouched, so filtering by the real name elsewhere still works.

## Institution names: local-LLM extraction, registry-anchored

Affiliation notes are prose ("...the Division of Robotics, Perception, and Learning
(RPL), KTH Royal Institute of Technology, Stockholm, Sweden"), not a comma-separated
list. Splitting on commas produced fragments, such as "Perception" as its own
institution, that later regex steps could never fully repair.

`institution_extraction_llm.py` gives the raw text to a local model and asks for the
institution names directly. To stop the same place being reinvented as "KTH", "Royal
Institute of Technology" and "KTH Royal Institute of Technology", each call is shown a
token-overlap shortlist from `data/institution_registry.json`. The model must either
return an existing canonical name exactly or propose a new one, which is then added
to the registry.

`data/institution_aliases_llm.json` is a one-time batched pass that folded existing
registry duplicates (diacritic and abbreviation variants) onto canonical names. It is
applied after the hand-typed `INSTITUTION_ALIASES`.

## An institution typed into an author field must not veto that institution

`author_affiliations()` throws away any "institution" that is really a person's name,
by exact match against every author name in the corpus. That works until one paper
lists an institution as an author. One BoundED paper has "Delft University of
Technology" in its author list, one HeightFormer paper has "Southeast University", one
survey has "Graz University of Technology". From then on every genuine TU Delft,
Southeast and Graz affiliation on the site was rejected as "a person's name". TU Delft
had no row at all on the Institutions page (71 AV papers), Southeast 41, Graz 29.

The set of author names now skips anything that reads as an institution (the same
`_LOOKS_ACADEMICISH_RE` that `is_valid_institution` uses). The check that catches real
leaked names ("Ben Sapp" showing up as an institution) is unchanged.

## A city glued onto an institution name

An address line like "Technical University of Darmstadt, Darmstadt" sometimes loses its
comma and arrives as "Technical University of Darmstadt Darmstadt", which then counted
as a second institution next to the real one. `strip_glued_city_suffix()` handles the two
shapes that are safe to be sure about: the last word repeats the word before it, or it
repeats the very first word of a name that still reads as an institution without it
("Seoul National University Seoul"). It leaves "Hong Kong University of Science and
Technology Hong Kong" alone on purpose, because a two-word city could cut the real name
in half. "TU Dortmund" and "TU Dortmund University" (12 and 3 papers) are one alias now.

## Institution country is only a fallback, and a company can span countries

`data/institution_countries.json` holds one country per institution. It exists for the
case where OpenAlex gave an author an institution but no country. It was applied to
every institution on a paper, whatever the authors' own countries were. A company works
in several places (Bosch in Germany and China, Huawei in Canada, France and Sweden,
Uber ATG in Toronto), so a paper by Bosch's Shanghai authors also got Germany.

An industry institution now only supplies its country for an author who has none of their
own. Universities stay in one place, so for them nothing changed. This removed a country
from 93 AV papers. The two country calculations in `aggregate.py` (the per-paper list and
the leaderboard tally) go through the same `institution_country_applies()` so they agree.

Three map entries were wrong and are fixed: Vector Institute was Russia (it is in
Toronto), York University was the UK (it is in Toronto too), and Technical University of
Darmstadt was missing, which left 4 of its 10 papers with no country.

Not changed: an institution still has exactly one country on the Institutions page, its
detail page and the country filter, so a multinational shows up under its home country
only. Authors whose country was already stamped into `authors_detail` from a wrong map
entry keep it until their affiliations are applied again.

## Countries get their own page

Clicking a country used to jump to the Papers page filtered to it, which answered "what
did this country publish" and nothing else. `country.html?name=` now shows a country the
way `institution.html` shows an institution: totals, papers by year, top authors, top
institutions, categories, venues and the paper list. It is computed in the browser from
`all_papers` and the author-detail shards, so the build has nothing new to produce. The
Papers-page link is still on it ("See these in the Papers page").

A country's authors are the ones whose own record lists that country, not every author
of a paper that touches it (the reasoning in `countries.html`). Its institutions are the
ones `institution_countries.json` places there, so a foreign co-author's university
doesn't show up under it. That is also why the authors and institutions lists are lower
bounds: only people and places with an affiliation on record can appear.

## Scholar profiles and photos: confirm or skip

A profile is saved to `data/scholar_profiles.json` only after a cross-check against a
real signal: a shared co-author on a specific corpus paper, or a matching institution
in the Scholar bio. Ambiguous same-name matches are left unresolved. A wrong photo on
the wrong person is worse than a missing one, on a site whose premise is being
defensible about what it claims.

## Paper Scholar links come from author profiles, never from title searches

`paper.html` and the Source column used to link to a Scholar search for the paper's
title. A search can land on a different paper, a citing paper or nothing, so those
links were removed. A paper now shows a Scholar link only if `scholar_url` is in
`stats.json`, which `aggregate.py` takes from `data/scholar_paper_links.json`.

`fetch_scholar_paper_links.py` fills that file for the 1000 most-cited AV papers. It
never searches Scholar. It reads the profile pages already in `scholar_profiles.json`,
whose rows carry a stable `citation_for_view` URL. A row is accepted only if the
normalized title is identical, one author agrees (surname plus initial), and the years
are within one year. Titles that are ambiguous among the target papers or listed twice
on a profile are skipped. Scholar answers HTTP 429 after roughly 70 profile pages in a
row, so the script paces itself, stops at the first block and resumes from
`profiles_done` on the next run.

## DBLP-sourced venues have no abstracts

RSS, ICLR and AAAI are pulled from DBLP, because their own sites block scripted
access. DBLP has never carried abstracts, so `is_fully_processed()` requires only a
title and year for these venues, and `classify.py` falls back to title-only keyword
matching. These papers carry a weaker relevance and category signal than the rest of
the corpus. The Methodology page lists this under "Known gaps".

## Misc vs uncategorized

`classify_paper()` in `classify.py` used to leave every paper that matched no category
keyword as `"uncategorized"`, AV-relevant or not. `categories.json`'s own `_readme`
called that deliberate: a signal that the taxonomy needs a new category, not that the
paper has none. In practice 3,647 of 25,664 AV papers (14.2%) sat in `"uncategorized"`,
which `categories.html` hides from the ranked table and shows once as a footnote. That
was a large, permanently growing slice of the AV corpus with no real visibility. The
user asked for very few papers to be uncategorized, and for uncategorized papers to be
told apart from misc ones.

The fix splits the same fallback in two by `av_relevance`, which is computed just
before the split:

- An **AV paper** falls to `"misc"`. It already matched an explicit AV-relevance
  phrase, so it's confirmed on-topic. Not fitting a specific category doesn't make it
  any less real, so it gets a normal, ranked, browsable bucket like the other 30.
- A **non-AV paper** still falls to `"uncategorized"`. Its relevance is the weaker
  signal, and non-AV papers never appear in the ranked UI anyway, so tracking a
  missing category for them isn't useful in the same way.

This is a post-processing split of the existing fallback, not a new matching path, and
nothing about how a category keyword match is scored changed. On real data, AV-side
`"uncategorized"` is now exactly 0 (all of it moved to `"misc"`), and `"misc"` never
appears on a non-AV paper.

`"misc"` is deliberately not listed in the `categories` array in `categories.json`.
It isn't keyword-matched, so it can't be extended by adding keywords the way a real
category can, and listing it would invite exactly that.

Alongside the split, about 215 papers that used to fall through were moved into real
categories. A direct audit of the then-3,647-paper uncategorized set showed some
common phrasings that no category matched, so several keyword lists were extended:

- plurals ("traffic lights control" next to "traffic light control")
- synonyms ("scene understanding" alongside "segmentation")
- missing but unambiguous terms ("kalman filter", "valet parking", "emergency
  braking", "ramp merging", "lane change", "temporal logic", "driver
  activity"/"activity recognition")

At that point `"misc"` held 13.4% of AV papers and was the single largest bucket,
larger than any one real category. That showed the taxonomy still had room to grow.
The next few entries cover what growing it involved, which brought `"misc"` down to
12.8%.

## Surveys/reviews are a paper-type gate, same tier as Datasets

An audit of `"misc"` (checking `index.html?category=misc`, sorted by citations)
found "A Survey of X" among its most-cited entries. Corpus-wide, 541 AV papers have
`\bsurvey\b|\breview\b` in the title, and they were already scattered across the other
30 categories by keyword luck. This is the same problem `dataset-benchmark-paper`
already solves for dataset papers (see `presents_dataset()`): topic keywords describe
what a paper covers, not what it is.

`is_survey_or_review_paper()` in `classify.py` is the same kind of title gate. It runs
right after the dataset and radar gates, so a radar survey still lands in
`radar-perception` and a dataset survey in `dataset-benchmark-paper`, both by design.
It runs before the topic-keyword ranking.

It checks the bare words `survey` and `review` instead of a curated phrase list,
because checking all 541 matches turned up only 2 false positives. Both use the other
sense of "survey" (a questionnaire): "An Online Survey" and "...a driver activity
survey". Those two are excluded explicitly instead of guessing at a general rule.

`"survey-review-paper"` has no real keywords of its own in `categories.json`.
`dataset-benchmark-paper` also catches a weaker abstract-only mention, but that added
complexity isn't worth it for a paper type this reliably detectable from the title.

## Explainability had to be a last-resort category, not a normal one

It was added alongside Surveys/Reviews (same audit, same request) to cover the
"Interpretable X" / "Explainable X" cluster in `"misc"`. A first attempt added it as a
normal competing category, like all the others, and immediately reproduced the
bare-word problem that `categories.json`'s own `_readme` warns about for keyword
phrases. 193 papers landed in `"explainability"`, but most weren't from `"misc"`. They
were pulled out of a more specific category that was already correct. Two real
examples:

- "Hint-AD: Holistically Aligned Interpretability in End-to-End Autonomous Driving"
  (an end-to-end driving paper)
- "Interpretable Self-Aware Neural Networks for Robust Trajectory Prediction" (a
  motion-prediction paper)

Both lost their specific category. An explainability-flavoured abstract repeats
"interpretable" or "explainable" several times as a matter of course, and the raw
occurrence count in `score_category()` has no defence against a short, frequently
repeated word outscoring a topic phrase that appears once or twice. The usual
mitigation, a multi-word phrase, doesn't work here because the concept has no longer,
more specific phrasing to prefer.

The fix is a different priority, not different keywords. `LAST_RESORT_CATEGORY_IDS` in
`classify.py` takes `explainability` out of the normal ranking pass entirely. It's
ranked only in a second pass, and only when the first pass matched nothing at all.
That isn't a scoring tiebreak: a last-resort category is never even evaluated against
a paper that a normal category already claims, so no repeat count can win one away.
The count dropped from 193 to 57, and all the papers listed above went back to their
correct categories. That is much closer to what a category meant only for the "misc"
leftovers should look like. The keywords stay in `categories.json` as the single
source of truth, and only the ranking-pass membership is hardcoded, in one small
`frozenset` in `classify.py`.

There is one exception. "Textual Explanations for Self-Driving Vehicles" was sitting in
Control, because its abstract says "controller" five times (the network being explained)
and that wins the abstract-wide count. Its title says what the paper is about. So when a
title itself matches an explainability keyword and no normal category is named anywhere in
that title, explainability now takes the paper. This is the same "a title states what the
paper is" reasoning the tie-break above already uses. Hint-AD ("... Interpretability in
End-to-End Autonomous Driving") and the trajectory-prediction paper still keep their
categories, because their titles name a normal topic too.

It does move papers: AV papers in Explainability went from 55 to 164. Nearly all have
"explainable", "interpretable" or "explanation" in the title, for example "Explaining How a
Deep Neural Network Trained with End-to-End Learning Steers a Car" and "Explainable
Object-Induced Action Decision for Autonomous Vehicles" (which had been in Segmentation).
If that turns out to be too many, the fix is to require the title keyword to be
"explanation" or "explaining" only, which is the narrower part of the change.

## An LLM category guess is the last thing consulted, weaker than any keyword

`fetch_llm_category_labels.py` handles what neither keyword matching nor a real
abstract can reach: title-only papers (DBLP-sourced venues never had abstracts; see
"DBLP-sourced venues have no abstracts") that are still in `"misc"` after every
keyword path, including the last-resort tier above, has had its turn.
`classify_paper()` only consults it when `category` is still `"uncategorized"` at that
point. One local model's guess from a single title must never outrank a real keyword
match, the way `explainability`'s bare words accidentally did. So the guess isn't part
of the ranking at all. It's just the very last fallback before the misc/uncategorized
split.

A spot check of the first batch found real value alongside real imprecision:

- "A Statistical GPS Error Model for Autonomous Driving" went to `mapping-localization`
  and "Real-Time Prediction of Multi-Class Lane-Changing Intentions" to
  `motion-prediction`, both correct.
- "Traffic-Responsive Control Technique for Fully-Actuated Coordinated Signal..." went
  to `control`, but it's about traffic-signal control and belongs in
  `traffic-flow-management`. The model sees only each category's `{id, label}`, which
  isn't enough to tell "control" the vehicle-dynamics sense from "control" the
  traffic-signal sense every time.

We accepted this as a known, bounded tradeoff. This tier only touches papers that had
no topic signal at all before it ran, so a right-ish guess is still better than an
unbroken "misc". It is the weakest and most provisional signal in the whole stack and
is never promoted above a real keyword match. If a specific confusion like this turns
out to be common and not a one-off, revisit the prompt's category descriptions.

`category: null` (the model's own "none of these fit") is written to
`data/category_labels_llm.json` but deliberately not read back by `classify.py`. It's a
useful answer (don't force a category that doesn't exist), but it should give the same
"misc" result as a paper this pass hasn't looked at yet, so there's nothing to
distinguish at the classification layer.

## Object Detection and Mapping & Localization split by sensor/task, not merged

They were the two largest categories by a wide margin (2,020 and 2,062 papers). The
user asked to split them for a more even distribution, the same way Datasets, Surveys
and Explainability carve distinct concerns out of a crowded taxonomy instead of making
it flatter.

**`object-detection` became `object-detection-2d` and `object-detection-3d`,** split
by sensor modality. Bare "2D" or "3D" mentions turned out to be almost useless as a
keyword signal: of 2,020 papers only 35 said "2D" explicitly, because it was
historically the unmarked default and nobody writes it. LiDAR, point-cloud, BEV and
voxel language is a reliable proxy, though. 3D papers overwhelmingly also say "3D"
explicitly ("3D Object Detection", "LiDAR 3D Vehicle Detection", ...), and 2D or
camera-based ones use the shared generic detection vocabulary without it. The result
was 1,282 and 1,132 papers, about as even as this kind of split gets. "radar" was
deliberately left out of the 3D keyword list, because `radar-perception`'s gate (checked
before all normal categories) already claims radar-based detection first, so it would
never fire.

**`mapping-localization` became `mapping` and `localization`.** This is a cleaner
conceptual line (HD maps and lane information vs SLAM, odometry and pose) than an even
one: 605 and 1,361 papers, with localization the naturally larger, more heavily
researched half. We kept it anyway. Halving one bucket beats having a single one, and
the two are different reader interests, since someone hunting for HD-map papers doesn't
want the SLAM literature mixed in.

## The 2D/3D object-detection split needed its own tie-break, not a global one

The split created a collision the original single category never had. The bare
`'object detection'` keyword in `object-detection-2d` is a literal substring of every
specific phrase in `object-detection-3d` (`'3d object detection'`, `'lidar object
detection'`, `'point cloud object detection'`, ...). So a genuinely 3D paper's title
always ties 2D on the title-score check in `rank()`, since both match. Then whichever
other, dimension-unrelated 2D keyword ("detector", "bounding box") appears in the
abstract wins the combined-score tiebreak for 2D. On real data, "Center-Based 3D
Object Detection and Tracking" (CenterPoint), DETR3D, Voxel R-CNN, PolarFormer, HDNET
and about 385 more all landed in `object-detection-2d` this way, despite an explicit
"3D Object Detection" in their own titles.

`rank()`'s docstring describes the intended tiebreak order as title match, then longest
matching keyword, then id, but the actual tuple checks combined score before longest
match, so the docs and the code disagree. We tried the literal fix (reordering the tuple
so the longest match outranks combined score everywhere) and measured its blast radius
first. 6,878 of 235,288 papers (about 3%) would be reclassified, across dozens of
unrelated category pairs (`llm-vlm-driving` vs `reinforcement-learning`, `segmentation`
vs `general-cv-ml-method`, ...), with no way to review that many changes. That was far
too broad for a problem that only one category pair had.

Instead there's a narrow, explicit override right after the normal ranking loop. If a
paper lands on `object-detection-2d`, and `object-detection-3d` matched the title at
least as well, and 3D's longest matching keyword is longer (more specific) than 2D's,
the paper goes to 3D. It's scoped to the roughly 388 papers with exactly this ambiguity
(spot-checked: all genuinely 3D by title). `rank()`'s general tiebreak and every other
category pair are untouched.

## `load_llm_category_labels()` must re-validate against the live taxonomy

The split above orphaned `data/category_labels_llm.json`. 65 entries still said
`object-detection` and 96 still said `mapping-localization`, ids that no longer exist in
`categories.json`. `load_llm_category_labels()` had no reason to distrust its own file,
so `classify_paper()` applied a dead id as-is wherever it was the last fallback. On the
live site that appeared as literal "Mapping Localization (40)" and "Object Detection
(33)" rows in every page's category filter, the Categories ranking table and the
Insights category-correlation matrix. (`categoryLabel()` title-cases any id it doesn't
recognize instead of hiding it.)

`merge_corpus.py`'s `main()` now builds the set of currently valid ids, from the same
`taxonomy` list it already loads for keyword ranking, and passes it to
`load_llm_category_labels()`. That function drops any entry whose `category` isn't in
the set, which is the same "fail safe back to misc/uncategorized" behaviour an absent or
`null` entry already gets. It's a permanent check, not a one-time cleanup of the JSON
file, and it has to hold for every future category split or rename. The LLM-label file
is cached output from an expensive crawl, re-running it isn't a data-quality fix on its
own, and nothing else in the pipeline checks its contents against the taxonomy that
produced it.

## Derived data is not tracked; `gh-pages` is a single squashed commit

`data/stats.json`, the sharded `data/non_av_papers/` and the abstract shards are
gitignored. `aggregate.py` regenerates them from `data/papers_full.json` alone, in well
under a minute, and tracking a leaderboard dump bloats every diff with numbers that
change on every corpus update and aren't reviewable anyway. `deploy.py` force-pushes
`gh-pages` as one orphan commit each time instead of committing on its history. The
branch is 100% generated output, and appending multi-MB snapshots that don't compress
against each other would grow the repo forever.

**`data/papers_full.json` and `data/citation_graph.json` are a different case, and
calling them "fully regenerable" used to overstate it.** `merge_corpus.py` only
*classifies* papers from scratch correctly (that part really is regenerable, from the
tracked `data/venues/*.json` plus `classify.py`). Author affiliations,
`citations_by_source`, abstracts and arXiv links are carried forward from whatever
`papers_full.json` already exists, not re-derived. We confirmed directly that a fresh
clone rebuilt this way gets the right paper list and classification but no author,
institution or country data. Recovering that enrichment means re-running the CVF and
arXiv affiliation scrapers and the Semantic Scholar and ORCID lookups, which is the
"weeks of crawling plus API keys" that `build_data_release.py`'s docstring describes.

`citation_graph.json` is a second, separate gap. `aggregate.py` reads its `edges`
directly for the disruption index and the citation-graph coverage table. If that file
alone is lost, even with `papers_full.json` intact, those features silently go blank on
the next rebuild, and nothing in `papers_full.json`'s carry-forward list protects it.

Neither file is tracked in git, so each had exactly one live copy and no backup.
Whichever machine held the only `papers_full.json` with real enrichment in it was a
single point of failure. The fix is to snapshot both files somewhere durable after
every deploy, not to keep believing a fresh rebuild reproduces them.

## By-hand data corrections are scripts, not one-off edits

Any manual fix to `papers_full.json` (an author merge, an institution-name correction)
goes in a tracked, tested, idempotent `repair_*.py` script. It's wired into
`build_public_site.py` between `merge_corpus.py` and `aggregate.py`, so a corpus rebuilt
from scratch after data loss comes back with every correction applied. Author-name
fixes live in `KNOWN_NAME_FIXES` in `aggregate.py` and institution aliases in the
tracked JSON maps, and both are re-applied on every build.

## One build command, always with tests

`build_public_site.py` runs the whole pipeline in order (rebuild the corpus, apply
repairs, rebuild stats, run the full test suite, publish `public/`) and aborts before
publishing if any step fails. Crawler scripts write into `data/papers_full.json` and
stop there. Folding every later step into one command, the same one `deploy.py` calls,
makes "crawled but never published" impossible by construction, instead of a step to
remember.

## Finding the backup release by tag needs a list-and-filter, not the tags endpoint

`backup_corpus.py` and `restore_corpus.py` find the `corpus-backup` release by calling
`GET /repos/.../releases` and filtering by tag on the client, not with
`GET /repos/.../releases/tags/corpus-backup`, which looks like the right endpoint.
GitHub's "get release by tag" only resolves *published* releases. A **draft** release
has no real git tag ref, so that endpoint returns 404 even when a draft with that
`tag_name` exists.

That was confirmed on the live repo:

- With the old lookup, `get_or_create_release` always got a 404 and fell through to
  "create new release". Every `backup_corpus.py` run silently created a fresh draft
  instead of reusing the existing one, and 4 duplicate `corpus-backup` drafts piled up
  on GitHub, contradicting the module's own "one fixed tag, one asset, always replaced"
  docstring.
- `restore_corpus.py` had the same lookup with no fallback, so it was flatly broken.
  Every restore raised an uncaught `HTTPError: 404` and downloaded nothing.

Both now list all releases and match on `tag_name` on the client. `backup_corpus.py`
also deletes any stale duplicates beyond the most recent one, so a repo hit by the old
bug cleans itself up on its next backup.

## Every crawler processes its queue most-cited-first, not corpus order or random

`fetch_common.by_citations()` is now the shared ordering for every incremental
crawler's pending queue. It sorts by `citations_by_source.in_corpus.count`, not the
top-level `citations` field, which is dead (0 of 25,639 AV papers have it set,
including nuScenes). It's used by:

- `mine_abstracts.py`
- `fetch_abstracts_semanticscholar.py`
- `enrich_av_authors.py` (already had its own copy, now consolidated)
- `fetch_affiliations_arxiv.py` (same)
- `fetch_arxiv_links.py`
- `fetch_cvf_affiliations.py`
- `build_citation_graph.py`
- `fetch_s2_author_ids.py`
- `fetch_semanticscholar_citing.py`
- `fetch_llm_category_labels.py`
- `classify_code_links_llm.py` (previously alphabetical by normalized title, a
  tuple-sort accident)

The user asked for this. Every external source in the pipeline is rate-limited or
budget-capped (see PIPELINE.md), so a run that gets cut off partway should already have
enriched the papers readers actually see (top-cited lists, comparison pages,
leaderboards), not whichever paper happened to load first from its venue file.

Two crawlers were asked about specifically, and the user made a choice on each:

- **`fetch_s2_author_ids.py`.** An earlier "sort by author count" ordering was reverted
  because it skewed ORCID and S2-ID coverage toward big-collaboration papers. Citation
  count risks the same skew, since a highly cited paper often has a large author list
  too. It was switched to most-cited-first anyway, confirmed by the user, accepting that
  trade-off again.
- **`fetch_semanticscholar_citing.py`.** This one used to be sorted by citations and was
  reverted after a confirmed, self-defeating bug. A paper (or a whole venue) with zero
  in-corpus citations *because it has never been queried* always sorts last, so it can
  never earn a citation and rise in priority. It got stuck exactly this way ("IV and
  arXiv still have no citations") after a run well over a third of the way through the
  corpus. It was switched back anyway, confirmed by the user, accepting that long-tail
  venues may go unqueried again.

These were deliberately not switched, and shouldn't be: `select_labeling_candidates.py`,
`select_near_threshold_candidates.py`, `train_relevance_classifier.py`,
`fetch_llm_relevance_labels.py` and `_v2.py`, and `audit_code_links_llm.py`. They all
feed or evaluate the relevance classifier and need an unbiased random draw, not a
priority order. Sorting them by citations would skew training and evaluation data toward
older, already-popular papers.

## A raw DBLP-suffixed author name in papers_full.json is not a live bug

While looking at the top of the most-cited-authors list, we found names like "Andreas
Geiger 0001" in the raw `authors` field of `papers_full.json`. That's a DBLP
disambiguation suffix, present on 2,377 distinct base names and several thousand raw
occurrences. Before writing a fix we checked the output this feeds.
`clean_author_name()` in `aggregate.py` already strips a trailing `\s+\d{4}`
unconditionally (`DBLP_DISAMBIG_SUFFIX_RE`). In `stats.json`, "Andreas Geiger 0001"
appears nowhere in `all_papers[].authors`, and the real "Andreas Geiger" entry already
carries the correct, unified 7,098 citations and 56 papers. Nothing fragmented reaches
the live site.

This is written down so it doesn't get "fixed" again from the same starting point. It
was the second time in a session that a raw `papers_full.json` check almost drove a
change that the processed output already handled. The lesson holds: check `stats.json`
and `stats_detail.json`, which the site actually reads, before treating a string in
`papers_full.json` as evidence of a live bug.

## `flag_ambiguous_authors.py`'s heuristics false-positive on prolific lab researchers

We checked its 4 unconfirmed "review"-tier candidates from the current top 60 by
citations (Hongyang Li, Long Chen, Hang Zhao, Zheng Zhu) against Google Scholar instead
of leaving them in the queue. All four are real, single, extremely prolific researchers
in the fast-moving, highly collaborative modern AV and world-model community (near
OpenDriveLab and Shanghai AI Lab):

- Hang Zhao's papers consistently share one email (hangzhao@mail.tsinghua.edu.cn).
- Hongyang Li and Long Chen keep appearing as each other's co-authors across a large,
  coherent, recent (2023-2026) end-to-end driving publication cluster.
- Zheng Zhu's papers form one coherent "driving world models" research thread.

None are marked ambiguous in `scholar_profiles.json`, because that would wrongly warn
on a real researcher's legitimate, unified profile.

This is a generalizable false-positive mode. The script's structural signals (disjoint
co-author clusters, papers per active year, category spread; see its docstring) were
tuned on the classic failure case, a common name silently blending 2-3 unrelated
academics. They weren't tuned for a newer pattern that this corpus's recent years are
full of: one person embedded in a large, fast-publishing lab, co-authoring across many
loosely connected sub-teams and projects. That looks structurally just like "several
disjoint collaboration circles" even though it's one person. A common surname plus high
recent output should be weighed as a real prior for "prolific lab researcher", not just
"possible collision", when the corpus's co-author graph forms one connected,
thematically coherent recent cluster this consistently.

Nothing was changed here, because it needs care not to under-flag the opposite, real
case. If this pattern keeps showing up at the top of future review runs, revisit the
scoring itself.

## Whole-corpus citations: the feature already existed, only the data didn't

The user asked to integrate "citations from the whole corpus, not just AV papers" and to
make sure every place showing a citation number says which one it is. Before building
anything we checked what already existed. `paper.html` already shows exactly this in two
adjacent stat tiles, each with its own explanatory tooltip:

- "Cited by (all papers)" is `paper.citations`, i.e. `citations_by_source.in_corpus.count`.
- "Cited by (AV papers)" is `paper.citing_papers.length`, the subset of those citers
  that are themselves AV papers.

`in_corpus_counts()` in `apply_citation_sources.py` already counts every edge in
`citation_graph.json` with no AV filter. We confirmed this on real data before touching
anything: nuScenes already showed 2,742 for "all papers" vs 1,938 for "AV papers".

What was actually missing was upstream. `build_citation_graph.py` only ever scanned the
reference lists of `av_relevance=="AV"` papers (both its own CVF-PDF fetch and
`fetch_affiliations_arxiv.py`'s ar5iv-HTML side channel). So `citation_graph.json`'s
edges could only ever contain AV citers, and "all papers" was capped at "the AV slice"
however the counting code was written. It's fixed at the source: `main()` now builds its
CVF title pool from the whole corpus (34,352 more papers, prioritized most-cited-first
like everywhere else; see `fetch_common.by_citations`), not just the AV subset.
`citations_by_source.in_corpus`, `citing_papers` and `paper.citations` keep their exact
meaning and code. They just get fed a more complete graph as the crawl (running at the
time) works through the backlog over the following days.

We drafted a parallel `in_corpus_all`/`citations_all` field and reverted it before
shipping. It would have duplicated the existing `in_corpus`/`citing_papers` pair under
new names and, worse, changed `in_corpus`'s own counting to exclude non-AV citers, which
is the opposite of what "Cited by (all papers)" has always meant. That's recorded here so
the same duplicate isn't built again from the same confusion.

The arXiv and ar5iv side (`fetch_affiliations_arxiv.py`, about 55,534 more non-AV papers
with a known `arxiv_url`) is not widened yet. Its reference extraction is a side effect
of its expensive per-paper LLM affiliation extraction, which non-AV papers don't need at
all, so widening it naively would waste that LLM cost on about 55,000 papers just to get
a reference list. It needs its own leaner, reference-only path, and that's left as a
follow-up.

## Whole-corpus citations from Semantic Scholar reference lists

Until now only about 15,000 papers had a reference list at all (the CVF PDFs and the
ar5iv pages), so a paper's in-corpus count only included citations from that slice.
`fetch_s2_references.py` gets Semantic Scholar's reference list for any paper in the
corpus. It maps each paper to an S2 CorpusId once (`data/s2_paper_ids.json`), stores
each paper's references as CorpusIds (`data/reference_lists_s2.json`), and
`build_citation_graph.py` turns a reference into an edge when its id maps back to a
corpus paper. There's no title matching on this path, so it can't produce the
near-miss matches the text matcher has to guard against.

We ran it on a sample of 1,998 papers (222 at random from each of CVPR, NeurIPS, ICLR,
ICRA, IROS, T-ITS, ITSC, IV and arXiv-only) before merging:

- 1,950 (97.6%) got an S2 id: 453 by arXiv id, 57 by DOI, 1,302 from the per-venue
  bulk scan and 138 by per-title search. The lowest venue was IROS at 92%.
- Only 917 of those came back with a reference list. For 934 S2 knows how many
  references there are but withholds the list at the publisher's request. It's worst
  for IEEE: T-ITS 29 of 222, ITSC 30, IV 56, while arXiv-only got 208 and ICLR 159.
  CVPR (106) and NeurIPS (121) sit in between.
- The 917 lists hold 20,637 in-corpus citations. The current graph had 3,139 for the
  same papers, so 17,749 are new, and 732 of the 917 papers had no reference list of
  any kind before. Of the 3,139 existing edges, only 251 aren't in S2's lists.

Scaled up by venue size, that's roughly 1.5 million new edges from these nine venues
alone, against 377,000 in the whole graph today. The full crawl takes something like
half a day to a day at S2's rate limit, most of it the per-title searches for the
roughly 9% of papers that neither an id nor the venue scan finds. It's resumable and
runs most-cited-first like every other crawler.

The withheld lists can sometimes still be had from the one-paper
`/paper/{id}/references` endpoint. A probe of 27 withheld papers found them for all 6
arXiv-only papers, 2 of 7 CVPR papers and none of the 14 from ICLR, NeurIPS, ICRA,
T-ITS and ITSC. That costs one request per paper, so `--step elided` exists but isn't part of
the default run, and it's meant for the arXiv venues.

The S2 edges are merged with the text-matched CVF and arXiv edges per citing paper and
deduplicated, so a citation found by both counts once.

## Manually-sourced abstracts for the top-cited gap

The user asked us to look up missing information for the most-cited papers specifically.
We took the AV papers with the most in-corpus citations that were still missing an
abstract (topped by KITTI's two seed papers, with 1,901 and 1,336 citations) and checked
each by hand against its own publisher page (IEEE Xplore, ACM DL, CMU RI publications)
in a real browser session, not a script. Every one had already been queried by
`fetch_abstracts_semanticscholar.py` and came back with no abstract there either, so this
wasn't a case of the crawler not having reached it yet.

13 verbatim abstracts were recovered this way and applied directly to `papers_full.json`,
stamped `abstract_source: "manual-publisher-page"` so the batch stays distinguishable
from every automated source.

This matches the IEEE Xplore finding in PIPELINE.md ("Other abstract sources we looked
at"): a real browser shows the abstract even though scripted access gets a `418`. Here
it's applied at small, human-in-the-loop scale, not automated. It was a one-time patch of
13 papers, not a crawler, and it won't be repeated as one. PIPELINE.md explains why an
automated scraper stays off the table however well it would work technically.

## In-corpus citation counts vs. real-world (Google Scholar) counts

The user asked us to estimate how accurate this corpus's own citation counts are compared
with Google Scholar. We sampled 15 AV papers spanning three orders of magnitude (nuScenes
at 2,742 in-corpus down to a 5-citation T-ITS paper), looked each up on Google Scholar by
hand and compared:

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

This corpus's own count captures roughly **5-23%** of a paper's true citation count. It's
never close to complete, by design: it only counts citations from papers whose reference
list this pipeline has actually scanned, not all of academic literature. It's not wildly
inconsistent either. Two real patterns show up, not just noise:

- **How AV-core or adjacent-field a paper is matters more than its raw citation count.**
  The two lowest ratios (4.2% and 5.7%) are a T-ITS traffic-engineering paper and a
  domain-adaptation and segmentation paper. Both are cited heavily by fields that this
  corpus's roughly 20 tracked venues barely touch (general traffic engineering, general
  semantic segmentation). The KITTI papers score surprisingly low (8.8% and 9.5%) for the
  same reason, despite being foundational AV datasets. An enormous share of their real
  citers are generic computer vision and robotics papers with nothing to do with
  autonomous driving, which sit outside this corpus's scope by construction and not
  through a coverage gap.
- **There's no strong bias by citation-count tier itself.** A highly cited paper (nuScenes,
  22.8%) and a modestly cited one (IntentNet, 21.6%) can land at nearly the same ratio.
  The venue and topic fit above matters more than sheer popularity.

This is not a reason to change how citations are computed or labelled. The "Cited by (all
papers)" and "Cited by (AV papers)" pair on `paper.html` already says plainly that both
numbers are corpus-internal and never come from an external database (see "Whole-corpus
citations" above). This is a sanity check on that honesty, not a finding that anything
needs fixing. A reader who wants a truer "how cited is this in the world" number already
has an explicit, correct signal in the tooltip that this isn't it.

## `build_citation_graph.py`'s PDF text extraction: pdfplumber -> PyMuPDF, plus a local PDF archive

The overnight widened citation-graph crawl (see "Whole-corpus citations" above) was
running at about 23 seconds per paper. We confirmed that for real, not by estimate, by
comparing the growth of `data/reference_lists_cvf.json` with wall-clock time over a run
of about 21 hours: only 3,200 of 32,686 CVF papers were done. The laptop sleeping for
part of that night inflated the wall-clock total but didn't explain the per-paper rate.

The root cause: `fetch_pdf_text()`'s docstring claimed to "stop once past" the References
section, but the loop actually called the expensive `page.extract_text()` on every page
from page 1 onward, just to search each page's text for the word "References". No early
stop of the expensive work ever happened. This matches the recurring "Could not get
FontBBox from font descriptor" pdfplumber warnings in the log, which point to slow font
parsing, not network waiting.

The fix was to switch the PDF backend from `pdfplumber` to `pymupdf` (`import pymupdf`,
with `page.get_text()` in place of `page.extract_text()`). We confirmed it was already
installed locally (1.28.2) before switching, and added it to `scripts/requirements.txt`.
On identical downloaded PDF bytes (network time excluded, so this isolates parsing),
the same 10-page 2021 CVPR paper took 2.67 seconds before and 0.13 seconds after, about
20x faster, in line with PyMuPDF's reputation for bulk text extraction.

`fetch_cvf_affiliations.py` (page-1-only extraction, a much smaller per-paper cost) was
deliberately left on pdfplumber. It wasn't the bottleneck, and there was no reason to
touch a working, unrelated call site while chasing this one.

We checked that the output quality is equivalent, not only the speed. We ran both the old
and new paths on the exact same PDF bytes for two papers, an old 2013 single-column paper
and a modern 2021 two-column paper. Reference-section detection was identical, and the
extracted text and reference-entry counts were nearly identical. Both papers also showed
a pre-existing quirk in `split_reference_entries()` (not introduced by this change): a
two-column PDF's text sometimes interleaves in a way that its `[N] ` regex (no period)
doesn't split into separate entries, and a chart's axis-label text can occasionally trip
the "References" heading search early. Both quirks reproduce identically with the old and
new backends, so they're an existing limitation and not a regression. We left them alone
as out of scope for a speed and archiving request.

The user also asked, in the same message, to "save all pdfs locally in a gitignored folder
so we might process them later for other things". Every downloaded CVF PDF is now archived
to `data/pdfs_cvf/<title-key>.pdf`, which is gitignored because it's a many-GB personal
cache and not source data. It's written *before* parsing, so a PDF that fails to parse
still leaves its raw bytes on disk. It's a general-purpose local cache for future
reprocessing. Nothing in the current pipeline reads it back or depends on it existing.

One gap was left deliberately. The roughly 3,200 CVF papers that the old (pre-fix) crawler
run had already marked `succeeded` won't be re-fetched, because `fetch_phase()` always
skips `succeeded` papers. They won't gain an archived PDF just from re-running the
crawler. Closing the gap would need a deliberate backfill pass that temporarily ignores
`succeeded` to force a re-download. We didn't do it because nothing required it: their
extracted references are already saved and correct, and only the raw-PDF archive is
incomplete for that subset.

## `stats_non_av.json` sharded into `data/non_av_papers/`, same scheme as abstracts

The single-file non-AV dataset had grown to 81.5MB, past GitHub's 50MB recommended
single-file size. Every gh-pages push flagged it ("File stats_non_av.json is 77.76 MB...
GH001: Large files detected"), a real concern while assessing whether the site was ready
to go public and attract traffic. It also had a more direct cost. `paper.html`'s fallback
lookup checks a paper that isn't in the AV-relevant set against the non-AV set too (a
paper can be in the corpus without being AV-relevant, for example if it was reached only
through the citation graph). That lookup downloaded and parsed the entire 81.5MB file just
to find one paper by title.

It's fixed the same way `ABSTRACTS_DIR` solved the same problem for abstract text. The
data is sharded into `data/non_av_papers/shard-NN.json` (64 shards), reusing the title-hash
`shard_index()` and `ABSTRACT_SHARD_COUNT` already used for abstracts. The largest shard is
1.32MB, comfortably under any size limit, with room to grow for years.

- `paper.html`'s single-title lookup now fetches exactly one shard instead of the whole
  set.
- Two consumers genuinely need the whole non-AV set: `fetchStatsWithRelevance` in
  `filters.js` (the "Show: include non-AV papers" dropdown on every listing page) and
  `author.html`'s per-author non-AV paper list. They fetch and join all 64 shards through
  a new shared `window.fetchAllNonAvPapers()` helper in `filters.js`, so neither
  reimplements the same shard loop.

Those two cases download the same total bytes as before, and only the `paper.html` fallback
gets less data. All three consumers stop triggering GitHub's single-file warning.

`build_public_site.py` copies the shard directory file by file, the way it already copies
`abstracts/`. It never uses `shutil.rmtree`, because of a real OneDrive directory-lock
`PermissionError` this repo hit in practice (see the abstracts entry). The old single
`stats_non_av.json` in `public/` is cleaned up by the existing sweep that removes anything
not in the expected set, the same mechanism that once caught a stray
`label_relevance.html`. No special deletion was needed, only removing the old filename from
that expected set.

**Investigated but declined:** converting `fetch_cvf_affiliations.py`'s page-1-only
pdfplumber extraction to PyMuPDF, the same swap that gave `build_citation_graph.py` a 20x
win (see the entry above). The two cases aren't alike. That win came from removing an
early-stop bug that made pdfplumber scan every page instead of one, not from pdfplumber
being slow on a single page. This script already touches only page 1. It also hand-tunes a
pdfplumber parameter (`x_tolerance=1`) that we confirmed against real CVF PDFs stops
two-column author and affiliation blocks from losing word spacing entirely ("University of
California at Merced" becoming "UniversityofCaliforniaatMerced"). PyMuPDF's text extraction
has no equivalent setting, so swapping backends risks silently bringing that bug back in a
feature the Institutions and Countries pages rely on for correctness. The per-paper cost
saved would be far smaller than in the citation-graph case. The script also wasn't running
at the time (it had last touched a side file three days before this check), so there was no
active throughput problem to justify the risk.

## Staging site and incremental gh-pages deploys

`gh-pages` is force-pushed as a single commit with no parent on every deploy (see "Derived
data is not tracked" above), so it has to stay unprotected. Only `main` is protected. Three
changes make deploys cheap and add a preview step:

- **Persistent clone, single-commit push.** `deploy.py` keeps a clone per target in
  `.deploy-cache/`, copies only changed files from `public/` into it, and pushes a commit
  made with `commit-tree` and no parent. Because the clone knows the remote's current tip,
  git sends only the blobs the server doesn't already have, and history still never
  accumulates.
- **Staging is a second repo, not a subfolder or branch of this one.** A separate repo keeps
  167 MB preview snapshots out of this repo's size, needs no branch-protection changes on
  the main repo, and gets its own Pages URL. Staging HTML is the production HTML
  post-processed at publish time (noindex, a banner, staging canonical URLs), so `--promote`
  ships the byte-identical build that was previewed. `.deploy-cache/state.json` records the
  previewed content hash.
- **The corpus rebuild is skipped automatically** when a stat fingerprint of the pipeline
  scripts, the git-tracked `data/` sources and `papers_full.json` matches the one saved
  after the last full build. Tests still run on the fast path. Scripts that can't change
  `stats.json` (`deploy.py`, `run_tests.py`, the backup and restore scripts and
  `build_data_release.py`) are left out of the fingerprint so editing them doesn't force a
  10-minute rebuild. `build_public_site.py` is also left out, except for its list of
  `run_step` calls, because adding or reordering a step there does change what a full build
  produces and would otherwise be skipped silently.

## Wrong venue names from Semantic Scholar, and "missing" vs "not yet parsed"

Semantic Scholar's `venue` string for arXiv-discovered papers is sometimes wrong: it
expanded ICIP to "International Conference on Information Photonics" (247 papers), showed
the MDPI journal Sensors as "Italian National Conference on Sensors", and gave short names
like "Most", "Delta" or "Machine-mediated learning" that are unrelated to the paper.

- **Verified renames** (`aggregate.py`'s `VENUE_ALIASES` for ICIP and Sensors,
  `scripts/fix_suspect_venues.py` for the rest) were each checked against the paper's own
  S2 journal name or DOI. Real journals in adjacent fields (Optics Express, Nature
  Communications, Physica A) are left alone; they do publish AV papers.
- **Short names that can't be tied to a real venue are discarded, not guessed.** The record
  goes back to `conference: "arXiv preprint"` and gets `venue_status: "missing"`, with the
  discarded string kept in `venue_raw`.
- **`venue_status: "missing"` means "we looked, there is no usable venue".** An arXiv record
  with no `venue_status` means "not looked up yet". The two used to look identical.
  `backfill_citing_venues.py` sets `missing` when S2 has no venue and skips records that
  already have a status. `stats.json` reports both counts as
  `corpus_stats.venue_missing` and `venue_unparsed`.
- The S2 source file is gitignored, so `fix_suspect_venues.py` runs first in
  `build_public_site.py`, so a fresh crawl can't bring the bad names back.
