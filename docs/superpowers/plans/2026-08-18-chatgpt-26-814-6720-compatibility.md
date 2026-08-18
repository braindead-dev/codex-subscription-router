# ChatGPT build 6720 Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exact, fail-closed support for ChatGPT `26.814.41407` (build `6720`) and install a separately ad-hoc-signed Codex Subscription Router without changing the official app.

**Architecture:** Treat each supported ChatGPT build as an explicit compatibility variant selected from the source `CFBundleShortVersionString` and `CFBundleVersion`. Keep common patch flow shared, but place every minified renderer/bootstrap identifier and exact replacement anchor behind the selected build variant. Add small Python unit tests for variant selection and updater removal, then prove the full patch against the installed build before replacing the final destination.

**Tech Stack:** Python 3 standard library, Electron ASAR via `@electron/asar`, minified JavaScript bundle rewriting, Go mux binary, macOS `codesign`, `ditto`, `xcrun clang`, `curl`, Git.

## Global Constraints

- Work only in this repository checkout on branch `codex/compat-chatgpt-26-814-6720`.
- Preserve `/Applications/ChatGPT.app` byte-for-byte; it is a read-only source.
- Do not use `--allow-untested-source` in the probe or final build.
- Ad-hoc signing is explicitly approved; Appshots and Computer Use may remain unavailable and must not be reported as verified.
- Keep build `6396` and `6662` behavior unchanged. Unknown version/build pairs and anchor-count mismatches must still fail closed.
- Use task-owned directories matching `.tmp-codex-router-*` beside the repository; remove them and stop task-owned processes before completion.
- Do not alter or delete `~/.codex-mux`, which contains durable router state.

---

## Task 1: Make source-build selection explicit and testable

**Files:**

- Modify: `scripts/patch_app.py`
- Create: `scripts/test_patch_app.py`
- Modify: `package.json`

- [ ] **Step 1: Add failing tests for the supported variant table**

Create `scripts/test_patch_app.py` with `unittest` cases that import `patch_app` from the same directory and assert:

```python
class SourceBuildVariantTests(unittest.TestCase):
    def test_selects_each_supported_build(self):
        self.assertEqual(patch_app.source_build_variant("26.803.61601", "6396"), "6396")
        self.assertEqual(patch_app.source_build_variant("26.810.52044", "6662"), "6662")
        self.assertEqual(patch_app.source_build_variant("26.814.41407", "6720"), "6720")

    def test_rejects_unknown_build(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported ChatGPT source build"):
            patch_app.source_build_variant("26.814.41407", "future")
```

- [ ] **Step 2: Run the new test and observe the expected failure**

Run:

```bash
python3 -m unittest scripts/test_patch_app.py -v
```

Expected: `AttributeError` because `source_build_variant` does not exist yet.

- [ ] **Step 3: Add named build constants and the exact 6720 compatibility values**

In `scripts/patch_app.py`, define:

```python
BUILD_6396 = ("26.803.61601", "6396")
BUILD_6662 = ("26.810.52044", "6662")
BUILD_6720 = ("26.814.41407", "6720")
```

Use those tuples as keys in all existing per-build tables. Add:

```python
BUILD_6720: "8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0"
```

and these verified counts/layouts:

```python
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD[BUILD_6720] = 99  # 删除 8 个 profile 后；原始总计 107
EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD[BUILD_6720] = 20
EXPECTED_CUA_SERVICE_LAYOUT_BY_BUILD[BUILD_6720] = (
    ("Codex Computer Use.app", 17),
    ("bin/mac/normal/Codex Computer Use.app", 13),
)
```

- [ ] **Step 4: Implement fail-closed build selection**

Add:

```python
def source_build_variant(source_version: str, source_build: str) -> str:
    variants = {
        BUILD_6396: "6396",
        BUILD_6662: "6662",
        BUILD_6720: "6720",
    }
    try:
        return variants[(source_version, source_build)]
    except KeyError as error:
        raise RuntimeError(
            f"unsupported ChatGPT source build: {source_version} ({source_build})"
        ) from error
```

Resolve this variant only after the existing ASAR approval check. Pass the variant into `patch_desktop_profile` and `patch_renderer`; do not detect a build by searching bundle text.

- [ ] **Step 5: Wire unit tests into the normal Python check**

Change `package.json` so `check:python` runs:

