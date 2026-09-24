#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for the site version build_public_site.py assigns to every build:
counting on from production, the maintainer's major.minor bump, the offline
fallback, and where the version ends up in public/. No network: production is
always a stub.

Usage: python -m unittest discover -s scripts/tests
"""
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_data_release
import build_public_site as bps


def offline():
    raise OSError("no network")


class NextVersionTests(unittest.TestCase):
    def test_same_minor_adds_one_patch(self):
        self.assertEqual(bps.next_version((0, 1), (0, 1, 2)), (0, 1, 3))

    def test_bumped_minor_starts_at_patch_zero(self):
        self.assertEqual(bps.next_version((0, 2), (0, 1, 9)), (0, 2, 0))
        self.assertEqual(bps.next_version((1, 0), (0, 3, 4)), (1, 0, 0))

    def test_parse_version(self):
        self.assertEqual(bps.parse_version("0.1.12"), (0, 1, 12))
        for bad in (None, "", "0.1", "v0.1.2", "0.1.x"):
            self.assertIsNone(bps.parse_version(bad))


class ResolveVersionTests(unittest.TestCase):
    def resolve(self, **kw):
        kw.setdefault("major_minor", (0, 1))
        with tempfile.TemporaryDirectory() as d:
            kw.setdefault("state_file", Path(d) / "missing.json")
            with redirect_stdout(io.StringIO()) as out:
                return bps.resolve_version(**kw), out.getvalue()

    def test_counts_on_from_production(self):
        self.assertEqual(self.resolve(fetch=lambda: (0, 1, 5))[0], "0.1.6")

    def test_production_without_a_version_counts_as_0_1_1(self):
        # Today's live BUILD_INFO.json has no "version" field.
        info = {"target": "production", "content_hash": "abc"}
        with mock.patch.object(bps.urllib.request, "urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(info).encode()
            self.assertEqual(self.resolve(fetch=bps.fetch_production_version)[0], "0.1.2")

    def test_maintainer_bump_resets_the_patch(self):
        self.assertEqual(self.resolve(major_minor=(0, 2), fetch=lambda: (0, 1, 7))[0], "0.2.0")

    def test_offline_counts_on_from_the_last_recorded_deploy(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            state.write_text(json.dumps({"published_version": "0.1.4"}), encoding="utf-8")
            version, out = self.resolve(fetch=offline, state_file=state)
        self.assertEqual(version, "0.1.5")
        self.assertIn("WARNING", out)

    def test_offline_with_nothing_recorded_assumes_0_1_1(self):
        version, out = self.resolve(fetch=offline)
        self.assertEqual(version, "0.1.2")
        self.assertIn("WARNING", out)

    def test_override_wins_and_is_validated(self):
        self.assertEqual(self.resolve(override="0.3.9", fetch=offline)[0], "0.3.9")
        with self.assertRaises(SystemExit):
            self.resolve(override="0.3", fetch=offline)

    def test_repo_version_file_is_major_minor(self):
        self.assertEqual(len(bps.read_major_minor()), 2)


class FetchProductionVersionTests(unittest.TestCase):
    def fetch_with(self, body=None, error=None):
        with mock.patch.object(bps.urllib.request, "urlopen") as urlopen:
            if error:
                urlopen.side_effect = error
            else:
                urlopen.return_value.__enter__.return_value.read.return_value = body.encode()
            return bps.fetch_production_version()

    def test_reads_the_version_field(self):
        self.assertEqual(self.fetch_with('{"version": "0.1.7"}'), (0, 1, 7))

    def test_missing_field_or_missing_file_is_0_1_1(self):
        self.assertEqual(self.fetch_with('{"content_hash": "x"}'), (0, 1, 1))
        err = bps.urllib.error.HTTPError(bps.PRODUCTION_BUILD_INFO_URL, 404, "Not Found", {}, None)
        self.assertEqual(self.fetch_with(error=err), (0, 1, 1))

    def test_unreachable_raises_oserror(self):
        with self.assertRaises(OSError):
            self.fetch_with(error=bps.urllib.error.URLError("down"))
        err = bps.urllib.error.HTTPError(bps.PRODUCTION_BUILD_INFO_URL, 503, "Unavailable", {}, None)
        with self.assertRaises(OSError):
            self.fetch_with(error=err)


class StampJsonTests(unittest.TestCase):
    def test_key_is_spliced_in_without_touching_the_rest(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "stats.json"
            p.write_text('{"a": 1, "b": [1, 2]}', encoding="utf-8")
            self.assertTrue(bps.stamp_json_version(p, "0.1.2"))
            self.assertEqual(p.read_text(encoding="utf-8"),
                             '{"site_version": "0.1.2", "a": 1, "b": [1, 2]}')
            p.write_text("{}", encoding="utf-8")
            bps.stamp_json_version(p, "0.1.2")
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"site_version": "0.1.2"})
            p.write_text("[1]", encoding="utf-8")
            self.assertFalse(bps.stamp_json_version(p, "0.1.2"))


class BuildOutputTests(unittest.TestCase):
    """Runs the real build_public_site() over the real site/ pages with a tiny
    stats.json, to check every place the version is supposed to show up."""

    def test_version_reaches_pages_scripts_json_and_build_info(self):
        with tempfile.TemporaryDirectory() as d:
            base, public = Path(d), Path(d) / "public"
            (base / "data").mkdir()
            (base / "data" / "stats.json").write_text(
                json.dumps({"generated_at": "2026-09-24", "all_papers": []}), encoding="utf-8")
            public.mkdir()
            (public / "old-page.html").write_text("stale", encoding="utf-8")
            with mock.patch.object(bps, "BASE", base), mock.patch.object(bps, "PUBLIC_DIR", public), \
                 redirect_stdout(io.StringIO()):
                bps.build_public_site("0.1.2")

            self.assertFalse((public / "old-page.html").exists())
            self.assertEqual(json.loads((public / "BUILD_INFO.json").read_text(encoding="utf-8"))["version"],
                             "0.1.2")
            self.assertEqual(json.loads((public / "stats.json").read_text(encoding="utf-8"))["site_version"],
                             "0.1.2")
            # about.json is written from the same stats and stamped the same
            # way; the About page shows its site_version.
            self.assertEqual(json.loads((public / "about.json").read_text(encoding="utf-8"))["site_version"],
                             "0.1.2")
            nav = (public / "nav.js").read_text(encoding="utf-8")
            self.assertIn("const SITE_VERSION = '0.1.2';", nav)
            for page in public.glob("*.html"):
                self.assertNotIn(bps.VERSION_PLACEHOLDER, page.read_text(encoding="utf-8"), page.name)
            for script in public.glob("*.js"):
                self.assertNotIn(bps.VERSION_PLACEHOLDER, script.read_text(encoding="utf-8"), script.name)

            # The homepage links exactly the files build_data_release.py writes.
            index = (public / "index.html").read_text(encoding="utf-8")
            self.assertIn("Download the data (v0.1.2)", index)
            linked = re.findall(r'href="download/([^"]+)"', index)
            self.assertEqual(linked, build_data_release.release_file_names("0.1.2"))


if __name__ == "__main__":
    unittest.main()
