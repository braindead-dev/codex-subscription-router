# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.13.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.930.41038` | `13022` | `0.160.0` | `60e98fe5dd78b34fb1db604c48c1018c56516663000529dce913b48c3d49300e` |
| `26.930.51102` | `13100` | `0.160.0` | `a159b8f5b78ed1ba89fc70d5c8448d822a46c4fc2a4a9f18ec348f3cc2f6b8c9` |
| `26.930.61225` | `13232` | `0.160.1` | `88b8cce6f627771bf341f5a6bb464ad220749b0d442d44f618d7741c2de7318b` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
