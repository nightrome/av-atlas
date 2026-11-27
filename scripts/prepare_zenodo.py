#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prepares the Zenodo deposit for the current data release. It does NOT upload
anything: it copies the files in public/download/ into dist/zenodo/ next to a
zenodo_metadata.json (title, description, license, keywords, links) that can be
pasted into Zenodo's upload form or sent to its API.

Run it after a build, so public/download/ holds the current release:

    python scripts/prepare_zenodo.py

Then, when the release is meant to go out: create a new upload on zenodo.org,
add the files from dist/zenodo/, fill the form from zenodo_metadata.json, and
publish. Zenodo then gives the release a DOI; add it to CITATION.cff and to
the About page. Publishing is permanent, so check the files first.
"""
import json
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "dist" / "zenodo"


def metadata(version):
    return {
        "metadata": {
            "title": f"AV Atlas data release v{version}: autonomous vehicle research papers, authors and institutions",
            "upload_type": "dataset",
            "description": (
                "<p>The corpus behind AV Atlas (https://nightrome.github.io/av-atlas/): autonomous-vehicle "
                "papers from about 20 computer vision and robotics venues, with authors, institutions, "
                "countries, and citations counted between papers in the corpus. Gzipped CSV files (papers, authors, authorship, "
                "citations, institutions, other paper versions, similar papers), the text embeddings of the papers and a "
                "README that lists every column and explains what the data is not. Abstracts are not included.</p>"
                "<p>Institutions and countries are read from paper pages and PDFs and are incomplete and "
                "approximate; author names are not disambiguated. The paper metadata remains subject to the "
                "terms of its original sources.</p>"),
            "creators": [{"name": "Caesar, Holger"}],
            "version": version,
            "access_right": "open",
            "license": "cc-by-nc-4.0",
            "keywords": ["autonomous vehicles", "self-driving cars", "bibliometrics", "science of science",
                         "computer vision", "robotics"],
            "related_identifiers": [
                {"relation": "isSupplementTo", "identifier": "https://github.com/nightrome/av-atlas",
                 "resource_type": "software"},
                {"relation": "isDocumentedBy", "identifier": "https://nightrome.github.io/av-atlas/about.html",
                 "resource_type": "other"},
            ],
        }
    }


def main():
    info = json.loads((BASE / "public" / "BUILD_INFO.json").read_text(encoding="utf-8"))
    version = info["version"]
    files = sorted((BASE / "public" / "download").glob(f"av-atlas-v{version}-*"))
    if not files:
        sys.exit(f"No files for v{version} in public/download/. Build the site first.")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for f in files:
        shutil.copy2(f, OUT / f.name)
    (OUT / "zenodo_metadata.json").write_text(json.dumps(metadata(version), indent=2, ensure_ascii=False),
                                              encoding="utf-8")
    print(f"v{version}: {len(files)} files and zenodo_metadata.json in {OUT}. Nothing was uploaded.")


if __name__ == "__main__":
    main()
