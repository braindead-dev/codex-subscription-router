# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.12.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.930.31730` | `12947` | `0.160.0` | `87a934de9a00a04d2e534693db87756321ca4f3413f6caa55d3a0d32a5543836` |
| `26.930.41038` | `13022` | `0.160.0` | `60e98fe5dd78b34fb1db604c48c1018c56516663000529dce913b48c3d49300e` |
| `26.930.51102` | `13100` | `0.160.0` | `a159b8f5b78ed1ba89fc70d5c8448d822a46c4fc2a4a9f18ec348f3cc2f6b8c9` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
