# AV Atlas data release

Generated 2026-09-13 from <https://nightrome.github.io/av-atlas>.

## Files

| File | Rows | Contents |
| --- | --- | --- |
| `av-atlas-papers.csv.gz` | 25,664 | One row per AV-relevant paper. |
| `av-atlas-authorship.csv.gz` | 120,128 | Paper-to-author edges, with author position. |
| `av-atlas-citations.csv.gz` | 161,071 | In-corpus citation edges (citing -> cited). |
| `av-atlas-institutions.csv.gz` | 4,149 | Institutions with country, sector and totals. |

`paper_id` joins the tables. It is the paper's title lowercased with every
non-alphanumeric character removed -- the same key the pipeline dedupes on.
Many papers in this corpus have no DOI or arXiv id, so there is no external
identifier that covers all of them.

## What these numbers are, and are not

- **Citations are internal.** `in_corpus_citations` counts papers *in this
  corpus* that cite a paper, found by parsing reference lists. It is not a
  global citation count and is roughly an order of magnitude below Google
  Scholar's. It measures standing within AV research specifically.
- **Citation coverage is partial.** Reference lists have been parsed for some
  of the corpus, not all of it, so every citation count is a lower bound and
  the shortfall is not evenly distributed.
- **Affiliation coverage is partial.** `institutions` and `countries` are
  resolved for well under half the papers; rows with empty values are
  unresolved, not unaffiliated. Any institution- or country-level analysis
  describes that subset.
- **Author names are not disambiguated.** Names are matched as strings, so
  common names merge distinct people and spelling variants split one person.
- **Categories and AV-relevance are automated,** not manually reviewed. See
  the site's About page for the method and its measured accuracy.

Treat all of it as a well-documented estimate, not a verified count.

## Licence

Metadata in this release is published under CC BY-NC 4.0 (non-commercial
research and reference use). It is derived from bibliographic metadata
published by CVF, DBLP, OpenAlex, arXiv, Semantic Scholar, ecva.net, the
NeurIPS proceedings, PMLR and community-maintained venue listings, and
remains subject to those sources' own terms and to the rights of the papers'
original authors and publishers. For anything beyond non-commercial research
or reference use, go to the original source.

## Citing this

See `CITATION.cff` in the AV Atlas repository.
