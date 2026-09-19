# AV Atlas

A dashboard of autonomous vehicle (AV) research: who is publishing, which
institutions and countries are active, how topics have changed over time, and
which papers are cited most *within AV research*. It is built from the complete
proceedings of about 20 major computer vision and robotics venues, not from a
keyword-filtered subset. The [About page](site/about.html) explains how papers
are found, classified and scored.

Live site: https://nightrome.github.io/av-atlas/

[![tests](https://github.com/nightrome/av-atlas/actions/workflows/tests.yml/badge.svg)](https://github.com/nightrome/av-atlas/actions/workflows/tests.yml)

The code is MIT-licensed ([LICENSE](LICENSE)). The paper metadata is CC BY-NC 4.0,
for non-commercial research and reference use, and is still subject to the terms
of its original sources ([LICENSE-data](LICENSE-data)). If you use AV Atlas in
academic work, see [CITATION.cff](CITATION.cff).

## Where things are

| Folder | What's in it |
| --- | --- |
| `site/` | The website: plain HTML, JS and CSS, no backend. |
| `scripts/` | The data pipeline, the deploy script and their tests. [scripts/README.md](scripts/README.md) explains each script. |
| `data/` | The collected corpus. The large derived files are gitignored. |
| `tests/` | JavaScript tests for the site. |

For how the corpus was built venue by venue, see [PIPELINE.md](PIPELINE.md). For
why things are the way they are, see [DECISIONS.md](DECISIONS.md).

## Downloading the data

The whole corpus is available as four gzipped CSVs: papers, authorship, citations
between papers in the corpus, and institutions. They join on `paper_id`. You can
find them through the About page. `scripts/build_data_release.py` regenerates them
on every build, and they come with their own README.

Please read that README before using them. In short: citation counts only cover
this corpus and are incomplete, affiliations are known for only a minority of
papers, author names are not disambiguated, and all classification is automated.

## Running the site locally

The site is static and just reads a `stats.json` served next to it.

```bash
python scripts/build_public_site.py   # assembles public/
cd public && python -m http.server 8747
```

Then open http://localhost:8747/index.html.

`stats.json` is produced by the pipeline and is gitignored, so a fresh clone has
no data. Restore the crawled corpus first (see
[Backing up and restoring](#backing-up-and-restoring-the-crawled-corpus)).
Otherwise the build finishes quickly but the site has no author, institution or
citation data.

## Deploying

Look at a build on the staging site first, then promote that same build to
production:

```bash
python scripts/deploy.py --preview   # build and publish to staging only
python scripts/deploy.py --promote   # publish the previewed build to production
```

- Staging lives at https://nightrome.github.io/av-atlas-staging/ (a separate
  repo). Its pages are marked `noindex`, show a PREVIEW banner, and its
  `robots.txt` blocks all crawlers.
- `--promote` refuses to publish if `public/` has changed since the preview.
  `--force-promote` overrides that.
- Running `python scripts/deploy.py` with no flags skips staging. It builds,
  commits source changes to `main`, publishes to production, and backs up the
  corpus.

Deploys are quicker than they used to be:

- The corpus rebuild is skipped when nothing that feeds it has changed since the
  last full build. That means the tracked files in `data/`, `papers_full.json`
  and the pipeline scripts. Editing `deploy.py`, `run_tests.py`, the backup
  scripts, or anything in `build_public_site.py` other than its list of steps
  doesn't count. Tests still run. `--full` forces the rebuild. `--skip-build`
  skips both the rebuild and the tests, which is only safe for HTML, JS and CSS
  changes.
- Only files that changed are uploaded, using a local clone in `.deploy-cache/`.

Run `--preview` and `--promote` from the same checkout, because the preview is
remembered there.

Both repos need `gh-pages` to allow force pushes. To set up staging, create an
empty public repo `nightrome/av-atlas-staging`, run `--preview` once, then turn on
Pages from its `gh-pages` branch. To use a different repo, pass `--staging-repo`
or set `AV_ATLAS_STAGING_REPO` and `AV_ATLAS_STAGING_URL`.

## Backing up and restoring the crawled corpus

`data/papers_full.json` and `data/citation_graph.json` are gitignored, and unlike
`stats.json` they can't be fully regenerated. DECISIONS.md explains why, under
"Derived data is not tracked". After every successful deploy,
`scripts/backup_corpus.py` saves both to a draft GitHub Release on this repo. It
skips this if no `GITHUB_TOKEN` is set.

On a new machine, restore before building:

```bash
python scripts/restore_corpus.py     # pulls the latest backup into data/
python scripts/build_public_site.py  # now builds with the real enrichment
```

Backing up and restoring both need `GITHUB_TOKEN` in `.env` (see Setup).

## The pipeline

Five stages, run roughly in this order. The About page has a diagram.

1. **Collect.** `fetch_cvf.py`, `fetch_neurips.py`, `fetch_dblp_listing.py`,
   `fetch_ieee_openalex.py`, `fetch_arxiv.py` and others pull the complete
   proceedings of each venue from its own source, with no keyword filtering.
2. **Merge and classify.** `merge_corpus.py` removes duplicates across sources and
   `classify.py` gives each paper a category and an AV-relevance label.
3. **Enrich.** For AV-relevant papers only: author affiliations
   (`fetch_cvf_affiliations.py`, `fetch_affiliations_arxiv.py`), ORCIDs
   (`fetch_orcids.py`) and Semantic Scholar author IDs (`fetch_s2_author_ids.py`).
4. **Citation graph.** `build_citation_graph.py` parses reference lists, and
   `fetch_semanticscholar_citing.py` finds papers that cite the corpus. That can
   turn up new paper titles, which feed back into stage 1.
5. **Aggregate.** `aggregate.py` writes `data/stats.json`, which every page reads.

## Setup

```bash
pip install -r scripts/requirements.txt
```

Some enrichment scripts (`fetch_semanticscholar_citing.py`,
`fetch_s2_author_ids.py`, `fetch_orcids.py`) need a free Semantic Scholar API key.
Copy `.env.example` to `.env` and fill it in. You can get a key at
https://www.semanticscholar.org/product/api. `.env` is gitignored, so never commit
a real key.

The backup and restore scripts need a `GITHUB_TOKEN` in the same file. A
fine-grained personal access token for this repo with read and write access to
"Contents" is enough.

## Tests

```bash
python scripts/run_tests.py
```

This runs the Python unit tests (`scripts/tests/`), the JavaScript logic tests
(`tests/*.test.js`, plain Node, nothing to install), and smoke, regression and
detail-page tests that load every page's script against real data.

CI runs the same command on every push and pull request. The last three tests need
`data/stats.json`, which is gitignored. Without it they are skipped with a notice
instead of failing, so run `scripts/build_public_site.py` first for a full run.

## Reporting a data error

Every page has a "spot an error?" link that opens an email pre-filled with the
page you were on. Use it for anything wrong with a specific paper, author or
institution.