```json
"check:python": "python3 -m unittest scripts/test_patch_app.py && python3 -m py_compile scripts/patch_app.py scripts/check_release.py scripts/test_patch_app.py"
```

- [ ] **Step 6: Run focused tests and inspect the diff**

Run:

```bash
npm run check:python
git diff --check
git diff -- scripts/patch_app.py scripts/test_patch_app.py package.json
```

Expected: tests pass; the only compatibility data added is the exact build `6720` tuple and verified counts.

- [ ] **Step 7: Commit the build-selection layer**

```bash
git add scripts/patch_app.py scripts/test_patch_app.py package.json
git commit -m "feat: recognize ChatGPT build 6720"
```

---

## Task 2: Port native identity and desktop bootstrap patches

**Files:**

- Modify: `scripts/patch_app.py`
- Modify: `scripts/test_patch_app.py`

- [ ] **Step 1: Add a failing updater-removal test for build 6720**

Add a test using this exact synthetic source:

```python
source = "try{await o.initialize();let{runMainAppStartup:e}=await load();await e()}"
expected = "try{let{runMainAppStartup:e}=await load();await e()}"
self.assertEqual(patch_app.disable_copied_app_updater(source, "6720"), expected)
```

Retain one test for the existing `6396`/`6662` form, where `try{` follows the initialization call.

- [ ] **Step 2: Run the focused test and observe the expected failure**

```bash
python3 -m unittest scripts.test_patch_app.DesktopBootstrapTests -v
```

Expected: failure because `disable_copied_app_updater` is not implemented.

- [ ] **Step 3: Extract the updater rewrite into a build-aware helper**

Implement `disable_copied_app_updater(bootstrap: str, variant: str) -> str`. Use the existing pattern for variants `6396` and `6662`. For `6720`, use exactly:

```python
re.compile(
    r"await [A-Za-z_$][\w$]*\.initialize\(\);"
    r"(?=let\{runMainAppStartup:)"
)
```

Require exactly one replacement in every supported variant and preserve the current error message on mismatch. Call the helper from `patch_desktop_profile`.

- [ ] **Step 4: Preserve the verified 6720 Computer Use layout checks**

Confirm the build-specific values added in Task 1 feed both native package rewriting and ASAR rewriting. Do not loosen raw team-ID or bundle-ID counts. The verified build `6720` layout is:

```text
cua_node/lib/node_modules/@oai/sky/Codex Computer Use.app                  17 service references
cua_node/lib/node_modules/@oai/sky/bin/mac/normal/Codex Computer Use.app   13 service references
all @oai/sky native bundle-ID references                                  99（删除 8 个 profile 后；原始总计 107）
ASAR bundle-ID references                                                   20
```

- [ ] **Step 5: Run focused tests and the existing patch-function probe**

Run:

```bash
npm run check:python
CODEX_ROUTER_ASAR_ROOT="${CODEX_ROUTER_ASAR_ROOT:?set to a fresh extracted ASAR directory}" python3 - <<'PY'
import os
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
import patch_app

root = Path(os.environ["CODEX_ROUTER_ASAR_ROOT"])
assert root.is_dir(), root
patch_app.patch_desktop_profile(
    root,
    Path.home() / "Applications" / "Codex Subscription Router Computer Use.app",
    "6720",
)
print("desktop profile patch: PASS")
PY
```

If the prior extracted tree has already been modified, create a new task-owned ASAR extraction first; never probe against `/Applications/ChatGPT.app` in place.

Expected: updater removal and desktop profile isolation pass. A later renderer failure is not part of this task.

- [ ] **Step 6: Commit the desktop/bootstrap port**

```bash
git add scripts/patch_app.py scripts/test_patch_app.py
git commit -m "fix: port desktop bootstrap patch to build 6720"
```

---

## Task 3: Port the build 6720 renderer patch

**Files:**

- Modify: `scripts/patch_app.py`
- Modify: `ui/account-menu.js`
- Modify: `scripts/test_patch_app.py`

- [ ] **Step 1: Add failing tests for renderer configuration and request names**

Add tests for a pure `renderer_variant_config("6720")` helper. Assert the returned configuration contains the exact component anchor and identifier maps below, the `plugins-page-*.js` glob, and the thread component anchor. Also assert `ui/account-menu.js` contains all five direct app-server request names:

