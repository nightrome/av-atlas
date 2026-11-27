#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Packages the corpus as a downloadable, citable dataset under public/download/.

Until now the only way to get data out of AV Atlas was the per-table CSV
button on each page, which exports the rows currently on screen. That is fine
for a glance and useless for research: someone writing a survey, checking a
trend, or replicating a figure had no way to obtain the corpus, and the
pipeline that produces it needs weeks of crawling plus API keys to re-run.
The site asks to be treated as a research instrument, so it has to hand over
its data.

Writes gzipped CSVs, the text embeddings and a README, each named with the site version
(av-atlas-v0.1.2-papers.csv.gz and so on):

  papers.csv.gz        one row per AV paper (title, year, venue, category,
                       in-corpus citations, institutions, countries,
                       DOI/arXiv link)
  authorship.csv.gz    paper <-> author edges, so per-author analysis
                       doesn't require re-parsing an author column
  citations.csv.gz     the in-corpus citation edge list
  institutions.csv.gz  one row per institution, with its country
  authors.csv.gz       one row per author name (ORCIDs, institutions, totals)
  paper_versions.csv.gz  other titles/venues of merged preprints
  similar_papers.csv.gz  the 20 nearest papers of each paper by text embedding
  embeddings.npy + embeddings_paper_ids.csv.gz  the embedding matrix and its row ids
  README.md            what the columns mean and what they are not

The version is in the file name, not only in the README, so a browser or
proxy holding an old copy can never hand it out as the current one, and a
file someone downloaded says which release it came from. Only the current
version's files are kept in public/download/; deploy.py's sync removes the
old ones from gh-pages.

CSV rather than JSON because the audience is people who will open this in
pandas, R or a spreadsheet. Gzipped because the papers table is tens of MB
uncompressed and gh-pages serves these as static files.

Licensing: the metadata is published under CC BY-NC 4.0, the same terms the
About page already states, and remains subject to the original sources' own
terms -- see LICENSE-data at the repo root. Only AV-relevant papers are
exported; the full unfiltered proceedings pull is a means to an end here, not
something this site has any business redistributing wholesale.

Run by build_public_site.py as part of a normal build. Standalone usage:
    python build_data_release.py              # version from public/BUILD_INFO.json
    python build_data_release.py --version 0.1.2
"""
import argparse
import csv
import gzip
import io
import json
import re
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATS_FILE = BASE / "data" / "stats.json"
CITATION_GRAPH_FILE = BASE / "data" / "citation_graph.json"
OUT_DIR = BASE / "public" / "download"

PUBLIC_BUILD_INFO = BASE / "public" / "BUILD_INFO.json"

LICENSE_NAME = "CC BY-NC 4.0"
SITE_URL = "https://nightrome.github.io/av-atlas"


# The parts of the release, in the order the README and the homepage list them.
RELEASE_PARTS = ("papers.csv.gz", "authorship.csv.gz", "citations.csv.gz",
                 "institutions.csv.gz", "authors.csv.gz", "paper_versions.csv.gz",
                 "similar_papers.csv.gz", "embeddings.npy", "embeddings_paper_ids.csv.gz", "README.md")
EMBEDDINGS_DIR = BASE / "data" / "embeddings"
SIMILAR_DIR = BASE / "data" / "similar"
AUTHOR_DETAIL_DIR = BASE / "data" / "author_detail"


def release_file_name(version, part):
    """av-atlas-v0.1.2-papers.csv.gz for version '0.1.2' and part 'papers.csv.gz'."""
    return f"av-atlas-v{version}-{part}"


def release_file_names(version):
    return [release_file_name(version, part) for part in RELEASE_PARTS]


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def write_csv_gz(path, header, rows):
    """One gzip member holding one UTF-8 CSV.

    newline="" on the text wrapper is the documented requirement for csv;
    without it every record gets an extra blank line on Windows.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    n = 0
    for row in rows:
        writer.writerow(row)
        n += 1
    with gzip.open(path, "wb") as fh:
        fh.write(buf.getvalue().encode("utf-8"))
    print(f"  {path.name}: {n:,} rows ({path.stat().st_size / 1e6:.1f} MB gzipped)")
    return n


def read_author_detail(directory):
    """Every author's detail record from the sharded data/author_detail/ files ({} when absent)."""
    detail = {}
    if directory and Path(directory).exists():
        for shard in sorted(Path(directory).glob("shard-*.json")):
            detail.update(json.loads(shard.read_text(encoding="utf-8")))
    return detail


