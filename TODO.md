# TODO

Open items deliberately deferred, not forgotten.

## Document how in-corpus citations relate to Google Scholar counts

This site's citation counts are deliberately internal-only (see Methodology
> Citation counts) — but readers will still want a sense of scale against the
numbers they already know from Google Scholar. Worth a documented,
reproducible comparison: sample a set of papers with both an in-corpus count
and a known Scholar count, compute the ratio (rough expectation: Scholar is
roughly 3x higher, since it counts citations from far outside this corpus's
~20 venues), and report the ratio's mean and standard deviation — not just a
single multiplier, since it likely varies a lot by paper age and subfield.
Publish the methodology and result on the Methodology page once done, framed
as "a rough multiplier, not a conversion."

## sql.js-httpvfs prototype: real numbers, mixed verdict

Prototyped replacing the Authors page's "download the whole ~19MB
stats.json up front" model with a queryable SQLite file fetched over HTTP
range requests (`scripts/build_sqlite.py` builds it,
`researchers_sql_prototype.html` is the pilot page -- not linked from nav,
not deployed, local-only spike). Vendored `sql.js-httpvfs` under
`vendor/sqljs-httpvfs/` (self-hosted, required by the CSP).

**Confirmed working end to end**, with real measured numbers (Chrome, this
corpus, this schema):
- GitHub Pages' CDN genuinely supports HTTP Range requests (`206 Partial
  Content`, verified directly against the live site) -- the hard
  prerequisite holds.
- A narrow query (a precomputed per-author summary table, indexed) costs
  ~420KB to return the top-50 leaderboard, vs. today's ~19MB gzip transfer
  for the same rendered result -- a real ~45x reduction.
- Second interactions are close to free: paging to page 2 of the same query
  cost 0 additional bytes (the relevant index pages were already cached
  from page 1).

**The catch**: SQLite's query planner did NOT handle the raw 3-way join
(paper_authors + papers + authors, grouped/ordered/limited) well at all --
it drove the scan from the largest table (255k authors) regardless of
indexes, touching ~45-60MB of the ~90MB file for what should've been a
top-50 lookup. Fixing this needed a precomputed summary table
(`author_stats`), which only covers the unfiltered default view -- a
category/venue filter falls back to the expensive live join. The "how many
total match" COUNT query needed its own covering-index fix (papers added to
the index) to stop being the dominant cost (was ~15MB alone). Net result for
a full first-render (list + count + populating filter dropdowns): ~3.8MB --
a real but much more modest ~5x improvement over today's ~19MB, not the
~45x the narrow-query number suggests.

**Bottom line**: the mechanism is real and GitHub-Pages-compatible, but
getting a genuine win requires schema/query engineering per query shape,
not a drop-in replacement -- every page (Institutions/Venues/Countries/
Network all have their own aggregate shapes) would need its own tuned
summary table(s) the same way Authors did. Worth doing only if the ~19MB
transfer is an active problem, not because the idea is inherently better.

## Consider outsourcing the paper database

Right now the full corpus (`data/papers_full.json`, `stats.json`, and the
various enrichment side-files) is entirely homegrown: scraped, merged, and
hosted as static JSON in this repo. Worth revisiting later whether some of
this should instead lean on an existing paper database/API (Semantic Scholar,
OpenAlex, etc.) as the source of record rather than this project's own
scrape-and-store pipeline — trade-off between control/reproducibility (current
approach) and maintenance burden (an outsourced approach). No action yet;
flagged for a later decision once the project's public-facing shape settles.
