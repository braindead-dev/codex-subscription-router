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
[COMPATIBILITY.md](docs/COMPATIBILITY.md); if a new official build needs new
anchors, update that file in the same change.

## Pull requests

One concern per PR, tests for backend behavior, and an explicit note on any
security-relevant behavior. CI runs Go tests and vet, JavaScript and Python
syntax checks, native C syntax, and release metadata consistency.
