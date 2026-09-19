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

Writes four gzipped CSVs plus a README:

  av-atlas-papers.csv.gz       one row per AV paper (title, year, venue,
                               category, in-corpus citations, institutions,
                               countries, DOI/arXiv link)
  av-atlas-authorship.csv.gz   paper <-> author edges, so per-author analysis
                               doesn't require re-parsing an author column
  av-atlas-citations.csv.gz    the in-corpus citation edge list
  av-atlas-institutions.csv.gz one row per institution, with its country

CSV rather than JSON because the audience is people who will open this in
pandas, R or a spreadsheet. Gzipped because the papers table is tens of MB
uncompressed and gh-pages serves these as static files.

Licensing: the metadata is published under CC BY-NC 4.0, the same terms the
About page already states, and remains subject to the original sources' own
terms -- see LICENSE-data at the repo root. Only AV-relevant papers are
exported; the full unfiltered proceedings pull is a means to an end here, not
something this site has any business redistributing wholesale.

Run by build_public_site.py as part of a normal build. Standalone usage:
    python build_data_release.py
"""
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

LICENSE_NAME = "CC BY-NC 4.0"
SITE_URL = "https://nightrome.github.io/av-atlas"


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


def build(stats, citation_graph):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
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
        OUT_DIR / "av-atlas-papers.csv.gz",
        ["paper_id", "title", "year", "venue", "category", "av_relevance",
         "in_corpus_citations", "doi", "institutions", "countries"],
        ([normalize_title(p.get("title")), p.get("title"), p.get("year"), p.get("venue"),
          p.get("category"), p.get("av_relevance") or "AV", p.get("citations"),
          p.get("doi") or "",
          "; ".join(p.get("institutions") or []),
          "; ".join(p.get("countries") or [])]
         for p in papers if normalize_title(p.get("title"))))

    n_authorship = write_csv_gz(
        OUT_DIR / "av-atlas-authorship.csv.gz",
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
        OUT_DIR / "av-atlas-citations.csv.gz",
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
        OUT_DIR / "av-atlas-institutions.csv.gz",
        ["institution", "country", "sector", "papers", "citations"],
        ([name, inst_countries.get(name, ""), inst_sector.get(name, ""),
          inst_papers[name], inst_citations[name]]
         for name in sorted(inst_papers, key=lambda k: -inst_papers[k])))

    (OUT_DIR / "README.md").write_text(README_TEMPLATE.format(
        date=date.today().isoformat(), n_papers=f"{n_papers:,}",
        n_authorship=f"{n_authorship:,}", n_edges=f"{n_edges:,}", n_inst=f"{n_inst:,}",
        license=LICENSE_NAME, site=SITE_URL,
    ), encoding="utf-8", newline="\n")
    print(f"  README.md")


README_TEMPLATE = """# AV Atlas data release

Generated {date} from <{site}>.

## Files

| File | Rows | Contents |
| --- | --- | --- |
| `av-atlas-papers.csv.gz` | {n_papers} | One row per AV-relevant paper. |
| `av-atlas-authorship.csv.gz` | {n_authorship} | Paper-to-author edges, with author position. |
| `av-atlas-citations.csv.gz` | {n_edges} | In-corpus citation edges (citing -> cited). |
| `av-atlas-institutions.csv.gz` | {n_inst} | Institutions with country, sector and totals. |

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

Metadata in this release is published under {license} (non-commercial
research and reference use). It is derived from bibliographic metadata
published by CVF, DBLP, OpenAlex, arXiv, Semantic Scholar, ecva.net, the
NeurIPS proceedings, PMLR and community-maintained venue listings, and
remains subject to those sources' own terms and to the rights of the papers'
original authors and publishers. For anything beyond non-commercial research
or reference use, go to the original source.

## Citing this

See `CITATION.cff` in the AV Atlas repository.
"""


def main():
    if not STATS_FILE.exists():
        raise SystemExit(f"{STATS_FILE} not found -- run aggregate.py first")
    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    graph = (json.loads(CITATION_GRAPH_FILE.read_text(encoding="utf-8"))
             if CITATION_GRAPH_FILE.exists() else {})
    build(stats, graph)


if __name__ == "__main__":
    main()
