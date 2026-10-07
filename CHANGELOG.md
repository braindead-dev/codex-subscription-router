# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versioning:
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.14.0] - 2026-10-07

### Added

- Compatibility with official ChatGPT builds `13536` (26.1002.52244,
  Codex 0.162.0-alpha.2) and `13520` (26.930.61225, Codex 0.160.1).
  Build `13232` remains supported; builds `13022` and `13100` are retired.

## [0.13.0] - 2026-10-06

### Added

- Compatibility with official ChatGPT build `13232` (26.930.61225),
  bundling Codex 0.160.1. Builds `13022` and `13100` remain supported;
  build `12947` is retired.

## [0.12.0] - 2026-10-05

### Added

- Compatibility with official ChatGPT build `13100` (26.930.51102),
  bundling Codex 0.160.0. Builds `12947` and `13022` remain supported;
  build `12913` is retired.

## [0.11.0] - 2026-10-04

### Added

- Compatibility with official ChatGPT build `13022` (26.930.41038),
  bundling Codex 0.160.0. Builds `12913` and `12947` remain supported;
  build `12776` is retired.

## [0.10.0] - 2026-10-03

### Added

- Compatibility with official ChatGPT builds `12913` (26.930.31428) and
  `12947` (26.930.31730), both bundling Codex 0.160.0. Build `12776`
  remains supported; builds `12553` and `12694` are retired.

## [0.8.1] - 2026-10-02

### Changed

- Moved Models and automatic-update controls from the profile menu into
  Settings > General, using native settings rows, button, and switch.
  Model management and update status remain available in the new section.

## [0.8.0] - 2026-10-02

### Added

- Compatibility with official ChatGPT builds `12694` (26.928.40906, Codex
  0.159.2) and `12776` (26.930.21537, Codex 0.159.0-alpha.12.1).
  Build `12553` remains supported; builds `12246` and `12404` are retired.
- Staged boot checks target the staged CLI and verify the rendered UI directly.

## [0.6.0] - 2026-10-01

### Added

- Compatibility with official ChatGPT build `12553` (26.928.31416), which
  continues to bundle Codex 0.159.2. Builds `12246` and `12404` remain supported.

### Removed

- Compatibility with build `12111`, outside the newest three official builds.

## [0.5.0] - 2026-09-30

### Added

- Compatibility with official ChatGPT build `12404` (26.928.21956), which
  bundles Codex 0.159.2.
- Compatibility with build `12111` (26.924.51851) to cover the newest three
  official builds alongside `12246`.

### Changed

- The `CODEX_MUX_TRACE` log names the MCP server, tool, or resource each
  plugin request targets, without its arguments.

### Removed

- Compatibility with build `11645`.

## [0.4.4] - 2026-09-29

### Fixed

- Chats on a subscription other than Primary lost every notification outside
  a few thread and turn prefixes, including MCP server startup status, MCP
  app event streams, resolved approvals, errors, warnings, and process
  output. Code Review, which opens its own chat on the subscription new chats
  start on, could not load pull requests. Notifications about a chat, or
  answering a request routed to that subscription, now reach the app; only
  account-wide ones stay limited to Primary.

### Added

- `CODEX_MUX_TRACE` records the multiplexer's routing and replies for
  debugging; see CONTRIBUTING.md.

## [0.4.3] - 2026-09-29

### Fixed

- Chats bypassed the router since build 11645. The desktop's startup
  preflight became the multiplexer, and ignored the signal that ends it
  because its stdin stays open, so it kept the slot and the chat connection
  that starts a second later ran as a plain app-server on Primary: no
  pooling, failover, or subscription choice. The multiplexer now hands back
  the slot and control port on that signal, and a connection that finds the
  slot claimed moments ago waits for it.
- Moving or inspecting a chat the router never saw start failed with "has no
  subscription assignment"; such a chat now belongs to the account whose home
  holds it.

## [0.4.2] - 2026-09-29

### Fixed

- Choosing the subscription new chats start on failed with "Failed to fetch":
  the control API's CORS preflight did not allow the `PUT` the composer
  account picker sends. A test now checks every method the renderer sends.

## [0.4.1] - 2026-09-29

### Added

- A **Models…** row in the profile menu when the Primary Codex home sends
  OpenAI traffic through a local proxy such as opencodex; it opens that
  proxy's model page, where the models the picker shows are chosen.

## [0.4.0] - 2026-09-29

### Added

- Compatibility with official ChatGPT build `12246` (26.928.20755), which
  bundles Codex 0.159.0, the first version the backend offers GPT-6.1 Sol.
