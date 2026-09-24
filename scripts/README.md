# What's in scripts/

About fifty scripts live here: the live pipeline, the crawlers that feed it,
one-off repairs, and a couple of shelved prototypes. The filenames don't say which
is which, so this index does.

They are deliberately not split into subfolders. `build_public_site.py`,
`PIPELINE.md`, `DECISIONS.md` and `CLAUDE.md` all refer to them by name, and so do
many old commit messages. If they are ever reorganised, update those together.

## The build

Start here. One command runs everything in this section.

| Script | What it does |
| --- | --- |
| `deploy.py` | The deploy command. `--preview` publishes to the staging site, `--promote` publishes that build to production. With no flags it builds, commits to `main`, publishes and backs up the corpus. |
| `build_public_site.py` | The build itself: merge, repair, aggregate, test, publish, data release. It also assigns the site version. Every other script here is either called by it or produces files it reads. |
| `run_tests.py` | The full test suite. CI runs it too. |
| `backup_corpus.py`, `restore_corpus.py` | Save `papers_full.json` and `citation_graph.json` to a draft GitHub Release, and pull them back on a new machine. `deploy.py` runs the backup after each deploy. |

## Pipeline core

`build_public_site.py` runs these in this order.

| Script | What it does |
| --- | --- |
| `email_addresses.py` | Replaces every email address in the tracked `data/` files with its domain, since a crawl can bring them back. Also the helper the affiliation cache and `aggregate.py` use to keep addresses out. |
| `merge_corpus.py` | Merges `data/venues/*.json` into `data/papers_full.json`, removes duplicates by normalized title, keeps existing enrichment, and reclassifies everything. |
| `classify.py` | Gives each paper a category and an AV-relevance label. A module, not a script you run. |
| `repair_garbled_authors_detail.py`, `repair_glued_institution_strings.py`, `repair_openalex_institution_errors.py` | Fixes for real data bugs that had already shipped. Safe to re-run, and run on every build so a recrawl from scratch gives the same corpus. |
| `aggregate.py` | Turns the corpus into `data/stats.json`, which holds every number the pages show. |
| `build_data_release.py` | Writes the downloadable CSVs to `public/download/`, named with the site version. |

## Collection (stage 1)

These pull complete, unfiltered proceedings. Run them by hand, not through the
build.

| Script | What it does |
| --- | --- |
| `fetch_cvf.py`, `fetch_cvf_history.py` | CVPR, ICCV and WACV from CVF Open Access. |
| `fetch_ecva_history.py` | ECCV from ecva.net. |
| `fetch_neurips.py`, `fetch_neurips_history.py` | NeurIPS proceedings. |
| `fetch_corl_history.py` | CoRL from PMLR. |
| `fetch_dblp_listing.py` | Venues with no proceedings site to scrape (RSS, ICLR, AAAI and the journals). Titles and authors only, no abstracts. |
| `fetch_ieee_openalex.py` | ICRA and IROS through OpenAlex, because IEEE Xplore blocks direct access. |
| `fetch_github_paper_lists.py` | ICRA and IROS years that OpenAlex doesn't cover. |
| `fetch_arxiv.py` | arXiv preprints by the corpus's top authors. |
| `fetch_common.py` | Shared HTTP code used by every `fetch_*.py`. |

## Enrichment (stage 3)

Affiliations, links and abstracts. Most crawlers come in pairs: `fetch_*` writes a
side file and the matching `apply_*` folds it into `papers_full.json`. The split
lets two crawlers run at the same time without fighting over one file.

| Script | What it does |
| --- | --- |
| `fetch_cvf_affiliations.py` → `apply_cvf_affiliations.py` | Affiliations from the first page of CVF PDFs. |
| `fetch_affiliations_arxiv.py` → `apply_affiliations_arxiv.py` | Affiliations from arXiv's HTML pages. |
| `enrich_av_authors.py` | Affiliations and author IDs from an OpenAlex title search. Writes `papers_full.json` directly. |
| `enrich_av_authors_by_doi.py` | The same, but 50 DOIs per request, which is much cheaper for papers that have a DOI. |
| `fetch_arxiv_links.py` → `apply_arxiv_links.py` | An arXiv link for each core paper. |
| `mine_abstracts.py` → `apply_abstracts_arxiv.py` | Abstracts for papers whose venue source had none. |
| `fetch_abstracts_semanticscholar.py` → `apply_abstracts_semanticscholar.py` | A Semantic Scholar fallback for the abstracts still missing, mostly from DBLP venues. Never overwrites an abstract that already exists. |
| `fetch_s2_author_ids.py`, `fetch_orcids.py` | Stable author identifiers. |
| `fetch_institution_countries.py` | Which country each institution is in. |
| `fetch_institution_logos.py`, `fetch_venue_logos.py` | Logos, taken from Wikipedia page images. |
| `institution_extraction_llm.py` | Pulls clean institution names out of raw affiliation text, using a local LLM and a registry of known institutions. |

**Never run two writers of `papers_full.json` at once.** That means
`enrich_av_authors.py`, `enrich_av_authors_by_doi.py` and every `apply_*.py`. The
`fetch_*` scripts only write their own side files, so they are safe to run
alongside anything.

Nothing here talks to Google Scholar, which doesn't allow automated queries.
`data/scholar_profiles.json` and `data/scholar_paper_links.json` are edited by hand
(see DECISIONS.md, "Paper Scholar links are added by hand").

## Citation graph (stage 4)

| Script | What it does |
| --- | --- |
| `build_citation_graph.py` | Parses reference lists into citation edges between papers in the corpus. |
| `apply_citation_sources.py` | Adds the citation counts to `papers_full.json`. |
| `fetch_semanticscholar_citing.py` | Finds papers that cite the corpus. This can surface new papers, which then go back through stage 1. |
| `backfill_citing_venues.py` | Fixes the venue on papers that were first filed under plain "arXiv". |

## Classifier development

Not part of a build. These produced the trained model and the labels that
`classify.py` reads.

| Script | What it does |
| --- | --- |
| `train_relevance_classifier.py` | Trains the scorer and writes `data/relevance_model.json`. |
| `fetch_llm_relevance_labels.py`, `fetch_llm_relevance_labels_v2.py` | Labelling passes with a local LLM (Ollama). |
| `fetch_llm_category_labels.py` | Uses a local LLM to pick a category for AV papers that are stuck in "misc" because they only have a title. |
| `evaluate_llm_relevance.py` | Checks those labels against hand-labelled ground truth. |
| `select_labeling_candidates.py`, `build_labeling_tool.py` | Pick a sample and generate the hand-labelling page, `dev/label_relevance.html` (gitignored). |
| `select_near_threshold_candidates.py` | A sample aimed at recall: papers marked "adjacent" that are in a driving-named category and clearly about vehicles or traffic. For a targeted round of hand labelling, unlike the two general-purpose samplers above. |
| `flag_ambiguous_authors.py` | Flags author records that probably mix up several people. |
| `classify_code_links_llm.py`, `audit_code_links_llm.py` | The `has_code_link` classifier and its audit. The Insights panel that uses it is parked until coverage is good enough to trust. |

## One-off and shelved

Kept because they document something, not because anyone runs them.

| Script | What it does |
| --- | --- |
| `strip_redundant_venue_fields.py` | A finished cleanup that shrank `data/venues/*.json`. |
