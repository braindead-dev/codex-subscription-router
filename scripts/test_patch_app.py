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

    def test_build_6720_native_replacement_count_excludes_removed_profiles(self):
        self.assertEqual(
            patch_app.EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD[
                patch_app.BUILD_6720
            ],
            99,
        )


class DesktopBootstrapTests(unittest.TestCase):
    def test_removes_updater_initialization_for_6396_form(self):
        source = "await o.initialize();try{let{runMainAppStartup:e}=await load();await e()}"
        expected = "try{let{runMainAppStartup:e}=await load();await e()}"
        self.assertEqual(patch_app.disable_copied_app_updater(source, "6396"), expected)

    def test_removes_updater_initialization_for_6720_form(self):
        source = "try{await o.initialize();let{runMainAppStartup:e}=await load();await e()}"
        expected = "try{let{runMainAppStartup:e}=await load();await e()}"
        self.assertEqual(patch_app.disable_copied_app_updater(source, "6720"), expected)


class RendererVariantTests(unittest.TestCase):
    def test_build_6720_renderer_configuration_uses_exact_native_identifiers(self):
        renderer_variant_config = getattr(
            patch_app, "renderer_variant_config", None
        )
        self.assertIsNotNone(renderer_variant_config)

        config = renderer_variant_config("6720")
        self.assertEqual(
            config["component_anchor"],
            "function DIl(e){let t=(0,MIl.c)(253),",
        )
        self.assertEqual(
            config["component_identifiers"],
            {
                "e7": "d7",
                "kXc": "NIl",
                "Lo": "Ss",
                "BW": "Tz",
                "QLs": "kxc",
                "_H": "rL",
                "S2": "z2",
                "CH": "lL",
                "jLa": "jwa",
                "lt": "ct",
            },
        )
        self.assertEqual(config["usage_anchor"], "usageItems:Ct")
        self.assertEqual(
            config["open_change_anchors"],
            (
                "triggerButton:Dt,onOpenChange:l,children:P",
                "open:s,onOpenChange:l,contentWidth:`panel`,triggerButton:Dt",
            ),
        )
        self.assertEqual(config["plugin_bundle_glob"], "plugins-page-*.js")
        self.assertEqual(
            config["thread_component_anchor"],
            "function xE(e){let t=(0,wE.c)(32),",
        )
        self.assertEqual(
            config["thread_identifiers"],
            {"$n": "Xn", "sr": "ec", "TE": "mb", "zE": "TE", "K": "Z"},
        )

    def test_account_menu_scopes_build_6720_direct_request_names(self):
        account_menu = (
            Path(__file__).resolve().parents[1] / "ui" / "account-menu.js"
        ).read_text(encoding="utf-8")
        for request_name in (
            "app/list",
            "app/installed",
            "app/read",
            "mcpServer/oauth/login",
            "mcpServerStatus/list",
        ):
            with self.subTest(request_name=request_name):
                self.assertIn(f'"{request_name}"', account_menu)


if __name__ == "__main__":
    unittest.main()
