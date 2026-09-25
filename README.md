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
Without it the build stops at the merge step: about 45% of the AV papers come
from `data/venues/arxiv_s2_citing.json`, which is gitignored too, and the rest
would have no author, institution or citation data.

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
  last full build. That means every JSON file in `data/` and `data/venues/`,
  tracked or not (so `papers_full.json`, `citation_graph.json`, the reference
  lists and a venue file nobody has added to git yet all count), and the pipeline
  scripts. The build's own output (`stats.json` and the shard folders) and
  `data/pdfs_cvf/` don't. Editing `deploy.py`, `run_tests.py`, the backup
  scripts, or anything in `build_public_site.py` other than its list of steps
  doesn't count. Tests still run. `--full` forces the rebuild. `--skip-build`
  skips both the rebuild and the tests, which is only safe for HTML, JS and CSS
  changes.
- Only files that changed are uploaded, using a local clone in `.deploy-cache/`.

A build also stops, before anything is published, if the new `stats.json` lost
more than 3% compared with the previous one on any of: AV papers, AV papers with
an institution, AV papers with an abstract, in-corpus citations, or venues
covered. It compares with the local `stats.json` from before the build, or the
live site's when there isn't one. If the drop is expected, pass
`--allow-shrink` to `deploy.py` or `build_public_site.py`. The first rebuild
after a change that removes false matches (such as a stricter title matcher)
will usually need it:

```bash
python scripts/deploy.py --preview --allow-shrink
```

Run `--preview` and `--promote` from the same checkout, because the preview is
remembered there.

Both repos need `gh-pages` to allow force pushes. To set up staging, create an
empty public repo `nightrome/av-atlas-staging`, run `--preview` once, then turn on
Pages from its `gh-pages` branch. To use a different repo, pass `--staging-repo`
or set `AV_ATLAS_STAGING_REPO` and `AV_ATLAS_STAGING_URL`.

## Backing up and restoring the crawled corpus

Some gitignored files in `data/` can't be regenerated the way `stats.json` can:
`papers_full.json` (the enrichment), `venues/arxiv_s2_citing.json` (the papers
found through Semantic Scholar citations, about 45% of the AV corpus),
`citation_graph.json`, and the crawlers' resume files. DECISIONS.md explains
why, under "Derived data is not tracked". After every successful deploy,
`scripts/backup_corpus.py` saves them to a draft GitHub Release on this repo.
The full list is `BACKUP_FILES` in that script; PDFs are never included. It
skips the backup if no `GITHUB_TOKEN` is set, and any other failure ends the
deploy with "BACKUP FAILED".

On a new machine, restore before building:

```bash
python scripts/restore_corpus.py     # pulls the latest backup into data/
python scripts/build_public_site.py  # now builds with the real enrichment
```

The laptop and the monthly GitHub Actions job both write backups. To keep one
from overwriting a newer backup made by the other, `backup_corpus.py` only
replaces the backup this checkout last restored or wrote (it keeps a note in
the gitignored `data/corpus_backup_state.json`). If it refuses, run
`restore_corpus.py` first, or pass `--force` to replace the backup with this
checkout's data on purpose. The new archive is uploaded before the old one is
deleted, so a failed upload leaves the previous backup in place.

Backing up and restoring both need `GITHUB_TOKEN`, either in `.env` (see Setup)
or as an environment variable.

## The pipeline

Five stages, run roughly in this order. The About page has a diagram.

1. **Collect.** `fetch_cvf.py`, `fetch_neurips.py`, `fetch_dblp_listing.py`,
   `fetch_ieee_openalex.py` and others pull the complete proceedings of each
   venue from its own source, with no keyword filtering. New arXiv preprints come
   in monthly through `fetch_arxiv_monthly.py`, which keeps only the AV ones.
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

## Monthly update

`.github/workflows/monthly-update.yml` updates the site on its own, at 03:17 UTC on
the 4th of every month. It runs `scripts/monthly_update.py`, which:

1. restores the corpus backup (`restore_corpus.py`);
2. fetches what's new: arXiv preprints (`fetch_arxiv_monthly.py`), new conference
   editions (`check_new_editions.py`, which runs the right fetcher itself), journal
   papers from Crossref (`fetch_crossref.py journals`), and Semantic Scholar
   abstracts and reference lists for papers that don't have them yet;
3. builds the site with `build_public_site.py`, including the tests and the check
   that the corpus didn't shrink;
4. publishes to production with `deploy.py --skip-build --no-main-commit` (it never
   commits to `main`);
5. backs the corpus up again (`backup_corpus.py`);
6. if any tracked file changed, such as a new venue file or the arXiv ledger, pushes
   it to the `auto/monthly-update` branch and opens or updates a pull request.

Nothing waits for a review. The site stays correct even if that pull request is never
merged: each run starts from `main` plus whatever the bot branch has that `main`
doesn't, and everything else a run needs is in the backup. Merge it when convenient
to keep `main` in step. There is no LLM step, and no PDF is involved.

If a step fails, the job opens an issue called "Monthly update failed" (or comments on
the open one) with the step table and the end of the log. A failed source (arXiv down,
say) doesn't stop the rest; a failed restore, build or publish does, and then
production keeps last month's site.

To try it without changing anything, run the workflow by hand from the Actions tab
with "Only print what would run" ticked, or locally:

```bash
python scripts/monthly_update.py --dry-run
```

Don't run it for real on the laptop. It publishes to production and pushes the bot
branch.

**Secrets.** The maintainer adds two repository secrets (Settings, Secrets and
variables, Actions):

- `AV_ATLAS_BOT_TOKEN`: a fine-grained personal access token with access to
  `nightrome/av-atlas` only, and "Contents" and "Pull requests" set to read and
  write. It is used for the gh-pages push, the backup release and the pull request.
  It can't be the built-in `GITHUB_TOKEN`: a gh-pages push made with that doesn't
  start a Pages build, and a pull request it opens doesn't run the tests. Fine-grained
  tokens expire, so renew it before then; an expired token shows up as a failed
  restore in the issue.
- `SEMANTIC_SCHOLAR_API_KEY`: the same key as in `.env`.

The failure issue and keeping the schedule switched on use the built-in token. GitHub
turns off scheduled workflows in a public repo after 60 days without activity, so each
run switches its own workflow back on, which resets that clock.

**Two writers of the backup.** The laptop and the monthly job both work on the same
corpus, through the backup release. The rule is that every laptop session that
changes the corpus starts with `python scripts/restore_corpus.py` and ends with
`python scripts/backup_corpus.py` (a deploy does the backup for you). Don't run
crawlers on the laptop while the monthly job is running. If the two do overlap, the
second backup is refused rather than overwriting the first, and the issue says so.

**The first full Semantic Scholar crawl.** `fetch_s2_references.py` has to look up
every paper once, which takes longer than one job can run. The job gives it whatever
time is left before the build, stops it cleanly, and the next month carries on from
the backup, so it gets there after a few months on its own. To have it done at once,
run it on the laptop:

```bash
python scripts/restore_corpus.py
python scripts/fetch_s2_references.py
python scripts/deploy.py --no-main-commit   # builds, publishes and backs up
```

After that, each month only has the new papers to look up.

## Reporting a data error

Every page has a "spot an error?" link that opens an email pre-filled with the
page you were on. Use it for anything wrong with a specific paper, author or
institution.
