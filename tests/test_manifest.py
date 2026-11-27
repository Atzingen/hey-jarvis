from __future__ import annotations

import json
from pathlib import Path
import unittest

MANIFEST = Path(__file__).resolve().parent.parent / 'manifest.json'


class MarketplaceManifestTest(unittest.TestCase):
    def test_description_fits_the_marketplace_limit(self) -> None:
        # The marketplace rejects the whole update when the description passes 500 characters,
        # a limit `omarchy plugin validate` does not check (2.5.0 was rejected for it).
        manifest = json.loads(MANIFEST.read_text())
        self.assertLessEqual(len(manifest['description']), 500)
        self.assertLessEqual(len(manifest['barWidget']['description']), 500)
