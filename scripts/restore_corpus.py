#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Restores data/papers_full.json and data/citation_graph.json from the draft
GitHub Release scripts/backup_corpus.py maintains -- the fast path to a
working corpus on a fresh machine, instead of re-crawling for weeks (see
DECISIONS.md's "Derived data is not tracked" entry for why those two files
in particular can't just be regenerated from what's in git).

Run this BEFORE scripts/build_public_site.py on a new checkout: merge_corpus.py
carries enrichment (author affiliations, citation counts, abstracts) forward
from whatever papers_full.json already exists, so restoring it first is what
makes the rebuild fast and complete rather than fast and empty.

Requires GITHUB_TOKEN in av-atlas/.env (gitignored) with read access to this
repo -- the backup release is a draft, so its asset isn't downloadable
anonymously the way a normal release's would be. Same token as
backup_corpus.py; see .env.example.

Usage: python restore_corpus.py
"""
import gzip
import json
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
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
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE} -- add a line GITHUB_TOKEN=... (see .env.example), "
                          "a token with read access to this repo is enough")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_TOKEN="):
            token = line.split("=", 1)[1].strip()
            if token:
                return token
    raise SystemExit(f"GITHUB_TOKEN not found in {ENV_FILE}")


def api_get(url, token, headers=None):
    req = urllib.request.Request(url, headers={**HEADERS_BASE, "Authorization": f"Bearer {token}", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, resp.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stops urllib from auto-following the asset download's redirect.

    GitHub's asset-download endpoint (hit with Accept: application/octet-
    stream) 302s to a presigned, self-authenticating storage URL. Auto-
    following would resend our Authorization header to that URL -- which
    GitHub's own API docs warn against ("configure your HTTP client not to
    forward the Authorization header on redirect") since a presigned URL
    plus a second, unrelated auth scheme on top of it can be rejected by
    the storage backend as a double-auth request rather than accepted or
    just ignored. Fetching the redirect ourselves, deliberately without
    that header, is what the docs actually say to do.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_asset(asset_api_url, token):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(asset_api_url, headers={
        **HEADERS_BASE, "Authorization": f"Bearer {token}", "Accept": "application/octet-stream",
    })
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.read()  # small/no-redirect case (e.g. a test server)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        location = e.headers.get("Location")
        if not location:
            raise SystemExit(f"Asset download redirected ({e.code}) with no Location header")
        with urllib.request.urlopen(location, timeout=120) as resp:  # deliberately no auth header, see _NoRedirect
            return resp.read()


def find_asset(token):
    status, body = api_get(f"{API_BASE}/releases/tags/{BACKUP_TAG}", token)
    release = json.loads(body)
    for asset in release.get("assets", []):
        if asset["name"] == ARCHIVE_NAME:
            return asset
    raise SystemExit(f"No '{ARCHIVE_NAME}' asset on the '{BACKUP_TAG}' release -- "
                      "has scripts/backup_corpus.py ever run successfully?")


def extract_archive(archive_bytes):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(fileobj=BytesIO(archive_bytes), mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r") as tar:
            names = tar.getnames()
            # extractall's default filter changed across Python versions and
            # a bare call warns/behaves differently depending on which one's
            # running -- "data" is the standard library's own recommended
            # safe default (no path traversal, no device files) and pins the
            # behavior explicitly instead of depending on interpreter version.
            tar.extractall(DATA_DIR, filter="data")
    return names


def main():
    token = load_github_token()
    print(f"Looking up the '{BACKUP_TAG}' release...")
    asset = find_asset(token)
    print(f"Downloading {asset['name']} ({asset['size'] / 1e6:.1f} MB)...")
    archive_bytes = download_asset(asset["url"], token)
    if len(archive_bytes) != asset["size"]:
        raise SystemExit(f"Downloaded {len(archive_bytes)} bytes, expected {asset['size']} -- try again")
    names = extract_archive(archive_bytes)
    print(f"Restored into {DATA_DIR}: {', '.join(names)}")
    print("Now run: python build_public_site.py")


if __name__ == "__main__":
    main()
