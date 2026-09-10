#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backs up the two large, gitignored, NOT-actually-regenerable derived files
-- data/papers_full.json and data/citation_graph.json -- to a single draft
GitHub Release on this repo, so a data-loss on the machine that runs the
real pipeline doesn't mean re-crawling for weeks.

Why these two files specifically, and why they need a real backup: see
DECISIONS.md's "Derived data is not tracked" entry. Short version --
merge_corpus.py only carries author affiliations, citation counts, and
abstracts *forward* from an existing papers_full.json; it doesn't re-derive
them. citation_graph.json is a second, independent gap on top of that
(aggregate.py reads its edges directly for the disruption index and the
citation-graph coverage table). Neither survives a machine loss today.

Why a GitHub Release and not a git commit: a release asset is stored
separately from the git object database -- it's never fetched by `git
clone`, never appears in a diff, and isn't subject to the ~100MB per-file
push limit that made papers_full.json gitignored in the first place (see
.gitignore's comment on it). Draft (not published) so the asset is visible
only to repo collaborators despite the repo itself being public.

One fixed tag, one asset, always replaced -- never a new release per
backup -- so this can run after every real deploy without accumulating
old snapshots on GitHub's storage forever.

Requires GITHUB_TOKEN in av-atlas/.env (gitignored, never commit a real
one) with "contents: read and write" on this repo -- a fine-grained
personal access token scoped to nightrome/av-atlas is enough. Skips
silently (prints a note, exits 0) when no token is configured, so a
checkout without one doesn't fail scripts/deploy.py over an optional step.

Usage: python backup_corpus.py
"""
import gzip
import io
import json
import re
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
CITATION_GRAPH_FILE = BASE / "data" / "citation_graph.json"
ENV_FILE = BASE / ".env"

REPO_OWNER = "nightrome"
REPO_NAME = "av-atlas"
API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
BACKUP_TAG = "corpus-backup"
ARCHIVE_NAME = "av-atlas-corpus.tar.gz"
HEADERS_BASE = {
    "User-Agent": "av-atlas-backup (mailto:holger@it-caesar.com)",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def load_github_token():
    """None (not a hard error) when unset -- see module docstring: this
    step is optional, not a pipeline requirement."""
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_TOKEN="):
            token = line.split("=", 1)[1].strip()
            return token or None
    return None


def build_archive():
    """One gzip member holding a tar of exactly the two files that matter
    -- not the smaller side-files (reference lists, raw affiliation
    scrapes, ...), since those exist only to feed these two and a restore
    is about getting back to a deployable state fast, not resuming a
    crawl. See DECISIONS.md."""
    if not PAPERS_FILE.exists():
        raise SystemExit(f"{PAPERS_FILE} not found -- nothing to back up")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:  # mtime=0: reproducible bytes for the same input
        with tarfile.open(fileobj=gz, mode="w") as tar:
            tar.add(PAPERS_FILE, arcname="papers_full.json")
            if CITATION_GRAPH_FILE.exists():
                tar.add(CITATION_GRAPH_FILE, arcname="citation_graph.json")
            else:
                print(f"  note: {CITATION_GRAPH_FILE} not found, backing up papers_full.json only")
    return buf.getvalue()


def api_request(method, url, token, data=None, headers=None):
    req = urllib.request.Request(url, method=method, data=data,
                                  headers={**HEADERS_BASE, "Authorization": f"Bearer {token}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get_or_create_release(token):
    status, body = api_request("GET", f"{API_BASE}/releases/tags/{BACKUP_TAG}", token)
    if status == 200:
        return body
    if status != 404:
        raise SystemExit(f"GET release failed ({status}): {body}")
    payload = json.dumps({
        "tag_name": BACKUP_TAG,
        "name": "Corpus data backup (not a real release, do not download for site use)",
        "body": "Auto-updated snapshot of data/papers_full.json + data/citation_graph.json for "
                "scripts/restore_corpus.py. Overwritten on every backup_corpus.py run -- see DECISIONS.md.",
        "draft": True,
    }).encode("utf-8")
    status, body = api_request("POST", f"{API_BASE}/releases", token, data=payload,
                                headers={"Content-Type": "application/json"})
    if status != 201:
        raise SystemExit(f"Creating the {BACKUP_TAG} release failed ({status}): {body}")
    print(f"  created draft release '{BACKUP_TAG}'")
    return body


def delete_existing_asset(release, token):
    # GitHub has no "replace this asset in place" call -- same name, delete
    # then re-upload is the documented way to avoid accumulating one asset
    # per backup run forever.
    for asset in release.get("assets", []):
        if asset["name"] == ARCHIVE_NAME:
            status, body = api_request("DELETE", asset["url"], token)
            if status != 204:
                raise SystemExit(f"Deleting the old {ARCHIVE_NAME} asset failed ({status}): {body}")
            print(f"  removed previous {ARCHIVE_NAME} ({asset['size'] / 1e6:.1f} MB)")
            return


def upload_asset(release, token, archive_bytes):
    # upload_url is a URI template ("...assets{?name,label}") -- the {?...}
    # suffix is for URI-template expansion, not a literal query string.
    upload_url = re.sub(r"\{.*\}$", "", release["upload_url"])
    status, body = api_request(
        "POST", f"{upload_url}?name={ARCHIVE_NAME}", token,
        data=archive_bytes, headers={"Content-Type": "application/gzip"})
    if status != 201:
        raise SystemExit(f"Uploading {ARCHIVE_NAME} failed ({status}): {body}")
    print(f"  uploaded {ARCHIVE_NAME}: {len(archive_bytes) / 1e6:.1f} MB")


def main():
    token = load_github_token()
    if not token:
        print("backup_corpus.py: no GITHUB_TOKEN in .env, skipping corpus backup (see .env.example)")
        return
    print("Packing data/papers_full.json + data/citation_graph.json...")
    archive_bytes = build_archive()
    release = get_or_create_release(token)
    delete_existing_asset(release, token)
    upload_asset(release, token, archive_bytes)
    print(f"Backed up to https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/tag/{BACKUP_TAG} "
          f"(draft, visible to collaborators only)")


if __name__ == "__main__":
    main()
