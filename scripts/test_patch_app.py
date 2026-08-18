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


class DesktopBootstrapTests(unittest.TestCase):
    def test_removes_updater_initialization_for_6396_form(self):
        source = "await o.initialize();try{let{runMainAppStartup:e}=await load();await e()}"
        expected = "try{let{runMainAppStartup:e}=await load();await e()}"
        self.assertEqual(patch_app.disable_copied_app_updater(source, "6396"), expected)

    def test_removes_updater_initialization_for_6720_form(self):
        source = "try{await o.initialize();let{runMainAppStartup:e}=await load();await e()}"
        expected = "try{let{runMainAppStartup:e}=await load();await e()}"
        self.assertEqual(patch_app.disable_copied_app_updater(source, "6720"), expected)


if __name__ == "__main__":
    unittest.main()
