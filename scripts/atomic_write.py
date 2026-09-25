#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crash-safe file writes for the big corpus files (papers_full.json,
stats.json, citation_graph.json, venues/arxiv_s2_citing.json).

A plain Path.write_text() truncates the target first and then streams the
new content into it, so a crawler killed halfway through saving a 300 MB
papers_full.json (Ctrl+C, a laptop going to sleep, a CI job hitting its
time limit) leaves a truncated file behind. The next merge_corpus.py run
then can't read it and all carried-over enrichment is gone.

write_text_atomic() writes to a temp file in the same folder, flushes and
fsyncs it, then swaps it in with os.replace(), which is atomic on the same
filesystem on both Windows and Linux. Either the old file or the complete
new one is on disk, never half of each. If anything goes wrong before the
swap, the temp file is removed and the old file is left untouched.

Usage (from another script in scripts/):
    from atomic_write import write_json_atomic
    write_json_atomic(PAPERS_FILE, papers, indent=2)
"""
import json
import os
import time
from pathlib import Path

# os.replace() on Windows fails with PermissionError while another process
# (a virus scanner, the search indexer, an editor) briefly has the target
# open. A few short retries get past that without masking a real problem.
REPLACE_RETRIES = 5
REPLACE_RETRY_DELAY = 0.5


def write_text_atomic(path, text, encoding="utf-8"):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(REPLACE_RETRIES):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == REPLACE_RETRIES - 1:
                    raise
                time.sleep(REPLACE_RETRY_DELAY)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt mid-write is
        # exactly the case this module exists for, and it shouldn't leave
        # a stray temp file next to the data either.
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def write_json_atomic(path, data, indent=None, **dump_kwargs):
    """Same bytes as the old write_text(json.dumps(data, indent=...,
    ensure_ascii=False), newline="\\n") calls it replaces. The JSON is
    serialized before the temp file is even opened, so a serialization
    error never touches the disk."""
    dump_kwargs.setdefault("ensure_ascii", False)
    write_text_atomic(path, json.dumps(data, indent=indent, **dump_kwargs))
