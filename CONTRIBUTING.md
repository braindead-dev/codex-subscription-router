# Contributing

## Setup

macOS on Apple silicon, Go 1.26+, Node.js 22.12+, npm, Xcode Command Line
Tools, and the official ChatGPT app installed.

```sh
npm ci --ignore-scripts
npm run check
npm run release:check
```

Never commit an app bundle, credentials, signing material, account state, or
captures with unmasked emails or device codes.

## Patches

Renderer and main-process patches match exact upstream anchors. Every change
must keep the official app untouched, fail closed when an anchor or binary
constant is missing, preserve account isolation and thread ownership, and keep
the control service on loopback behind its token. Test against the builds in
[COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Pull requests

One concern per PR, tests for backend behavior, and an explicit note on any
security-relevant behavior. CI runs Go tests and vet, JavaScript and Python
syntax checks, native C syntax, and release metadata consistency.

## Live move test

`TestLiveMoveKeepsHistoryOnBothAccounts` drives two real Codex app-servers and
runs short model turns on each. `python3 scripts/live_seed.py` builds its home
from a short chat of the signed-in account and prints `CODEX_MUX_LIVE_HOME`;
set `CODEX_MUX_LIVE_CODEX` to the real Codex binary (`codex.real` in a staged
app) and run `go test -run TestLiveMove ./internal/mux`.

## Tracing the router

With `launchctl setenv CODEX_MUX_TRACE ~/.codex-mux/logs/mux-trace.log` before
the app starts, the multiplexer appends one JSON line per routed request, its
reply or error, and each notification it drops. Lines carry methods, ids, and
accounts only. Unset the variable and delete the file when done.

## New official builds

Follow `.agents/skills/port-chatgpt-build/SKILL.md`.
