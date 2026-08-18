import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patch_app


class SourceBuildVariantTests(unittest.TestCase):
    def test_selects_each_supported_build(self):
        self.assertEqual(patch_app.source_build_variant("26.803.61601", "6396"), "6396")
        self.assertEqual(patch_app.source_build_variant("26.810.52044", "6662"), "6662")
        self.assertEqual(patch_app.source_build_variant("26.814.41407", "6720"), "6720")

    def test_rejects_unknown_build(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported ChatGPT source build"):
            patch_app.source_build_variant("26.814.41407", "future")


if __name__ == "__main__":
    unittest.main()
