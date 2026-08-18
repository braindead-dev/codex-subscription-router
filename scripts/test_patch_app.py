import unittest
import sys
import subprocess
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
    BUILD_6720_PLUGIN_REQUEST_FIXTURE = """
async function loadApps(e,n,i,XMr,t,a){let o=await Qg(e,n).sendRequest(`app/list`,{cursor:i,limit:XMr,forceRefetch:t},{trace:a});return o}
async function loadInstalled(e,n,t,YMr,ZMr){let r=(await Qg(e,n).sendRequest(`app/installed`,t?{forceRefresh:!0}:{})).apps;let i=await Promise.all((0,YMr.default)(r.map(e=>e.id),ZMr).map(t=>Qg(e,n).sendRequest(`app/read`,{appIds:t})));return[r,i]}
async function loginMcp(t,n){let{authorizationUrl:r}=await t.sendRequest(`mcpServer/oauth/login`,n);return r}
class RequestClient {
  constructor(){this.dispatchMessage=()=>{};this.mcpServerStatusPromises=new Map;this.calls=[]}
  async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,e===`plugin/list`&&n?.timeoutMs==null?{...n,timeoutMs:Vjt}:n)}
  sendConfigReadRequest(t,n){return this.enqueueRequest(`config/read`,t,n)}
  enqueueRequest(e,t,n){this.calls.push({method:e,params:t,options:n});return new Promise(()=>{})}
  listMcpServers(e,t){let n=JSON.stringify({options:t,params:e}),r=this.mcpServerStatusPromises.get(n);if(r)return r;let i=this.sendRequest(`mcpServerStatus/list`,e,t);return this.mcpServerStatusPromises.set(n,i),i.finally(()=>{this.mcpServerStatusPromises.delete(n)})}
}
"""

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

    def test_legacy_renderer_variants_keep_their_native_targets(self):
        expected = {
            "6396": {
                "component_anchor": "function wXc({sidebarFooter:e,triggerButton:t})",
                "component_identifiers": {},
                "usage_anchor": "usageItems:Ge",
                "plugin_bundle_glob": "plugins-settings-*.js",
                "thread_component_anchor": "function bE(){let e=(0,wE.c)(57)",
                "thread_identifiers": {},
                "thread_summary_component": "zE",
            },
            "6662": {
                "component_anchor": "function Icl(e){let t=(0,Vcl.c)(248),",
                "component_identifiers": {
                    "e7": "$5",
                    "kXc": "Hcl",
                    "Lo": "Fo",
                    "BW": "RU",
                    "QLs": "E$s",
                    "_H": "GV",
                    "S2": "E0",
                    "CH": "ZV",
                    "jLa": "x$a",
                    "lt": "ct",
                },
                "usage_anchor": "usageItems:Ct",
                "plugin_bundle_glob": "plugins-page-*.js",
                "thread_component_anchor": "function bE(){let e=(0,SE.c)(1)",
                "thread_identifiers": {
                    "$n": "jf",
                    "sr": "Pa",
                    "TE": "jy",
                    "zE": "CE",
                    "K": "q",
                },
                "thread_summary_component": "CE",
            },
        }

        for variant, expected_values in expected.items():
            with self.subTest(variant=variant):
                config = patch_app.renderer_variant_config(variant)
                self.assertEqual(
                    {key: config[key] for key in expected_values}, expected_values
                )

    def test_renderer_configuration_rejects_unknown_variant(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported ChatGPT build variant"):
            patch_app.renderer_variant_config("future")

    def test_build_6720_config_guards_unique_direct_request_callsites(self):
        config = patch_app.renderer_variant_config("6720")
        self.assertIn("plugin_rpc_mapping_anchors", config)
        self.assertEqual(
            config["plugin_rpc_mapping_anchors"],
            (
                "let o=await Qg(e,n).sendRequest(`app/list`,{cursor:i,limit:XMr,forceRefetch:t},{trace:a})",
                "let r=(await Qg(e,n).sendRequest(`app/installed`,t?{forceRefresh:!0}:{})).apps",
                "map(t=>Qg(e,n).sendRequest(`app/read`,{appIds:t}))",
                "{authorizationUrl:r}=await t.sendRequest(`mcpServer/oauth/login`,n);",
                "let i=this.sendRequest(`mcpServerStatus/list`,e,t);",
            ),
        )

    def test_account_menu_scopes_build_6720_direct_request_methods(self):
        account_menu = (
            Path(__file__).resolve().parents[1] / "ui" / "account-menu.js"
        ).read_text(encoding="utf-8")
        assertions = """
globalThis.__codexMuxPluginAccountId = "secondary";
for (const method of ["app/list","app/installed","app/read","mcpServer/oauth/login","mcpServerStatus/list"]) {
  const scoped = codexMuxScopePluginRequest(method, {value: 1});
  if (scoped.value !== 1 || scoped.codexMuxAccountId !== "secondary") {
    throw new Error(`request method was not scoped: ${method}`);
  }
}
const untouched = {value: 2};
if (codexMuxScopePluginRequest("thread/list", untouched) !== untouched) {
  throw new Error("unscoped request was changed");
}
"""
        result = subprocess.run(
            ["node", "-e", account_menu + assertions],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_build_6720_scopes_mcp_status_before_inflight_key_lookup(self):
        patch_plugin_requests = getattr(patch_app, "patch_plugin_requests", None)
        self.assertIsNotNone(patch_plugin_requests)
        patched = patch_plugin_requests(
            self.BUILD_6720_PLUGIN_REQUEST_FIXTURE,
            "6720",
        )
        account_menu = (
            Path(__file__).resolve().parents[1] / "ui" / "account-menu.js"
        ).read_text(encoding="utf-8")
        regression = """
const client = new RequestClient();
globalThis.__codexMuxPluginAccountId = "account-a";
client.listMcpServers({}, {});
globalThis.__codexMuxPluginAccountId = "account-b";
client.listMcpServers({}, {});
if (client.calls.length !== 2) {
  throw new Error(`expected two account-specific dispatches, got ${client.calls.length}`);
}
if (client.calls[0].params.codexMuxAccountId !== "account-a" || client.calls[1].params.codexMuxAccountId !== "account-b") {
  throw new Error(`wrong scoped dispatch params: ${JSON.stringify(client.calls)}`);
}
"""
        result = subprocess.run(
            ["node", "-e", account_menu + patched + regression],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
