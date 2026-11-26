# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import prepare_zenodo as z


class MetadataTests(unittest.TestCase):
    def test_dataset_metadata_carries_version_license_and_no_orcid(self):
        m = z.metadata("0.2.1")["metadata"]
        self.assertEqual(m["upload_type"], "dataset")
        self.assertEqual(m["version"], "0.2.1")
        self.assertEqual(m["license"], "cc-by-nc-4.0")
        self.assertIn("v0.2.1", m["title"])
        self.assertNotIn("orcid", str(m["creators"]).lower())   # CITATION.cff keeps the maintainer's ORCID out


if __name__ == "__main__":
    unittest.main()