def write_authors(path, papers, author_detail):
    """One row per author name: paper and citation totals from the papers table, plus what the
    pipeline knows about the person (ORCIDs, institutions, countries, Scholar link, flags)."""
    author_detail = author_detail or {}
    totals = {}
    for p in papers:
        for a in dict.fromkeys(p.get("authors") or []):
            t = totals.setdefault(a, {"papers": 0, "citations": 0, "first": None, "last": None})
            t["papers"] += 1
            t["citations"] += p.get("citations") or 0
            y = p.get("year")
            if isinstance(y, int) or (isinstance(y, str) and y.isdigit()):
                y = int(y)
                t["first"] = y if t["first"] is None else min(t["first"], y)
                t["last"] = y if t["last"] is None else max(t["last"], y)

    def rows():
        for name in sorted(totals, key=lambda n: (-totals[n]["papers"], n)):
            t, d = totals[name], author_detail.get(name) or {}
            others = [o["orcid"] for o in (d.get("orcids") or []) if o.get("orcid") != d.get("orcid")]
            yield [name, d.get("orcid") or "", "; ".join(others), t["papers"], t["citations"],
                   t["first"] or "", t["last"] or "",
                   "; ".join(i["name"] for i in d.get("institutions") or []),
                   "; ".join(d.get("countries") or []), d.get("scholar_url") or "",
                   int(bool(d.get("ambiguous"))), int(bool(d.get("identity_conflict")))]
    return write_csv_gz(
        path, ["author_name", "orcid", "other_orcids", "papers", "in_corpus_citations", "first_year", "last_year",
               "institutions", "countries", "scholar_url", "ambiguous_name", "identity_conflict"], rows())


def similar_rows(similar_dir, paper_ids):
    """(paper_id, rank, similar_paper_id, similarity) from data/similar/, for papers in the table."""
    if not similar_dir or not Path(similar_dir).exists():
        return
    for shard in sorted(Path(similar_dir).glob("shard-*.json")):
        for title, neighbours in json.loads(shard.read_text(encoding="utf-8")).items():
            pid = normalize_title(title)
            if pid not in paper_ids:
                continue
            rank = 0
            for other, score in neighbours:
                oid = normalize_title(other)
                if oid in paper_ids:
                    rank += 1
                    yield [pid, rank, oid, score]


def empty_npy(columns):
    """A valid .npy file for an int8 array of shape (0, columns), written without numpy."""
    header = "{'descr': '|i1', 'fortran_order': False, 'shape': (0, %d), }" % columns
    header += " " * (63 - (10 + len(header)) % 64) + chr(10)
    return bytes([0x93]) + b"NUMPY" + bytes([1, 0]) + len(header).to_bytes(2, "little") + header.encode("latin1")


def write_embeddings(npy_path, ids_path, embeddings_dir):
    """The int8 embedding matrix (copied as is) and, row by row, the paper it belongs to."""
    vectors = Path(embeddings_dir) / "int8_512.npy" if embeddings_dir else None
    keys = Path(embeddings_dir) / "keys.json" if embeddings_dir else None
    if vectors and keys and vectors.exists() and keys.exists():
        npy_path.write_bytes(vectors.read_bytes())
        titles = json.loads(keys.read_text(encoding="utf-8"))
    else:
        npy_path.write_bytes(empty_npy(512))
        titles = []
    print(f"  {npy_path.name}: {len(titles):,} vectors ({npy_path.stat().st_size / 1e6:.1f} MB)")
    return write_csv_gz(ids_path, ["row", "paper_id", "title"],
                        ([i, normalize_title(t), t] for i, t in enumerate(titles)))