```text
app/list
app/installed
app/read
mcpServer/oauth/login
mcpServerStatus/list
```

Run:

```bash
python3 -m unittest scripts.test_patch_app.RendererVariantTests -v
```

Expected: failure until the 6720 renderer configuration and request names are added.

- [ ] **Step 2: Replace text inference with an explicit renderer configuration**

Create a small build-specific configuration returned by `renderer_variant_config(variant)`. Keep existing `6396` and `6662` values unchanged. The build `6720` profile-menu configuration is:

| Field | Build 6720 value |
| --- | --- |
| component anchor | `function DIl(e){let t=(0,MIl.c)(253),` |
| JSX runtime `e7` | `d7` |
| React namespace `kXc` | `NIl` |
| modal scope hook `Lo` | `Ss` |
| modal opener `BW` | `Tz` |
| native usage modal `QLs` | `kxc` |
| profile menu item `_H` | `rL` |
| usage icon `S2` | `z2` |
| menu namespace `CH` | `lL` |
| profile image helper `jLa` | `jwa` |
| query-client hook `lt` | `ct` |
| usage slot | `usageItems:Ct` |
| open-state anchors | `triggerButton:Dt,onOpenChange:l,children:P`; `open:s,onOpenChange:l,contentWidth:\`panel\`,triggerButton:Dt` |

Every exact anchor must occur once before replacement. Do not fold the new variant into an `else` fallback.

- [ ] **Step 3: Scope direct 6720 app-server requests**

In `ui/account-menu.js`, retain the five legacy bridge names and add the five direct request method names listed in Step 1 to the scoped-request set.

For build `6720`, locate this unique request-client method:

```javascript
async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,e===`plugin/list`&&n?.timeoutMs==null?{...n,timeoutMs:Vjt}:n)}
```

Insert `t=codexMuxScopePluginRequest(e,t);` after the dispatcher guard and before the return. Keep exact one-match validation. Verify these native mappings still exist before patching:

```text
app/list
app/installed
app/read
mcpServer/oauth/login
mcpServerStatus/list
listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})
let i=this.sendRequest(`mcpServerStatus/list`,e,t);
```

- [ ] **Step 4: Port usage and profile requests**

For build `6720`, replace the unique usage result:

```javascript
return{...e,rate_limit_upsell:t.success?t.data.rate_limit_upsell:void 0}
```

with:

```javascript
return await codexMuxFilterUsageStatus({...e,rate_limit_upsell:t.success?t.data.rate_limit_upsell:void 0})
```

This preserves the existing `OAI-App-Brand` header and schema parsing. Replace the unique profile fetch:

```javascript
let e=await Hv.safeGet(`/wham/profiles/me`)
```

with the established `codexMuxProfileData(globalThis.__codexMuxSelectedProfileAccountId??null)` call.

- [ ] **Step 5: Port reset-credit and Usage sheet integration**

Use native usage modal `kxc`. Add `CodexMuxUseResetAccountState();` at its function entry.

Replace build `6720` reset query `A_a` with:

```javascript
function A_a(){let e=window.__codexMuxResetAccountId;return It({queryKey:[`rate-limit-reset-credits`,e??`primary`],queryFn:e?()=>codexMuxRateLimitResets(e):j_a,refetchInterval:Lp.ONE_MINUTE,staleTime:Lp.FIVE_SECONDS})}
```

Replace reset mutation `M_a` with the existing account-keyed semantics, using `ct`, `Lb`, `N_a`, `n_a`, and `Qt` from the 6720 bundle:

```javascript
function M_a(){let e=ct(),t=Lb(),n=window.__codexMuxResetAccountId,r=[`rate-limit-reset-credits`,n??`primary`];return Qt({mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):N_a,onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?n.credit?.id??a:a;e.setQueryData(r,e=>n_a(e,o,t))}Promise.all([t([`rate-limit-status`]),t(r)])}})}
```

Replace the unique Usage header cache expression:

```javascript
let _e;t[46]===he?_e=t[47]:(_e=(0,N2.jsxs)(bz,{children:[he,ge]}),t[46]=he,t[47]=_e);
```

with:

```javascript
let _e=(0,N2.jsxs)(bz,{children:[he,ge,window.__codexMuxResetAccountSelector??null]});
```

