# Codex Subscription Router

![Multi-subscription account menu](screenshots/account-menu.png)

Use several ChatGPT subscriptions from one macOS Codex app.

The router builds a locally patched copy of the official ChatGPT app, spreads
new chats across your connected subscriptions, fails a depleted thread over to
one with quota, and keeps every thread pinned to one account so context and
caching survive. The official install is read, never modified.

This is the maintained continuation of the original
[codex-subscription-router](https://github.com/b-nnett/codex-subscription-router),
which is archived.

> [!WARNING]
> Unofficial and version-sensitive. Not affiliated with OpenAI. Make sure your
> use complies with the terms of every connected subscription.

![Combined multi-account profile](screenshots/combined-profile-20px.png)

## What you get

- **Quota-aware routing** — new chats go to the subscription whose weekly
  allowance is most at risk of going unused, with a bounded boost for banked
  resets.
- **Sticky threads with failover** — follow-ups stay on their account; a
  depleted owner hands the thread to one with capacity. Only when the whole
  pool is empty does the app show a limit.
- **Pooled usage everywhere** — the profile menu, usage banners, sidebar
  alert, and reset prompts describe the pool, not just the Primary account.
- **Native account management** — add subscriptions with device-code sign-in,
  see per-account usage and plan, pick an account for rate-limit resets,
  Apps and MCP connections, and Profile statistics.
- **Remote control per subscription** — pair a phone or another computer to
  any account, not only Primary.
- **Independent app** — its own bundle ID, desktop profile, and signed
  Computer Use helper, so it coexists with the official app and holds its own
  macOS privacy grants.

## How it works

```text
Codex Subscription Router.app
        │  one app-server connection
        ▼
    codex-mux
    ├── Primary        → ~/.codex
    ├── Subscription 2 → ~/.codex-mux/accounts/<id>/codex-home
    └── Subscription 3 → ~/.codex-mux/accounts/<id>/codex-home
             └── thread ID → persistent account owner
```

A small Go multiplexer sits between the desktop and one official Codex
app-server per account. Each account has an isolated Codex home; the
multiplexer records which account owns each thread and routes accordingly.
Details: [architecture](docs/ARCHITECTURE.md), [security model](docs/SECURITY-MODEL.md).

## Requirements

- macOS on Apple silicon, with the official ChatGPT app at `/Applications/ChatGPT.app`
  — supported builds are listed in [COMPATIBILITY.md](docs/COMPATIBILITY.md)
- Xcode Command Line Tools, Go 1.26+, Node.js 22.12+ with npm
- An Apple Development or Developer ID Application identity. Ad-hoc signing
  (`--allow-adhoc-signing`) works too: the copy then accepts its own
  unsigned `node_repl` on the owner-only browser and Computer Use pipes, but
  macOS may not persist Appshots and Computer Use privacy grants.

The patcher verifies the official version, build, ASAR hash, and every code
anchor before changing anything, and refuses unknown builds rather than
half-patching them.

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/braindead-dev/codex-subscription-router/main/install.sh | /bin/bash
```

The installer keeps its checkout in `~/.codex-subscription-router/source`,
reuses existing account state on upgrades, backs up the previous build, and
requires the same signing team across rebuilds so privacy grants stay valid.
Read [`install.sh`](install.sh) first if you prefer.

From a clone:

```sh
git clone https://github.com/braindead-dev/codex-subscription-router.git
cd codex-subscription-router
npm ci --ignore-scripts
python3 scripts/patch_app.py
open "$HOME/Applications/Codex Subscription Router.app"
```

Useful options:

| Setting | Effect |
| --- | --- |
| `CODEX_MUX_SIGNING_IDENTITY="Developer ID Application: … (TEAMID)"` | Pick a certificate explicitly |
| `CODEX_MUX_DISPLAY_NAME="Codex (router)"` | Dock and menu bar name; paths and identifiers are unchanged |
| `--allow-adhoc-signing` | Build without a certificate |
| `--force` | Rebuild over an existing install (previous copy goes to `~/.codex-mux/backups`) |
| `--allow-signing-team-change` | Deliberately rebuild under a different Apple team |

The build creates `~/Applications/Codex Subscription Router.app`, its Computer
Use helper, and a desktop profile under
`~/Library/Application Support/Codex Subscription Router`. Bundles embed
per-user paths; build them on the machine that runs them.

### macOS permissions

**System Settings → Privacy & Security**: grant **Accessibility** to
*Codex Subscription Router* and **Screen & System Audio Recording** to
*Codex Subscription Router Computer Use* (add it with **+** if the row is
missing). These are separate rows from the official app's.

## Use

**Add a subscription** — profile menu (bottom of the sidebar) → *Add another
subscription* → finish the device-code sign-in in the browser. The menu shows
pooled weekly usage, then one row per account.

**Account actions** — select an account's row in the profile menu to reveal
*Copy email address* and *Pair a device…*. Pairing enables remote control for
that account and shows a short-lived code to enter on the phone or computer
(selecting it copies). OpenAI requires multi-factor authentication on the
account; the row says so if it is missing.

**Usage and resets** — the native usage sheet gets an account picker so a
reset is consumed only for the chosen subscription. Profile statistics can be
viewed pooled or per account; Settings → Plugins can scope Apps and MCP
connections to an account.

**Routing**

| Situation | Behaviour |
| --- | --- |
| New chat | Assigned by quota at risk, banked resets, short-window pressure |
| Follow-up, steer, rename | Sent to the thread's account |
| Owner depleted | Continued on another account with capacity |
| Every account depleted | One combined limit notice with the next reset |
| Account disabled | Excluded from routing and pooled usage |

The thread's subscription appears in its pinned summary.

## Update or rebuild

The copy never self-updates. Update `/Applications/ChatGPT.app`, check the new
build is listed in [COMPATIBILITY.md](docs/COMPATIBILITY.md), quit the router
and its Computer Use helper, then `python3 scripts/patch_app.py --force`.
Account state and credentials live outside the bundle and are untouched.

## Local data

| Path | Purpose |
| --- | --- |
| `~/.codex` | Primary account (shared with the official app) |
| `~/.codex-mux/accounts/<id>/codex-home` | Isolated secondary accounts |
| `~/.codex-mux/state.json` | Accounts and thread ownership |
| `~/.codex-mux/control-token` | Token for the loopback-only control API |
| `~/.codex-mux/backups` | Previous builds and recovery copies |
| `~/Library/Application Support/Codex Subscription Router` | Desktop profile |

The control API binds to `127.0.0.1` only and never returns OAuth tokens.
Managed config (MCP servers, plugins, project trust) is synchronized from the
Primary home to each account home, and `AGENTS.md`, `agents/`, `hooks.json`,
and `skills/` are linked to the Primary copies; credentials are not shared. Account homes are
therefore not a secret boundary for inline MCP secrets.

## Development

```sh
npm ci --ignore-scripts
npm run check          # go test/vet, JS, Python, shell
npm run release:check
```

No runtime third-party dependencies; `@electron/asar` is build-only. See
[CONTRIBUTING.md](CONTRIBUTING.md), [SMOKE-TEST.md](docs/SMOKE-TEST.md), and
[RELEASING.md](docs/RELEASING.md).

## Known limitations

- New official builds usually need reviewed anchor updates.
- Initial merged history is capped at 500 threads per account.
- Pooled "skills explored" can count a skill once per account.
- Builds are tied to one macOS user and signing team; releases are source-only.

## License

[MIT](LICENSE) for this project's source. ChatGPT, Codex, and the official app
are OpenAI products and are not covered.