def build(stats, citation_graph, version, out_dir=None, author_detail=None, similar_dir=None,
          embeddings_dir=None):
    """The optional inputs default to the repo's own data/ folders when called from the build; tests pass
    nothing and get empty versions of those parts."""
    out_dir = Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = lambda part: out_dir / release_file_name(version, part)  # noqa: E731
    papers = stats.get("all_papers") or []

    # A stable id per paper. The corpus has no external identifier for every
    # paper (many venue-listing entries carry neither a DOI nor an arXiv id),
    # and the pipeline itself dedupes on normalized title, so that is the
    # only key that is actually total here. Exported explicitly rather than
    # left for a consumer to re-derive and get subtly wrong.
    paper_ids = {}
    for p in papers:
        key = normalize_title(p.get("title"))
        if key:
            paper_ids.setdefault(key, key)

    n_papers = write_csv_gz(
        name("papers.csv.gz"),
        ["paper_id", "title", "year", "venue", "category", "av_relevance",
         "in_corpus_citations", "doi", "arxiv_url", "source", "institutions", "countries"],
        ([normalize_title(p.get("title")), p.get("title"), p.get("year"), p.get("venue"),
          p.get("category"), p.get("av_relevance") or "AV", p.get("citations"),
          p.get("doi") or "", p.get("arxiv_url") or "", p.get("source") or "",
          "; ".join(p.get("institutions") or []),
          "; ".join(p.get("countries") or [])]
         for p in papers if normalize_title(p.get("title"))))

    n_authorship = write_csv_gz(
        name("authorship.csv.gz"),
        ["paper_id", "author_name", "author_position"],
        ((normalize_title(p.get("title")), name, i + 1)
         for p in papers if normalize_title(p.get("title"))
         for i, name in enumerate(p.get("authors") or [])))

    # Citation edges, restricted to pairs where BOTH ends are in the papers
    # table above -- an edge pointing at an id the download doesn't contain
    # is not usable by anyone.
    def edges():
        for citer, cited_list in (citation_graph.get("edges") or {}).items():
            if citer not in paper_ids:
                continue
            for cited in cited_list:
                if cited in paper_ids:
                    yield citer, cited

    n_edges = write_csv_gz(
        name("citations.csv.gz"),
        ["citing_paper_id", "cited_paper_id"], edges())

    # Every institution in the corpus, not stats.json's top_institutions --
    # that list is capped at 100 for the leaderboard, which would have made
    # this table a top-100 extract rather than a release. Recomputed from the
    # papers table so it covers the same set the papers file does.
    inst_countries = stats.get("institution_countries") or {}
    inst_sector = stats.get("institution_sector") or {}
    inst_papers, inst_citations = {}, {}
    for p in papers:
        for inst in set(p.get("institutions") or []):
            inst_papers[inst] = inst_papers.get(inst, 0) + 1
            inst_citations[inst] = inst_citations.get(inst, 0) + (p.get("citations") or 0)
    n_inst = write_csv_gz(
        name("institutions.csv.gz"),
        ["institution", "country", "sector", "papers", "citations"],
        ([name, inst_countries.get(name, ""), inst_sector.get(name, ""),
          inst_papers[name], inst_citations[name]]
         for name in sorted(inst_papers, key=lambda k: -inst_papers[k])))

    n_authors = write_authors(name("authors.csv.gz"), papers, author_detail)
    n_versions = write_csv_gz(
        name("paper_versions.csv.gz"),
        ["paper_id", "version_title", "version_venue", "version_year", "version_url"],
        ((normalize_title(p.get("title")), v.get("title") or "", v.get("venue") or "", v.get("year") or "",
          v.get("url") or "")
         for p in papers if normalize_title(p.get("title"))
         for v in (p.get("also_published_as") or [])))
    n_similar = write_csv_gz(
        name("similar_papers.csv.gz"),
        ["paper_id", "rank", "similar_paper_id", "similarity"],
        similar_rows(similar_dir, paper_ids))
    n_embedded = write_embeddings(name("embeddings.npy"), name("embeddings_paper_ids.csv.gz"), embeddings_dir)

    readme = name("README.md")
    readme.write_text(README_TEMPLATE.format(
        n_authors=f"{n_authors:,}", n_versions=f"{n_versions:,}", n_similar=f"{n_similar:,}",
        n_embedded=f"{n_embedded:,}",
        authors=release_file_name(version, "authors.csv.gz"),
        versions=release_file_name(version, "paper_versions.csv.gz"),
        similar=release_file_name(version, "similar_papers.csv.gz"),
        embeddings=release_file_name(version, "embeddings.npy"),
        embedding_ids=release_file_name(version, "embeddings_paper_ids.csv.gz"),
        date=date.today().isoformat(), n_papers=f"{n_papers:,}",
        n_authorship=f"{n_authorship:,}", n_edges=f"{n_edges:,}", n_inst=f"{n_inst:,}",
        license=LICENSE_NAME, site=SITE_URL, version=version,
        papers=release_file_name(version, "papers.csv.gz"),
        authorship=release_file_name(version, "authorship.csv.gz"),
        citations=release_file_name(version, "citations.csv.gz"),
        institutions=release_file_name(version, "institutions.csv.gz"),
    ), encoding="utf-8", newline="\n")
    print(f"  {readme.name}")

    # public/ persists between builds, so the previous release's files would
    # otherwise sit next to this one forever. Only the current version ships.
    current = set(release_file_names(version))
    for existing in out_dir.iterdir():
        if existing.is_file() and existing.name not in current:
            existing.unlink()
            print(f"  removed old {existing.name}")