- The usage-window selection and the rate-limit banner titles are per-build
  profile fields, since 12246 moved the first and dropped the banner.

### Changed

- Only the newest official build is ported; intermediate builds nobody
  installs are skipped.

### Removed

- Compatibility with builds `8881` and `10492`.

## [0.3.3] - 2026-09-29

### Fixed

- Build `11645` showed the model picker twice in the composer: its footer
  patch stored a hand-kept replacement that named the wrong element. Composer
  patches now keep only the anchor and insert the account picker into it, so
  a port can no longer get the replacement wrong.

## [0.3.2] - 2026-09-28

### Fixed

- Clicking **Update available** without a staged build closed the app for
  good; the updater now reopens it whether or not the install went through.
- Each launch check left the staged app's modifier-key monitor running.

## [0.3.1] - 2026-09-28

### Fixed

- Installing an update failed while the desktop's modifier-key monitor,
  which outlives the app, was still running. It and Chromium's crash
  reporters no longer count as a running app, and an install ends the ones
  the replaced bundle left behind.
- The updater agent ran a version-pinned Python path that breaks when
  Homebrew upgrades Python; it now uses `python3` from `PATH`.

## [0.3.0] - 2026-09-28

### Added

- Updates from this repository's published releases. `scripts/update.py
  enable` installs a launch agent that builds each new release on the Mac
  against the newest official build it supports, boots it with
  `launch_check.py`, and hands it to the app's own update UI: the
  **Update available** button, **Check for Updates…**, and the install
  confirmation. Installing quits, swaps, and relaunches the app; with
  **Update automatically** checked in the profile menu, a ready update
  installs when the app quits. Old release sources, staged builds, and
  unsupported official builds are removed as it goes.
- `patch_app.py --stage` builds for an installed app while it keeps running,
  and `--install-staged` swaps that build in once the app has quit.

### Removed

- `patch_app.py --discard-existing`; stage with `--stage` instead, and delete
  a broken install by path before replacing it.
- Unused code: five unreachable Go functions, an unused avatar component, and
  a porting helper.

### Fixed

- The installer's running-app check never matched a path with parentheses,
  such as `Codex (router).app`, and counted Chromium's lingering crash
  reporters as a running app.

## [0.2.1] - 2026-09-28

### Fixed

- Build `11645` crashed at launch. Its Electron seals `Info.plist`'s ASAR
  integrity entry with a digest compiled into the framework, so the patcher
  now restamps that digest for the repacked archive and re-signs the
  framework and its helpers.
- Installs no longer keep a copy of the replaced Computer Use helper in
  `~/.codex/computer-use`.

### Added

- `scripts/launch_check.py` boots a staged app until its window talks to
  Codex, and `verify_build.py` fails on an integrity seal format it does not
  know.

## [0.2.0] - 2026-09-27

### Added

- Compatibility with official ChatGPT builds `8881`, `10492`, and `11645`
  (Codex 0.154 to 0.158); builds `6396`, `6662`, and `7746` are no longer
  supported. Renderer patches land in whichever bundle holds their anchor,
  and the patcher wraps the Codex CLI whether it ships loose or as the nested
  `CodexCLI.app`.
- A composer account picker: choose the subscription a chat runs on, move a
  chat there with its history, or pin new chats to one subscription.
- Purchased credits count as capacity: an account holding credits keeps
  routing, and the menus show its balance instead of calling it depleted.
- Forks get their own generated title after their first turn.
- Porting tooling: `scripts/appcast.py` detects and fetches new official
  builds, `scripts/port_renderer.py` derives a build profile from the newest
  supported one, `scripts/verify_build.py` applies every app.asar patch and
  parses the result, `scripts/live_seed.py` prepares the live move test, and
  the `port-chatgpt-build` agent skill describes the whole procedure.
- Model-aware routing: a new chat that names an OpenAI model goes to a
  subscription the backend lets run it, and switching an existing chat to a
  model its subscription cannot run is refused with the subscriptions that
  can, instead of the backend's error. Models served through another
  provider are not gated.
- One-command installer with prerequisite checks, signed rebuilds, recoverable
  upgrades, and automatic launch.
- Reset-aware routing that prioritizes weekly quota at risk of expiring and
  gives a bounded boost to subscriptions with banked usage resets.
- Remote-control pairing per subscription from the profile menu, backed by
  `/v1/accounts/{id}/remote-control` control routes.
- `CODEX_MUX_DISPLAY_NAME` sets the Dock and menu bar name of the copied app
  without changing its paths, identifiers, or desktop profile.
- Optional unified thread catalog (flag `~/.codex-mux/unified-catalog.enabled`,
  default off): every connected subscription's index lists the pool's threads,
  so remote control from a phone signed into any account can see and resume any
  session. Turns still run on, and bill, the connected account only. The
  reconciler clones existing rows insert-only and never modifies another
  account's data. On Codex 0.153 and later, whose thread store keeps a
  per-account history projection, the flag is refused and logged once:
  mirrored threads would be unusable there or corrupt the shared rollout.

### Changed

- Native usage surfaces (limit banner, sidebar alert, reset prompts) reflect
  pooled usage, so a depleted Primary account no longer triggers them while
  another subscription still has weekly capacity.
- The account menu, Usage sheet, and Plugins picker open with the last known
  subscriptions and refresh in place instead of showing a connecting state.
- Turn routing reads recently observed account snapshots, kept current by the
  children's rate-limit notifications, instead of querying every app-server
  before each turn.
- Isolated subscriptions inherit the Primary account's project trust; entries
  they recorded themselves take precedence.

### Fixed

- Every subscription can use the installed plugins: isolated homes share the
  Primary home's plugin package cache through a link (the old cache is set
  aside), so a plugin enabled in the shared config no longer sits uninstalled
  on another account.
- Unfinished subscription sign-ins no longer pile up in the account list: an
  added subscription that never completed sign-in is removed after an hour.
- Codex 0.153 resumes a thread only from a rollout inside the account's own
  sessions directory, so moving a chat to another subscription now hard-links
  the rollout there instead of resuming it by its original path. The target
  receives what a native home holds: the chat's index row, every rollout
  file (including revert continuations), and every history projection
  stream, taken from the owner's caught-up copy, and resumes by id. A session
  the target still holds from an earlier move cannot be reused, since its
  stale numbering would corrupt the shared rollout, and the turn fails with
  an explanation until the app restarts.
- Features the desktop enables at runtime, such as the paginated thread
  history migration, reach every subscription instead of the controller only;
  without it other accounts answered `list_turns is not supported yet`.
- Pinned chats reorder again, across subscriptions: the multiplexer keeps
  the one pinned order the sidebar shows, applies each move in the account
  whose copy is listed with an anchor that account knows, the merged listing
  keeps that copy's section even when another subscription holds a fresher
  copy, and a thread read reports that same pin so the desktop lets the drag
  start.
- Ad-hoc signed builds can use the in-app Browser and Computer Use: the
  desktop rejected the unsigned bundled `node_repl` on its native pipes with
  `missing-code-signing-identity`, so every subscription saw zero browsers.
- Isolated subscriptions see the Primary home's `AGENTS.md`, `agents/`,
  `hooks.json`, and `skills/`.
- Thread listings no longer flip a moved thread's owner back to the account
  whose history still contains it, so steers and follow-ups reach the account
  running the turn. Moved threads are listed once, with activity from the
  freshest copy, the generated title from the originating account, and a
  user-assigned name from whichever copy received it.
- Threads created on another subscription in a trusted folder no longer run
  read-only with approvals because that account had not recorded the trust.
- The copied app can no longer start Sparkle through the renderer's update
  gate or the Check for Updates menu item.
- Profile menus dismiss normally on outside clicks and Escape after an
  additional subscription sign-in.

## [0.1.0] - 2026-08-15

### Added

- Multi-subscription routing with quota-aware balancing and sticky threads.
- Account isolation, device-code sign-in, pooled usage, and quota failover.
- Native account menu, masked emails, plan labels, and profile photos.
- Combined Profile statistics with per-account selection.
- Account-scoped Apps and MCP connection state in Settings → Plugins.
- Per-account rate-limit reset selection and pooled depletion handling.
- Independently signed Appshots and Computer Use support.
- Fail-closed upstream compatibility checks and deepest-first nested helper signing.
- Loopback-only, token-authenticated diagnostic UI states.
- Source-only CI, draft release automation, security documentation, and smoke tests.

[Unreleased]: https://github.com/braindead-dev/codex-subscription-router/compare/v0.14.0...HEAD
[0.14.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.14.0
[0.13.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.13.0
[0.12.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.12.0
[0.11.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.11.0
[0.10.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.10.0
[0.8.1]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.8.1
[0.8.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.8.0
[0.6.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.6.0
[0.5.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.5.0
[0.4.4]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.4.4
[0.4.3]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.4.3
[0.4.2]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.4.2
[0.4.1]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.4.1
[0.4.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.4.0
[0.3.3]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.3.3
[0.3.2]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.3.2
[0.3.1]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.3.1
[0.3.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.3.0
[0.2.1]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.2.1
[0.2.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.2.0
[0.1.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.1.0
