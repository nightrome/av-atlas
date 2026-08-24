# TODO

Open items deliberately deferred, not forgotten.

## Consider outsourcing the paper database

Right now the full corpus (`data/papers_full.json`, `stats.json`, and the
various enrichment side-files) is entirely homegrown: scraped, merged, and
hosted as static JSON in this repo. Worth revisiting later whether some of
this should instead lean on an existing paper database/API (Semantic Scholar,
OpenAlex, etc.) as the source of record rather than this project's own
scrape-and-store pipeline — trade-off between control/reproducibility (current
approach) and maintenance burden (an outsourced approach). No action yet;
flagged for a later decision once the project's public-facing shape settles.
