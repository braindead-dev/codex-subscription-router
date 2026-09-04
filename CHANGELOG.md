# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versioning:
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Compatibility with official ChatGPT `26.810.52044` (build `6662`) and
  `26.901.22334` (build `7746`), whose renderer splits data access and UI
  across bundles.
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
  account's data.

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

- Codex 0.153 resumes a thread only from a rollout inside the account's own
  sessions directory, so moving a chat to another subscription now hard-links
  the rollout there instead of resuming it by its original path.
- Features the desktop enables at runtime, such as the paginated thread
  history migration, reach every subscription instead of the controller only;
  without it other accounts answered `list_turns is not supported yet`.
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

[Unreleased]: https://github.com/braindead-dev/codex-subscription-router/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/braindead-dev/codex-subscription-router/releases/tag/v0.1.0
