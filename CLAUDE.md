# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## What this is

AV Atlas is a static GitHub Pages site (`https://nightrome.github.io/av-atlas/`) that
tracks autonomous-vehicle research: who's publishing, which institutions/countries are
active, how topics have shifted over time, and which papers are most cited *within AV
research specifically*. Built from the complete, unfiltered proceedings of ~20 major
computer vision/robotics venues, not a keyword-filtered subset. See
[README.md](README.md) for the pipeline overview and [about.html](about.html)
(or the live About page) for exactly how papers are found, classified, and scored.

It used to live as a sub-app inside a private family monorepo (`family-portal`); it was
split out into this standalone repo so it can be developed and linked to independently.

## Commands

```bash
# Single command for everything: rebuilds the corpus (merge_corpus.py), rebuilds
# stats (aggregate.py), runs the full test suite, then publishes. Aborts before
# publishing if any step fails.
python scripts/build_public_site.py

# Build, commit sources to main, and push the built site to gh-pages
python scripts/deploy.py

# Same, but skip committing to main (e.g. nothing source-side changed)
python scripts/deploy.py --no-main-commit

# Run just the test suite (stdlib unittest + plain-Node JS, no install needed)
python scripts/run_tests.py

# Serve the built site locally (run a build first so public/ exists)
cd public && python -m http.server 8747
# then open http://localhost:8747/index.html
```

GitHub Actions (`.github/workflows/tests.yml`) runs `run_tests.py` on every push to
`main` and every PR -- the Python unit tests only, since the smoke/regression tests
need the gitignored `data/stats.json`. `scripts/deploy.py` still runs the full
build/test pipeline locally before publishing, and `scripts/run_tests.py` should
be run before every commit.

**Never run `aggregate.py` or a raw build directly expecting freshly-crawled data to
already be reflected** -- always go through `scripts/build_public_site.py` (or
`scripts/deploy.py`, which calls it), since that's the one command that reruns
`merge_corpus.py` first. Crawler scripts (`mine_abstracts.py`,
`backfill_citing_venues.py`, `enrich_core_authors.py`, ...) write straight into
`data/papers_full.json` or `data/venues/*.json` and stop there -- nothing about running
one guarantees its results reach `stats.json` or the live site on its own.

**Don't run `merge_corpus.py`/`aggregate.py` directly against this checkout while a
crawler script is still running against it in the background** -- both would be writing
`papers_full.json` at once, and the crawler's next periodic save can silently clobber
the fresh merge.

## Architecture

- `site/` -- every source file the published site is built from: `index.html`,
  `authors.html`, `institutions.html`, `venues.html`, `countries.html`,
  `categories.html`, `network.html`, `insights.html`, `about.html`, `author.html`,
  `institution.html`, `venue.html`, `paper.html`, `compare.html` (the pages), the
  shared JS/CSS below, `logo.svg`/`og-image.png`, and `site/assets/` (vendored
  institution/venue logos). `build_public_site.py` copies this tree into `public/`
  alongside a fresh `stats.json`; there's no build step for the pages themselves.
  Each page reads `stats.json` (or `stats_adjacent.json` for the "include
  adjacent/non-core papers" view) at runtime.
- `site/nav.js` -- shared top nav bar, injected into every page via `<nav id="topnav">`.
- `site/filters.js` -- shared filter-bar component (`renderFilterBar`) used across the
  listing pages.
- `site/sortable.js` -- shared click-to-sort-any-column table behavior.
- `site/theme.css` + `site/theme-light.css` -- `theme.css` defines the base CSS custom
  properties (`--bg`, `--panel`, `--border`, `--text`, `--muted`, `--accent`,
  `--accent2`) and body reset; `theme-light.css` layers AV Atlas's actual light/modern
  palette on top (indigo `#4f46e5` + amber `#f59e0b`).
- `site/logo.svg` -- the site mark (car + LiDAR dome + scan), used as the favicon and in
  the nav bar brand.
- `site/og-image.png` -- static 1200x630 social share card (same mark/palette as
  logo.svg), referenced by every page's Open Graph/Twitter Card meta tags.
  Hand-generated, not rebuilt by any script -- regenerate only if the brand mark changes.
- `scripts/` -- the whole data pipeline (see README.md) plus `build_public_site.py` and
  `deploy.py`.
- `data/` -- the crawled/derived corpus. The large derived files (`papers_full.json`,
  `stats.json`, `stats_adjacent.json`, `abstracts/shard-NN.json`, citation-graph side
  files, reference-list dumps) are gitignored -- regenerable from the smaller tracked
  source files (`data/venues/*.json`, `scholar_profiles.json`, `orcids.json`, ...), not
  worth the repo bloat or unreviewable diffs. `data/abstracts/` is sharded out of
  `stats.json` (see DECISIONS.md's "Abstracts sharded out of stats.json" entry) --
  `paper.html` is the only page that fetches one, computing which shard from the
  paper's title via the same hash `aggregate.py`'s `shard_index()` uses.
- `tests/` -- pure-JS logic tests (`*.test.js`, plain Node) plus a smoke test
  (`qa_smoke_test.js`) and a structural UI regression test (`ui_regression_test.js`),
  both run via `dom_stub.js` against real page scripts and real data.
- `scripts/tests/` -- Python unit tests (stdlib `unittest`).

## Git conventions

- Commit author: `holger@it-caesar.com` (nightrome)
- Always co-author: `Co-Authored-By: Claude <noreply@anthropic.com>`
- Never commit `.env` (real API keys) or anything under `data/` that's gitignored (see
  `.gitignore` for the reasoning behind each entry)
