# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and
this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Exact compatibility with official ChatGPT `26.814.41407` (build `6720`),
  selected through an explicit build variant with updated renderer and
  bootstrap anchors. Future ChatGPT builds require separate verification.
- Compatibility with official ChatGPT `26.810.52044` (build `6662`).
- One-command installer with safe source updates, prerequisite checks, signed
  rebuilds, recoverable upgrades, and automatic launch.
- Reset-aware routing that prioritizes weekly quota at risk of expiring and
  gives a bounded boost to subscriptions with banked usage resets.
- Remote-control pairing per subscription: selecting an account row in the
  profile menu enables remote control for that account and shows its pairing
  code, backed by `/v1/accounts/{id}/remote-control` control routes.

### Fixed

- Profile menus now dismiss normally on outside clicks and Escape after an
  additional subscription sign-in.
- Native usage surfaces (limit banner, sidebar usage alert, reset prompts)
  now reflect pooled usage, so a depleted Primary account no longer triggers
  them while another connected subscription still has weekly capacity.
### Changed

- The account menu, Usage sheet, and Plugins picker open with the last known
  subscriptions and usage and refresh in place instead of showing a connecting
  state on every open.
### Changed

- Turn routing reads recently observed account snapshots, kept current by the
  children's rate-limit notifications, instead of querying every app-server
  before each turn is forwarded.
- The copied app can no longer start Sparkle through the renderer's update
  gate or the Check for Updates menu item, so it does not offer to replace
  itself with an unpatched official build.
- Thread listings no longer reassign a moved thread back to the account whose
  history still contains it, so steering and follow-ups stay on the
  subscription that is running the turn. Moved threads are listed once, with
  activity from the most recently updated copy and the title from the account
  whose Codex home stores the thread, so they keep their place and name.

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
