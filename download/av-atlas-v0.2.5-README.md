# AV Atlas data release

Version 0.2.5, generated 2026-11-28 from <https://nightrome.github.io/av-atlas>.

## Files

| File | Rows | Contents |
| --- | --- | --- |
| `av-atlas-v0.2.5-papers.csv.gz` | 27,488 | One row per AV-relevant paper. |
| `av-atlas-v0.2.5-authorship.csv.gz` | 130,985 | Paper-to-author edges, with author position. |
| `av-atlas-v0.2.5-citations.csv.gz` | 152,788 | In-corpus citation edges (citing -> cited). |
| `av-atlas-v0.2.5-institutions.csv.gz` | 5,020 | Institutions with country, sector and totals. |
| `av-atlas-v0.2.5-authors.csv.gz` | 52,155 | One row per author name: ORCIDs, institutions, countries, totals. |
| `av-atlas-v0.2.5-paper_versions.csv.gz` | 166 | Other titles and venues of merged papers (preprint versions). |
| `av-atlas-v0.2.5-similar_papers.csv.gz` | 545,602 | The 20 most similar papers of each paper, with a similarity score. |
| `av-atlas-v0.2.5-embeddings.npy` | 27,653 | Text embedding of each paper (int8 matrix, 512 columns). |
| `av-atlas-v0.2.5-embeddings_paper_ids.csv.gz` | 27,653 | Which paper each row of the embedding matrix belongs to. |

`paper_id` joins the tables. It is the paper's title lowercased with every
non-alphanumeric character removed -- the same key the pipeline dedupes on.
Many papers in this corpus have no DOI or arXiv id, so there is no external
identifier that covers all of them.

## Columns

`av-atlas-v0.2.5-papers.csv.gz`

| Column | Meaning |
| --- | --- |
| `paper_id` | Key used by all tables (see above). |
| `title` | Paper title as listed by its venue. |
| `year` | Publication year of the version in the corpus. |
| `venue` | Short venue name (for example CVPR, ICRA, T-ITS) or `arXiv` for preprints. |
| `category` | One topic label from a fixed list of 35, assigned automatically from title and abstract. |
| `av_relevance` | `AV` for every row: only papers judged to be about autonomous road vehicles are included. |
| `in_corpus_citations` | Papers in this corpus that cite this paper (see below). |
| `doi` | DOI where known, otherwise empty. |
| `arxiv_url` | arXiv link where known, otherwise empty. |
| `source` | How the paper entered the corpus: `venue_listing` (the venue's own proceedings), `arxiv_author_pull`, `arxiv_s2_citing_discovery` or `arxiv_monthly_intake`. |
| `institutions` | Institutions of the authors, separated by `; `. Empty when unresolved. |
| `countries` | Countries of those institutions, separated by `; `. Empty when unresolved. |

`av-atlas-v0.2.5-authorship.csv.gz`

| Column | Meaning |
| --- | --- |
| `paper_id` | The paper. |
| `author_name` | Author as spelled in the corpus. A few very common names are split into separate people where this could be checked; the rest of a split name appears as `Name (unidentified)`. |
| `author_position` | 1 for the first author, 2 for the second, and so on. |

`av-atlas-v0.2.5-citations.csv.gz`

| Column | Meaning |
| --- | --- |
| `citing_paper_id` | The paper whose reference list contains the other one. |
| `cited_paper_id` | The paper being cited. Both ends are in the papers table. |

`av-atlas-v0.2.5-institutions.csv.gz`

| Column | Meaning |
| --- | --- |
| `institution` | Institution name after merging spelling variants. |
| `country` | Country of the institution, empty when unknown. |
| `sector` | `academic` or `industry` where classified, otherwise empty (most rows). |
| `papers` | Papers in the papers table with an author at this institution. |
| `citations` | Sum of `in_corpus_citations` of those papers. |

`av-atlas-v0.2.5-authors.csv.gz`

| Column | Meaning |
| --- | --- |
| `author_name` | The name as in the authorship table (the key; names are strings, see below). |
| `orcid` | The main ORCID, the one found on most of the author's papers. Empty when none is known or the name is shared by several people. |
| `other_orcids` | Further ORCIDs found for the same author (people sometimes register twice), separated by `; `. |
| `papers`, `in_corpus_citations` | Papers in the papers table with this author and the sum of their in-corpus citations. |
| `first_year`, `last_year` | Years of the author's first and last paper in the corpus. |
| `institutions`, `countries` | Affiliations seen on their papers, earliest first, separated by `; `. |
| `scholar_url` | Google Scholar profile where one was matched by hand, otherwise empty. |
| `ambiguous_name` | 1 when a hand check found several different people behind the name. |
| `identity_conflict` | 1 when the data sources disagree on whether the name is one person. |

`av-atlas-v0.2.5-paper_versions.csv.gz`: `paper_id` is the published paper; `version_title`, `version_venue`, `version_year`
and `version_url` describe another version of the same work (usually its arXiv preprint).

`av-atlas-v0.2.5-similar_papers.csv.gz`: for each `paper_id`, its `rank` (1 is closest) among the 20 nearest papers
`similar_paper_id`, with the cosine `similarity` of their embeddings (0 unrelated, 1 identical).

`av-atlas-v0.2.5-embeddings.npy` and `av-atlas-v0.2.5-embeddings_paper_ids.csv.gz`: row `row` of the matrix is the embedding of the paper with that
`paper_id`. The matrix also has a row for each preprint that was merged into a published paper;
their `paper_id` is not in the papers table (see `av-atlas-v0.2.5-paper_versions.csv.gz`). The embeddings are made from title and abstract by the Qwen3-Embedding-8B model, cut to
their first 512 dimensions and stored as int8 with one global scale, which does not matter for cosine
similarity. Load with `numpy.load`. The abstracts themselves are not part of this release.

## What these numbers are, and are not

- **Preprints are merged into published versions.** When the same work exists as an
  arXiv preprint and a published paper under different titles, the preprint is
  folded into the published one and its citations count for it. The preprint's
  title is not in these files; the site shows it as "Also appeared as".

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
- **Author names are mostly not disambiguated.** Names are matched as strings, so
  common names merge distinct people and spelling variants split one person. Only a
  few very common names have been split by hand.
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

See `CITATION.cff` in the AV Atlas repository, and give the version above
(0.2.5) so others can tell which release you used. Every build of the
site gets a new version, and the numbers change between them.
