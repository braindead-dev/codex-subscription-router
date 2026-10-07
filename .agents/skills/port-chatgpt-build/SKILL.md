---
name: port-chatgpt-build
description: Port the router patch to a new official ChatGPT (Codex) macOS build, verify it, release it, and leave the repo smaller than before. Use when a new build is out, the patcher rejects a source, or someone asks to update or modernize the patch.
---

# Port a new ChatGPT build

A port locates the renamed or reshaped minified anchors, records a profile,
and proves it. Done means the newest build is in `SUPPORTED_BUILDS`, every
check below passes, and the port is pushed to `main`.

## Rules

- Anchors fail closed. Never weaken a check, count, or hash to make a build
  pass; find the new code instead.
- Port the newest official build. If the request requires the newest three
  official builds, also fill any gaps in that appcast window. Keep at most
  three profiles; retire older profiles with their `SUPPORTED_BUILDS` entry,
  `COMPATIBILITY.md` row, source copy, and any code only they used.
- Never commit an app, archive, credential, or account state.
- Never quit, relaunch, or install over Henry's running app without his "go"
  in the same message. Land verified ports directly on `main` without PRs.

## 1. Detect and fetch

The daily `Upstream` workflow fails when a new build needs a port. Locally:

```sh
python3 scripts/appcast.py --fetch ~/.codex-mux/sources
```

Exit 0 means the newest build is supported; exit 10 means port it. For a
newest-three request, stop only if `appcast.supported(version, build)` passes
for all of `appcast.builds()[:3]`; fetch gaps with `appcast.fetch`.

## 2. Port the profile

```sh
python3 scripts/port_renderer.py --source <app> > /tmp/profile.py
```

It matches every anchor of the newest profile by shape and prints a ready
`RENDERER_BUILD_<build>` plus a list of what it could not resolve. Register the
profile in `RENDERER_BUILDS` and the build in `SUPPORTED_BUILDS` (hash from
step 3). If the generated profile equals an existing one in every field,
alias it and register the shared profile once. Resolve the listed gaps:

- Find the moved code by a string literal or property name from the old
  anchor, and copy the new anchor verbatim from the bundle. Never retype it.
- Make anchors unique by shape; choose profile markers that distinguish
  supported builds, since adjacent builds can share the RPC accessor.
- Borrow only imports, hoisted functions, or variables of a module the patched
  component itself initializes. Otherwise draw it (see the usage icon) or read
  it through a live import.
- Give every borrowed identifier a probe in `identifier_probes`, taken from a
  usage site that is unique by shape, never from an import clause. Check its
  binding in the injection bundle: a capture in another chunk may name a
  different local alias even when the generator reports no gap.
- Our UI lives in the eager `app-initial` bundle next to `menu_anchor`; lazy
  chunks only call it through `globalThis`. Patches land in whichever bundle
  holds their anchor, so moved code needs a new anchor, not a new mechanism.
- When main-process code or packaging changes shape, teach the one helper
  both shapes instead of branching per build (see `attach_router_updater`),
  and drop the old shape with the last build that had it. A string that moves
  between builds belongs in the profile, and a replacement that only inserts
  code is derived from its anchor (see `composer_actions`).

Finish with `port_renderer.py --source <app> --reference <build>` exiting 0.

## 3. Verify

```sh
npm ci --ignore-scripts && npm run check
python3 scripts/verify_build.py --source <app>
```

`verify_build.py` applies every app.asar patch in a temporary directory, parses
each changed file, and prints the hash for `SUPPORTED_BUILDS`. On Henry's Mac,
also build the real app into a stage while his keeps running:

```sh
CODEX_MUX_DISPLAY_NAME="Codex (router)" CODEX_MUX_SIGNING_IDENTITY=- \
  python3 scripts/patch_app.py --source <app> --allow-adhoc-signing \
  --destination "$HOME/Applications/Codex (router).app" --stage ~/.codex-mux/port-stage
```

The staged app must boot: `python3 scripts/launch_check.py --app <staged>`
targets the staged CLI with an isolated desktop profile. It requires a rendered
UI and an initialized app-server, without a test-server port. Signing and
Electron hardening only show up here. The router, adjacent `codex.real`, and
`codex-cli/bin/codex` must each print the Codex version. Then run the live
move test against the staged `codex.real` (`scripts/live_seed.py` prepares its
home). After his "go", quit the app, install with
`patch_app.py --install-staged ~/.codex-mux/port-stage --destination <app>`
(delete a broken installed copy by exact path first so the backup stays the
last good build), relaunch with `launchctl setenv CODEX_MUX_UI_TESTS 1`, and
check the profile menu, composer account picker, usage sheet, and thread panel
through the bridge on port 48124. Unset the variable afterwards.

## 4. Release

1. Commit as `Support ChatGPT build <build> (<version>, Codex <cli version>)`.
2. Update `docs/COMPATIBILITY.md`, the Unreleased section of `CHANGELOG.md`,
   and bump the minor version as `docs/RELEASING.md` describes.
3. Run `npm run release:check`, fetch current `main`, integrate the verified
   port into `main` without a PR, push, and let CI pass. Preserve others’ work.
4. Tag `v<version>` and push the tag; the release workflow drafts the release.
   Summarize the upstream Codex changelog since the last port for Henry.
   After verification and CI pass, review and publish the draft automatically;
   no further go is needed. Publishing enables the installed Update button.

## 5. Housekeeping

Delete by exact path: source apps in `~/.codex-mux/sources` that are no longer
supported, and scratch extractions. The patcher keeps a single install backup
and the updater prunes release sources and older official builds. Leave the
diff smaller than you found it.

Fix incorrect skill steps in the same commit; keep this under 120 lines.
