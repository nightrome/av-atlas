# What's in scripts/

Fifty-odd scripts sit here: the live pipeline, the crawlers that feed it,
one-off repairs kept for reproducibility, and a couple of shelved prototypes.
Nothing in the filenames says which is which, so this index does.

They deliberately are *not* split into subdirectories. Every one is referenced
by name from `build_public_site.py`, `PIPELINE.md`, `DECISIONS.md` and
`CLAUDE.md`, and several are documented in commit messages going back months;
moving them would break those references for a gain this file already
delivers. If a real reorganisation happens, it should update those together.

## The build

Everything below runs as one command. **Start here.**

| Script | Role |
| --- | --- |
| `deploy.py` | The single deploy command: runs the full build, commits to `main`, publishes `public/` to `gh-pages`. |
| `build_public_site.py` | The build itself: merge → repair → aggregate → test → publish → data release. Everything else on this page is either called by it or feeds the files it reads. |
| `run_tests.py` | The full test suite. Runs in CI too. |

## Pipeline core

Called by `build_public_site.py`, in this order.

| Script | Role |
| --- | --- |
| `merge_corpus.py` | Merges `data/venues/*.json` into `data/papers_full.json`, dedupes by normalized title, carries enrichment over, reclassifies everything. |
| `classify.py` | Assigns a category and an AV-relevance label. A module, not a standalone script. |
| `aggregate.py` | Turns the corpus into `data/stats.json` — every number every page shows. |
| `build_data_release.py` | Writes the downloadable CSVs under `public/download/`. |
| `repair_garbled_authors_detail.py`, `repair_glued_institution_strings.py` | Idempotent fixes for real, already-shipped data bugs. Run every build so a recrawl-from-scratch reproduces the same corpus. |

## Collection (stage 1)

Pull complete, unfiltered proceedings. Run by hand, not by the build.

| Script | Role |
| --- | --- |
| `fetch_cvf.py`, `fetch_cvf_history.py` | CVPR/ICCV/WACV from CVF Open Access. |
| `fetch_ecva_history.py` | ECCV from ecva.net. |
| `fetch_neurips.py`, `fetch_neurips_history.py` | NeurIPS proceedings. |
| `fetch_corl_history.py` | CoRL via PMLR. |
| `fetch_dblp_listing.py` | Venues with no scrapable proceedings site (RSS, ICLR, AAAI, and the journals). Titles and authors only, no abstracts. |
| `fetch_ieee_openalex.py` | ICRA/IROS via OpenAlex — IEEE Xplore blocks direct access. |
| `fetch_github_paper_lists.py` | ICRA/IROS years OpenAlex doesn't cover. |
| `fetch_arxiv.py` | arXiv preprints by top corpus authors. |
| `fetch_common.py` | Shared HTTP plumbing for every `fetch_*.py`. |

## Enrichment (stage 3)

Affiliations, links, abstracts. Each `fetch_*` writes a side file; the paired
`apply_*` folds it into `papers_full.json` — that split exists so two crawlers
can run at once without racing over the same file.

| Script | Role |
| --- | --- |
| `fetch_cvf_affiliations.py` → `apply_cvf_affiliations.py` | Affiliations from page 1 of CVF PDFs. |
| `fetch_affiliations_arxiv.py` → `apply_affiliations_arxiv.py` | Affiliations from arXiv HTML. |
| `enrich_core_authors.py` | Affiliations + author IDs via OpenAlex title search. Writes `papers_full.json` directly. |
| `enrich_core_authors_by_doi.py` | The same, batched 50 DOIs per request. Much cheaper where a DOI exists. |
| `fetch_arxiv_links.py` → `apply_arxiv_links.py` | An arXiv link for each core paper. |
| `mine_abstracts.py` → `apply_abstracts_arxiv.py` | Abstracts for papers whose venue source had none. |
| `fetch_s2_author_ids.py`, `fetch_orcids.py` | Stable author identifiers. |
| `fetch_institution_countries.py` | Institution → country. |
| `fetch_institution_logos.py`, `fetch_venue_logos.py` | Logos, via Wikipedia pageimages. |
| `institution_extraction_llm.py` | Clean institution names out of raw affiliation text, using a local LLM against a registry. |

**Never run two `papers_full.json` writers at once.** `enrich_core_authors.py`,
`enrich_core_authors_by_doi.py` and every `apply_*.py` write it directly; the
`fetch_*` scripts only write their own side files and are safe to run
alongside.

## Citation graph (stage 4)

| Script | Role |
| --- | --- |
| `build_citation_graph.py` | Parses reference lists into in-corpus citation edges. |
| `apply_citation_sources.py` | Folds the edge counts into `papers_full.json`. |
| `fetch_semanticscholar_citing.py` | Reverse-citation discovery; can surface new papers, which re-enter at stage 1. |
| `backfill_citing_venues.py` | Fixes venue strings on papers that discovery originally filed as plain "arXiv". |

## Classifier development

Not part of a build. These produced the trained model and the labels
`classify.py` reads.

| Script | Role |
| --- | --- |
| `train_relevance_classifier.py` | Trains the scorer, writes `data/relevance_model.json`. |
| `fetch_llm_relevance_labels.py`, `..._v2.py` | Local-LLM labelling passes (Ollama). |
| `evaluate_llm_relevance.py` | Grades those labels against hand-labelled ground truth. |
| `select_labeling_candidates.py`, `build_labeling_tool.py` | Pick a sample and stamp it into the hand-labelling page (now `dev/label_relevance.html`). |
| `flag_ambiguous_authors.py` | Flags name-keyed records that likely conflate several people. |
| `classify_code_links_llm.py`, `audit_code_links_llm.py` | The `has_code_link` classifier and an audit of it. Its Insights panel is parked until coverage is trustworthy. |

## One-off and shelved

Kept because they document something, not because they run.

| Script | Role |
| --- | --- |
| `strip_redundant_venue_fields.py` | A completed repo-size cleanup of `data/venues/*.json`. |
| `build_sqlite.py` | Prototype only. Feeds `dev/authors_sql_prototype.html`, which was measured and not adopted — see DECISIONS.md's "sql.js-httpvfs prototype". |
