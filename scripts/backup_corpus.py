#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backs up the gitignored corpus files that can't be rebuilt from git alone
to a single draft GitHub Release on this repo, so a data-loss on the machine
that runs the real pipeline doesn't mean re-crawling for weeks.

What goes in (BACKUP_FILES below), and why: see DECISIONS.md's "Derived data
is not tracked" entry. Short version -- merge_corpus.py only carries author
affiliations, citation counts, and abstracts *forward* from an existing
papers_full.json; it doesn't re-derive them. citation_graph.json is a second,
independent gap (aggregate.py reads its edges directly).
venues/arxiv_s2_citing.json is where about 45% of the AV papers come from
at all, and it carries hand-checked venue fixes on top of the S2 crawl, so a
recrawl wouldn't give the same corpus back. The rest are the Semantic Scholar
and arXiv crawlers' resume files (seed list, ID lookups, per-paper "already
checked" markers), so a restored checkout, including the monthly GitHub
Actions job, can pick up where the last crawl stopped instead of starting
over. Never any PDF: data/pdfs_cvf stays on the laptop.

Why a GitHub Release and not a git commit: a release asset is stored
separately from the git object database -- it's never fetched by `git
clone`, never appears in a diff, and isn't subject to the ~100MB per-file
push limit that made papers_full.json gitignored in the first place (see
.gitignore's comment on it). Draft (not published) so the asset is visible
only to repo collaborators despite the repo itself being public.

One fixed tag and one asset name, never a new release per backup, so this can
run after every deploy without piling up old snapshots. The new archive is
uploaded under a temporary name first, and the old asset is deleted only
after that upload succeeded; then the new one is renamed into place. A
failed upload leaves the previous backup untouched and exits non-zero.

Two machines write backups: the laptop and the monthly GitHub Actions job.
To stop one of them overwriting a newer backup from the other with older
data, restore_corpus.py and this script record which asset this checkout
last restored or uploaded (data/corpus_backup_state.json, gitignored). A
backup is refused when the asset on GitHub is a different one, i.e. someone
else backed up since. Run restore_corpus.py to start from theirs, or pass
--force to overwrite it with this checkout's data on purpose.

Requires GITHUB_TOKEN (environment variable, or a line in av-atlas/.env,
which is gitignored -- never commit a real one) with "contents: read and
write" on this repo; a fine-grained personal access token scoped to
nightrome/av-atlas is enough. Skips (prints a note, exits 0) when no token
is configured, so a checkout without one doesn't fail scripts/deploy.py
over an optional step. Every other failure exits non-zero.

Usage: python backup_corpus.py [--force]
"""
import argparse
import gzip
import io
import json
import os
import re
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from atomic_write import write_json_atomic

BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
ENV_FILE = BASE / ".env"
# Which backup asset this checkout's data came from (or was last written
# to). See the module docstring; written by both scripts.
STATE_FILE = DATA_DIR / "corpus_backup_state.json"

# (path under data/, required). Required files abort the backup when
# missing: a backup without them would replace a complete one with one that
# silently drops most of the corpus. The optional ones are crawler resume
# files that may legitimately not exist yet on a given machine.
BACKUP_FILES = (
    ("papers_full.json", True),
    ("venues/arxiv_s2_citing.json", True),
    ("citation_graph.json", False),
    ("s2_citing_seeds.json", False),
    # Semantic Scholar side files (fetch_s2_references.py)
    ("s2_paper_ids.json", False),
    ("reference_lists_s2.json", False),
    # Resume files for the per-paper enrichment crawlers
    ("abstracts_semanticscholar.json", False),
    ("affiliations_arxiv.json", False),
    ("affiliations_cvf.json", False),
    ("arxiv_ids.json", False),
    ("s2_author_ids.json", False),
    ("s2_author_ids_checked_papers.json", False),
)

REPO_OWNER = "nightrome"
REPO_NAME = "av-atlas"
API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
BACKUP_TAG = "corpus-backup"
ARCHIVE_NAME = "av-atlas-corpus.tar.gz"
# Name prefix for a new archive while it's still being uploaded; renamed to
# ARCHIVE_NAME once the old one is gone.
UPLOAD_PREFIX = "av-atlas-corpus-upload-"
HEADERS_BASE = {
    "User-Agent": "av-atlas-backup (mailto:holger@it-caesar.com)",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
# Uploading 100+ MB over a slow link has timed out at 60 s before (see
# PIPELINE.md); this is a per-socket-operation timeout, not a total.
UPLOAD_TIMEOUT = 600


def load_github_token():
    """None (not a hard error) when unset -- see module docstring: this
    step is optional, not a pipeline requirement. The environment variable
    wins, which is how a GitHub Actions job passes it in."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_TOKEN="):
            token = line.split("=", 1)[1].strip()
            return token or None
    return None


def is_allowed_member(name):
    """Only files from BACKUP_FILES, never a PDF or anything under
    pdfs_cvf/ -- checked on both ends, so neither a later edit to the list
    nor a tampered archive can move PDFs off the laptop or onto it."""
    lowered = name.lower()
    if lowered.endswith(".pdf") or "pdfs_cvf" in lowered:
        return False
    return name in {path for path, _ in BACKUP_FILES}


def build_archive():
    """One gzip member holding a tar of the BACKUP_FILES that exist, stored
    under their path relative to data/ so restore_corpus.py can extract
    straight into data/. Returns (archive_bytes, member_names)."""
    members = []
    for rel, required in BACKUP_FILES:
        path = DATA_DIR / rel
        if path.exists():
            members.append(rel)
        elif required:
            raise SystemExit(f"{path} not found -- refusing to write a backup without it "
                             "(restore it with scripts/restore_corpus.py first)")
        else:
            print(f"  note: {path} not found, leaving it out")
    for rel in members:
        if not is_allowed_member(rel):
            raise SystemExit(f"Refusing to back up {rel}: not an allowed corpus file")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:  # mtime=0: reproducible bytes for the same input
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for rel in members:
                tar.add(DATA_DIR / rel, arcname=rel)
    return buf.getvalue(), members


def api_request(method, url, token, data=None, headers=None, timeout=60):
    """(status, parsed body). Network errors come back as status 0 with the
    error text, so every caller's "status != expected" check turns them into
    a clean non-zero exit instead of a traceback."""
    req = urllib.request.Request(url, method=method, data=data,
                                  headers={**HEADERS_BASE, "Authorization": f"Bearer {token}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"message": str(e)}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"message": str(e)}


def select_releases_by_tag(releases, tag):
    """Pure selection logic split out of find_existing_release so it's
    testable without a live API call. Returns (newest, stale) where newest
    is the most recently created release with this tag_name, or (None, [])
    if there's no match. Client-side filtering because GitHub's "get
    release by tag" endpoint (/releases/tags/{tag}) only resolves
    *published* releases -- a draft has no real tag ref, so it 404s there
    even when a draft with this tag_name exists."""
    matches = [r for r in releases if r.get("tag_name") == tag]
    if not matches:
        return None, []
    matches.sort(key=lambda r: r["created_at"], reverse=True)
    return matches[0], matches[1:]


def select_current_asset(assets):
    """(current, leftovers) among a release's assets. current is the
    ARCHIVE_NAME asset, or, if a run died between deleting the old asset
    and renaming the new one, the newest finished temporary upload.
    leftovers are the other temporary uploads (failed or superseded runs),
    safe to delete. Shared with restore_corpus.py."""
    uploads = [a for a in assets if a.get("name", "").startswith(UPLOAD_PREFIX)]
    finished = sorted((a for a in uploads if a.get("state", "uploaded") == "uploaded"),
                      key=lambda a: a.get("created_at", ""), reverse=True)
    current = next((a for a in assets if a.get("name") == ARCHIVE_NAME), None)
    if current is None and finished:
        current = finished[0]
    leftovers = [a for a in uploads if a is not current]
    return current, leftovers


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def save_state(asset, action):
    write_json_atomic(STATE_FILE, {
        "asset_id": asset["id"],
        "asset_name": asset.get("name"),
        "size": asset.get("size"),
        "action": action,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2)


def overwrite_refusal(current, state, force):
    """None if it's fine to replace `current` (the asset on GitHub now),
    otherwise the reason not to. Pure, for testing."""
    if current is None or force:
        return None
    if state is None:
        return (f"there is already a backup on GitHub (asset {current['id']}, "
                f"{current.get('updated_at', '?')}) and this checkout has no record of restoring "
                f"or writing it ({STATE_FILE.name} is missing)")
    if state.get("asset_id") != current["id"]:
        return (f"the backup on GitHub (asset {current['id']}, {current.get('updated_at', '?')}) is not "
                f"the one this checkout last restored or wrote (asset {state.get('asset_id')}), "
                "so another machine has backed up since")
    return None


def find_existing_release(token):
    """Without the fix in select_releases_by_tag, get_or_create_release
    always fell through to "create", silently piling up one new draft per
    backup run instead of reusing the one this module's docstring promises
    -- confirmed on the live repo, 4 accumulated 'corpus-backup' drafts
    before this fix."""
    status, body = api_request("GET", f"{API_BASE}/releases?per_page=100", token)
    if status != 200:
        raise SystemExit(f"Listing releases failed ({status}): {body}")
    newest, stale = select_releases_by_tag(body, BACKUP_TAG)
    if newest is None:
        return None
    for release in stale:
        # Self-heals the pre-fix duplicate-draft pile-up described above --
        # deletes drafts this same lookup would otherwise never reuse.
        status, resp_body = api_request("DELETE", f"{API_BASE}/releases/{release['id']}", token)
        if status != 204:
            raise SystemExit(f"Deleting stale '{BACKUP_TAG}' draft {release['id']} failed ({status}): {resp_body}")
        print(f"  removed stale duplicate draft release {release['id']} ({release['created_at']})")
    return newest


def get_or_create_release(token):
    existing = find_existing_release(token)
    if existing is not None:
        return existing
    payload = json.dumps({
        "tag_name": BACKUP_TAG,
        "name": "Corpus data backup (not a real release, do not download for site use)",
        "body": "Auto-updated snapshot of the gitignored corpus files (papers_full.json, "
                "venues/arxiv_s2_citing.json, citation_graph.json, crawler resume files) for "
                "scripts/restore_corpus.py. Overwritten on every backup_corpus.py run -- see DECISIONS.md.",
        "draft": True,
    }).encode("utf-8")
    status, body = api_request("POST", f"{API_BASE}/releases", token, data=payload,
                                headers={"Content-Type": "application/json"})
    if status != 201:
        raise SystemExit(f"Creating the {BACKUP_TAG} release failed ({status}): {body}")
    print(f"  created draft release '{BACKUP_TAG}'")
    return body


def delete_asset(asset, token):
    status, body = api_request("DELETE", asset["url"], token)
    if status != 204:
        raise SystemExit(f"Deleting asset {asset['name']} ({asset['id']}) failed ({status}): {body}")


def upload_asset(release, token, archive_bytes, name):
    # upload_url is a URI template ("...assets{?name,label}") -- the {?...}
    # suffix is for URI-template expansion, not a literal query string.
    upload_url = re.sub(r"\{.*\}$", "", release["upload_url"])
    status, body = api_request(
        "POST", f"{upload_url}?name={name}", token,
        data=archive_bytes, headers={"Content-Type": "application/gzip"}, timeout=UPLOAD_TIMEOUT)
    if status != 201:
        raise SystemExit(f"Uploading {name} failed ({status}): {body} -- the previous backup is untouched")
    print(f"  uploaded {name}: {len(archive_bytes) / 1e6:.1f} MB")
    return body


def rename_asset(asset, token, name):
    payload = json.dumps({"name": name}).encode("utf-8")
    status, body = api_request("PATCH", asset["url"], token, data=payload,
                                headers={"Content-Type": "application/json"})
    if status != 200:
        raise SystemExit(f"Renaming {asset['name']} to {name} failed ({status}): {body} -- the new "
                         "backup is on the release under its temporary name, and restore_corpus.py "
                         "will still find it")
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up the gitignored corpus files to a draft GitHub Release.")
    parser.add_argument("--force", action="store_true",
                        help="overwrite the backup on GitHub even if this checkout didn't start from it")
    args = parser.parse_args(argv if argv is not None else [])

    token = load_github_token()
    if not token:
        print("backup_corpus.py: no GITHUB_TOKEN set, skipping corpus backup (see .env.example)")
        return
    archive_bytes, members = build_archive()
    print(f"Packed {', '.join(members)} ({len(archive_bytes) / 1e6:.1f} MB)")
    release = get_or_create_release(token)
    current, leftovers = select_current_asset(release.get("assets", []))

    reason = overwrite_refusal(current, load_state(), args.force)
    if reason:
        raise SystemExit(f"Not backing up: {reason}. Run scripts/restore_corpus.py to start from that "
                         "backup, or pass --force to replace it with this checkout's data.")

    for asset in leftovers:
        delete_asset(asset, token)
        print(f"  removed leftover upload {asset['name']}")

    # Upload first, delete second: if the upload fails, the old backup is
    # still there. GitHub has no "replace this asset" call, and two assets
    # on one release can't share a name, hence the temporary name + rename.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    new_asset = upload_asset(release, token, archive_bytes, f"{UPLOAD_PREFIX}{stamp}.tar.gz")
    if current is not None:
        delete_asset(current, token)
        print(f"  removed previous {current['name']} ({current.get('size', 0) / 1e6:.1f} MB)")
    # Recorded before the rename (which keeps the asset id), so a failed
    # rename doesn't make the next run refuse over our own upload.
    save_state(new_asset, "backup")
    new_asset = rename_asset(new_asset, token, ARCHIVE_NAME)
    save_state(new_asset, "backup")
    print(f"Backed up to https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/tag/{BACKUP_TAG} "
          f"(draft, visible to collaborators only)")


if __name__ == "__main__":
    main(sys.argv[1:])
