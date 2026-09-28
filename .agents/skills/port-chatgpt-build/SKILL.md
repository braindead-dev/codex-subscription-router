---
name: port-chatgpt-build
description: Port the router patch to a new official ChatGPT (Codex) macOS build, verify it, release it, and leave the repo smaller than before. Use when a new build is out, the patcher rejects a source, or someone asks to update or modernize the patch.
---

# Port a new ChatGPT build

The patch anchors on minified code that every official release renames and
sometimes reshapes. A port finds the same code in the new build, records it as
one profile, proves it, and ships it. Done means: the newest build is in
`SUPPORTED_BUILDS`, every check below passes, and a release is drafted.

## Rules

- Anchors fail closed. Never weaken a check, count, or hash to make a build
  pass; find the new code instead.
- Support the newest three builds. Adding one removes the oldest profile, its
  `SUPPORTED_BUILDS` entry, its `COMPATIBILITY.md` row, its source copy, and
  any code only it used.
- Never commit an app, archive, credential, or account state.
- Never quit, relaunch, or install over Henry's running app without his "go"
  in the same message. Unattended runs stop at a pull request.

## 1. Detect and fetch

The daily `Upstream` workflow fails when a new build needs a port. Locally:

```sh
python3 scripts/appcast.py --fetch ~/.codex-mux/sources
```

Exit 0 means the newest build is already supported: stop. Exit 10 means port
it. The script prints the unpacked app's path.

## 2. Port the profile

```sh
python3 scripts/port_renderer.py --source <app> > /tmp/profile.py
```

It matches every anchor of the newest profile by shape and prints a ready
`RENDERER_BUILD_<build>` plus a list of what it could not resolve. Paste the
profile after the newest one, add it to `RENDERER_BUILDS`, and add the build to
`SUPPORTED_BUILDS` (hash from step 3). Then fix only what it listed:

- Find the moved code by a string literal or property name from the old
  anchor, and copy the new anchor verbatim from the bundle. Never retype it.
- Make each anchor long enough to be unique by shape, not just by text.
- Borrow only imports, hoisted functions, or variables of a module the patched
  component itself initializes. Otherwise draw it (see the usage icon) or read
  it through a live import.
- Give every borrowed identifier a probe in `identifier_probes`, taken from a
  usage site that is unique by shape, never from an import clause.
- Our UI lives in the eager `app-initial` bundle next to `menu_anchor`; lazy
  chunks only call it through `globalThis`. Patches land in whichever bundle
  holds their anchor, so moved code needs a new anchor, not a new mechanism.
- When main-process code or packaging changes shape, teach the one helper
  both shapes instead of branching per build (see `codex_entrypoint` and
  `attach_router_updater`).

The port is finished when the new build ports onto itself cleanly:
`port_renderer.py --source <app> --reference <build>` exits 0.

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
exits 0 only once the renderer runs and the app-server answers, and prints the
crash otherwise. Electron hardening (fuses, integrity seals, signing) only
shows up here. Every entrypoint must launch too: the router, `codex.real` next
to it, and `codex-cli/bin/codex` print the Codex version. Then run the live
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
3. `npm run release:check`, push, and let CI pass.
4. Tag `v<version>` and push the tag; the release workflow drafts the release.
   Summarize the upstream Codex changelog since the last port for Henry.
   Publishing the draft ships it to every updater, so it waits for his go.

## 5. Housekeeping

Delete by exact path: source apps in `~/.codex-mux/sources` that are no longer
supported, and scratch extractions. The patcher keeps a single install backup
and the updater prunes release sources and older official builds. Leave the
diff smaller than you found it.

## Keeping this skill current

If a step here was wrong, missing, or unnecessary, fix this file in the same
commit. Replace text rather than appending, and keep it under 120 lines.
