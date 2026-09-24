#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Restores the gitignored corpus files (papers_full.json,
venues/arxiv_s2_citing.json, citation_graph.json and the crawlers' resume
files -- the list is backup_corpus.py's BACKUP_FILES) from the draft GitHub
Release scripts/backup_corpus.py maintains. It's the fast path to a working
corpus on a fresh machine or in the monthly GitHub Actions job, instead of
re-crawling for weeks (see DECISIONS.md's "Derived data is not tracked"
entry for why those files can't just be regenerated from what's in git).

Run this BEFORE scripts/build_public_site.py on a new checkout: merge_corpus.py
carries enrichment (author affiliations, citation counts, abstracts) forward
from whatever papers_full.json already exists, and builds about 45% of the
AV papers from venues/arxiv_s2_citing.json, so restoring first is what makes
the rebuild complete rather than fast and half-empty. It overwrites those
files in data/; each one is swapped in whole, so an interrupted restore
never leaves a half-written file.

Also records which backup asset it restored (data/corpus_backup_state.json),
so a later backup_corpus.py run from this checkout can tell whether someone
else has backed up in between.

Requires GITHUB_TOKEN (environment variable, or a line in av-atlas/.env,
which is gitignored) with read access to this repo -- the backup release is
a draft, so its asset isn't downloadable anonymously the way a normal
release's would be. Same token as backup_corpus.py; see .env.example.

Usage: python restore_corpus.py
"""
import gzip
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import backup_corpus as bc

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
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
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


def select_latest_release_by_tag(releases, tag):
    """Pure selection logic, testable without a live API call -- see
    backup_corpus.py's select_releases_by_tag for why this can't just be
    a GET .../releases/tags/{tag} call."""
    matches = [r for r in releases if r.get("tag_name") == tag]
    if not matches:
        return None
    matches.sort(key=lambda r: r["created_at"], reverse=True)
    return matches[0]


def find_asset(token):
    # GitHub's "get release by tag" endpoint (/releases/tags/{tag}) only
    # resolves *published* releases -- the backup release is a draft (no
    # real tag ref), so it 404s there even when the release exists. Listing
    # and filtering client-side is the only way to find a draft by tag; see
    # the matching note in backup_corpus.py's select_releases_by_tag.
    status, body = api_get(f"{API_BASE}/releases?per_page=100", token)
    releases = json.loads(body)
    release = select_latest_release_by_tag(releases, BACKUP_TAG)
    if release is None:
        raise SystemExit(f"No '{BACKUP_TAG}' release found -- "
                          "has scripts/backup_corpus.py ever run successfully?")
    # Normally the ARCHIVE_NAME asset; the newest temporary upload if a
    # backup run died between deleting the old asset and renaming the new.
    asset, _ = bc.select_current_asset(release.get("assets", []))
    if asset is None:
        raise SystemExit(f"No '{ARCHIVE_NAME}' asset on the '{BACKUP_TAG}' release -- "
                          "has scripts/backup_corpus.py ever run successfully?")
    return asset


def extract_archive(archive_bytes):
    """Extracts into a temp folder inside data/ first and then moves each
    file into place with os.replace, so a failure partway (a corrupt
    archive, a full disk) leaves the existing files as they were. Members
    that aren't on backup_corpus.py's list -- in particular any PDF -- are
    skipped. Returns the restored member names."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=DATA_DIR))
    try:
        with gzip.GzipFile(fileobj=BytesIO(archive_bytes), mode="rb") as gz:
            with tarfile.open(fileobj=gz, mode="r") as tar:
                members = []
                for m in tar.getmembers():
                    if m.isfile() and bc.is_allowed_member(m.name):
                        members.append(m)
                    else:
                        print(f"  skipping unexpected archive member {m.name}")
                # extractall's default filter changed across Python versions and
                # a bare call warns/behaves differently depending on which one's
                # running -- "data" is the standard library's own recommended
                # safe default (no path traversal, no device files) and pins the
                # behavior explicitly instead of depending on interpreter version.
                tar.extractall(staging, members=members, filter="data")
        names = [m.name for m in members]
        for name in names:
            target = DATA_DIR / name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging / name, target)
        return names
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main():
    token = load_github_token()
    print(f"Looking up the '{BACKUP_TAG}' release...")
    asset = find_asset(token)
    print(f"Downloading {asset['name']} ({asset['size'] / 1e6:.1f} MB)...")
    archive_bytes = download_asset(asset["url"], token)
    if len(archive_bytes) != asset["size"]:
        raise SystemExit(f"Downloaded {len(archive_bytes)} bytes, expected {asset['size']} -- try again")
    names = extract_archive(archive_bytes)
    bc.save_state(asset, "restore")
    print(f"Restored into {DATA_DIR}: {', '.join(names)}")
    if "venues/arxiv_s2_citing.json" not in names:
        print("  note: this backup predates venues/arxiv_s2_citing.json being backed up; "
              "the build will refuse to merge without it")
    print("Now run: python build_public_site.py")


if __name__ == "__main__":
    main()