README_TEMPLATE = """# AV Atlas data release

Version {version}, generated {date} from <{site}>.

## Files

| File | Rows | Contents |
| --- | --- | --- |
| `{papers}` | {n_papers} | One row per AV-relevant paper. |
| `{authorship}` | {n_authorship} | Paper-to-author edges, with author position. |
| `{citations}` | {n_edges} | In-corpus citation edges (citing -> cited). |
| `{institutions}` | {n_inst} | Institutions with country, sector and totals. |
| `{authors}` | {n_authors} | One row per author name: ORCIDs, institutions, countries, totals. |
| `{versions}` | {n_versions} | Other titles and venues of merged papers (preprint versions). |
| `{similar}` | {n_similar} | The 20 most similar papers of each paper, with a similarity score. |
| `{embeddings}` | {n_embedded} | Text embedding of each paper (int8 matrix, 512 columns). |
| `{embedding_ids}` | {n_embedded} | Which paper each row of the embedding matrix belongs to. |

`paper_id` joins the tables. It is the paper's title lowercased with every
non-alphanumeric character removed -- the same key the pipeline dedupes on.
Many papers in this corpus have no DOI or arXiv id, so there is no external
identifier that covers all of them.

## Columns

`{papers}`

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

`{authorship}`

| Column | Meaning |
| --- | --- |
| `paper_id` | The paper. |
| `author_name` | Author as spelled in the corpus. A few very common names are split into separate people where this could be checked; the rest of a split name appears as `Name (unidentified)`. |
| `author_position` | 1 for the first author, 2 for the second, and so on. |

`{citations}`

| Column | Meaning |
| --- | --- |
| `citing_paper_id` | The paper whose reference list contains the other one. |
| `cited_paper_id` | The paper being cited. Both ends are in the papers table. |

`{institutions}`

| Column | Meaning |
| --- | --- |
| `institution` | Institution name after merging spelling variants. |
| `country` | Country of the institution, empty when unknown. |
| `sector` | `academic` or `industry` where classified, otherwise empty (most rows). |
| `papers` | Papers in the papers table with an author at this institution. |
| `citations` | Sum of `in_corpus_citations` of those papers. |

`{authors}`

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

`{versions}`: `paper_id` is the published paper; `version_title`, `version_venue`, `version_year`
and `version_url` describe another version of the same work (usually its arXiv preprint).

`{similar}`: for each `paper_id`, its `rank` (1 is closest) among the 20 nearest papers
`similar_paper_id`, with the cosine `similarity` of their embeddings (0 unrelated, 1 identical).

`{embeddings}` and `{embedding_ids}`: row `row` of the matrix is the embedding of the paper with that
`paper_id`. The matrix also has a row for each preprint that was merged into a published paper;
their `paper_id` is not in the papers table (see `{versions}`). The embeddings are made from title and abstract by the Qwen3-Embedding-8B model, cut to
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

Metadata in this release is published under {license} (non-commercial
research and reference use). It is derived from bibliographic metadata
published by CVF, DBLP, OpenAlex, arXiv, Semantic Scholar, ecva.net, the
NeurIPS proceedings, PMLR and community-maintained venue listings, and
remains subject to those sources' own terms and to the rights of the papers'
original authors and publishers. For anything beyond non-commercial research
or reference use, go to the original source.

## Citing this

See `CITATION.cff` in the AV Atlas repository, and give the version above
({version}) so others can tell which release you used. Every build of the
site gets a new version, and the numbers change between them.
"""


def main():
    parser = argparse.ArgumentParser(description="Build the downloadable data release.")
    parser.add_argument("--version", dest="site_version", metavar="X.Y.Z",
                        help="Version to name the files with (default: the one in "
                             "public/BUILD_INFO.json, written by build_public_site.py).")
    args = parser.parse_args()
    version = args.site_version
    if not version:
        try:
            version = json.loads(PUBLIC_BUILD_INFO.read_text(encoding="utf-8"))["version"]
        except (OSError, ValueError, KeyError):
            raise SystemExit(f"No version in {PUBLIC_BUILD_INFO} -- pass --version, "
                             "or run build_public_site.py first")
    if not STATS_FILE.exists():
        raise SystemExit(f"{STATS_FILE} not found -- run aggregate.py first")
    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    graph = (json.loads(CITATION_GRAPH_FILE.read_text(encoding="utf-8"))
             if CITATION_GRAPH_FILE.exists() else {})
    build(stats, graph, version, author_detail=read_author_detail(AUTHOR_DETAIL_DIR),
          similar_dir=SIMILAR_DIR, embeddings_dir=EMBEDDINGS_DIR)


if __name__ == "__main__":
    main()