Keep the selected-window anchor `let y=v;if(g!=null){` and all three depleted-alert anchors exact-count guarded.

- [ ] **Step 6: Port the profile and Plugins settings bundles**

For the single `profile-*.js` bundle, insert the avatar stack before the native label and hide that label when the stack is present:

```javascript
globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>M.refetch()})??null
```

Use the build `6720` exact values `R.isPending`, `lt`, `M.refetch`, `tt`, `Ye`, `$`, and `a`. Gate the native identity fields with `globalThis.__codexMuxSelectedProfileAccountId`:

```javascript
displayName:globalThis.__codexMuxSelectedProfileAccountId?(tt??(0,$.jsx)(a,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})):null
```

```javascript
username:globalThis.__codexMuxSelectedProfileAccountId&&Ye!=null?(0,$.jsx)(a,{id:`profile.usernameValue`,defaultMessage:`@{username}`,description:`Profile username shown with an at-sign prefix`,values:{username:Ye}}):null
```

For `plugins-page-*.js`, replace the unique wrapper:

```javascript
U=(0,sc.jsxs)(sc.Fragment,{children:[V,H]})
```

with:

```javascript
U=(0,sc.jsxs)(sc.Fragment,{children:[globalThis.CodexMuxPluginScope?.()??null,V,H]})
```

- [ ] **Step 7: Port the thread subscription indicator**

Use thread component anchor:

```text
function xE(e){let t=(0,wE.c)(32),
```

Retarget injected identifiers:

| Injected identifier | Build 6720 identifier |
| --- | --- |
| `$n` | `Xn` |
| `sr` | `ec` |
| `TE` | `mb` |
| `zE` | `TE` |
| `K` | `Z` |

Replace the unique summary children list:

```javascript
children:[l,u,d,f,p,m,h,g,_,v,y,b,x,S]
```

with the same list containing `(0,TE.jsx)(CodexMuxThreadSubscription,{})` immediately after `f`.

- [ ] **Step 8: Run syntax/unit checks, then a full isolated build probe**

Run:

```bash
npm run check:python
npm run check:js
repo_parent="$(dirname "$PWD")"
probe_root="$(mktemp -d "$repo_parent/.tmp-codex-router-build.XXXXXX")"
python3 scripts/patch_app.py \
  --allow-adhoc-signing \
  --destination "$probe_root/Codex Subscription Router.app"
```

Expected:

- source is reported as `26.814.41407 (6720)` with the approved hash;
- no `--allow-untested-source` warning appears;
- all native, ASAR, desktop and renderer anchor checks pass;
- the probe app and its sibling Computer Use helper are produced and signed.

If any exact anchor fails, inspect the relevant official extracted bundle and correct only that semantic mapping. Do not widen a regex just to make the count pass.

- [ ] **Step 9: Verify the probe signatures and official source hash**

Run:

```bash
codesign --verify --deep --strict "$probe_root/Codex Subscription Router.app"
codesign --verify --deep --strict "$probe_root/Codex Subscription Router Computer Use.app"
codesign --verify --deep --strict "$probe_root/Codex Subscription Router.app/Contents/Resources/cua_node/lib/node_modules/@oai/sky/Codex Computer Use.app"
codesign --verify --deep --strict "$probe_root/Codex Subscription Router.app/Contents/Resources/cua_node/lib/node_modules/@oai/sky/bin/mac/normal/Codex Computer Use.app"
test "$(shasum -a 256 /Applications/ChatGPT.app/Contents/Resources/app.asar | awk '{print $1}')" = "8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0"
```

- [ ] **Step 10: Commit the renderer port**

```bash
git add scripts/patch_app.py scripts/test_patch_app.py ui/account-menu.js
git commit -m "feat: port router UI to ChatGPT build 6720"
```

---

## Task 4: Document compatibility and run repository gates

**Files:**

- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/COMPATIBILITY.md`

- [ ] **Step 1: Add the exact supported build to user-facing documentation**

Add `26.814.41407` / `6720` / `8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0` beside existing supported builds. State that the local installation in this task uses ad-hoc signing and therefore does not prove Appshots or Computer Use.

- [ ] **Step 2: Add a concise changelog entry**

Record exact build `6720` support, explicit build-variant selection, and renderer/bootstrap anchor updates. Do not claim general support for future ChatGPT builds.

- [ ] **Step 3: Run all repository checks**

```bash
npm run check
npm run release:check
git diff --check
git status --short
```

Expected: all checks pass, and only task files are changed.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md CHANGELOG.md docs/COMPATIBILITY.md
git commit -m "docs: add ChatGPT build 6720 compatibility [skip ci]"
```

---

## Task 5: Install, launch, verify, and clean up

**Files:**

- Read-only source: `/Applications/ChatGPT.app`
- Install: `$HOME/Applications/Codex Subscription Router.app`
- Install: `$HOME/Applications/Codex Subscription Router Computer Use.app`

- [ ] **Step 1: Capture pre-install state**

Run:

```bash
official_hash_before="$(shasum -a 256 /Applications/ChatGPT.app/Contents/Resources/app.asar | awk '{print $1}')"
test "$official_hash_before" = "8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0"
pgrep -fl "$HOME/Applications/Codex Subscription Router" || true
```

If a prior Router instance is running, quit that exact app before replacement. Do not quit or alter official ChatGPT unless the patcher explicitly reports that its read-only source is locked.

- [ ] **Step 2: Build and atomically install the final app**

If the destination does not exist:

```bash
python3 scripts/patch_app.py --allow-adhoc-signing
```

If the exact Router destination already exists, first verify it belongs to this product and then use the patcher's recoverable backup behavior:

```bash
python3 scripts/patch_app.py --allow-adhoc-signing --force
```

Never add `--allow-untested-source`. Preserve any backup path printed by the patcher until smoke verification passes.

- [ ] **Step 3: Verify all final signatures**

```bash
router_app="$HOME/Applications/Codex Subscription Router.app"
router_helper="$HOME/Applications/Codex Subscription Router Computer Use.app"
codesign --verify --deep --strict "$router_app"
codesign --verify --deep --strict "$router_helper"
codesign --verify --deep --strict "$router_app/Contents/Resources/cua_node/lib/node_modules/@oai/sky/Codex Computer Use.app"
codesign --verify --deep --strict "$router_app/Contents/Resources/cua_node/lib/node_modules/@oai/sky/bin/mac/normal/Codex Computer Use.app"
```

Expected: all commands exit `0`; ad-hoc metadata may report no team identifier.

- [ ] **Step 4: Launch and prove the mux health endpoint**

```bash
open "$HOME/Applications/Codex Subscription Router.app"
```

Wait in short intervals for at most 30 seconds, then verify:

```bash
pgrep -fl "$HOME/Applications/Codex Subscription Router.app"
curl --fail --silent --show-error http://127.0.0.1:48123/v1/health
```

Expected health body: `{"ok":true}`. Confirm the process remains present after the health request. If macOS presents a permission or launch confirmation dialog, stop for user interaction; do not use an alternative automation channel to bypass it.

- [ ] **Step 5: Check launch stability and official-app integrity**

Confirm the Router window leaves splash/loading state and does not immediately exit or sustain obviously abnormal CPU. Then run:

```bash
official_hash_after="$(shasum -a 256 /Applications/ChatGPT.app/Contents/Resources/app.asar | awk '{print $1}')"
test "$official_hash_after" = "$official_hash_before"
test "$official_hash_after" = "8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0"
```

Do not report multi-account OAuth, account switching, Appshots, or Computer Use as verified in this task.

- [ ] **Step 6: Run final verification from a clean task state**

Use `superpowers:verification-before-completion`, then run:

```bash
npm run check
npm run release:check
git status --short --branch
git log --oneline --decorate -5
```

Expected: checks pass; all source/doc work is committed; branch identity is correct.

- [ ] **Step 7: Clean task-owned temporary artifacts**

Resolve every sibling `.tmp-codex-router-*` directory individually. Remove only directories confirmed to belong to this task and no longer needed. Stop only task-owned probe processes. Preserve final installed apps, repository source, Git commits, `~/.codex-mux`, and any recoverable installation backup until success is confirmed.

- [ ] **Step 8: Report the exact result**

Report:

- installed app and helper paths;
- source version/build/hash and unchanged post-install hash;
- signature verification results;
- process and `/v1/health` results;
- branch and commit IDs;
- Appshots/Computer Use and multi-account OAuth as explicitly unverified under the approved ad-hoc scope;
- any retained backup or residual path with its reason.
