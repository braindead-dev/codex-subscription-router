# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.30.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1103.41825` | `25679` | `0.167.0-alpha.3.2` | `766faa8a3da8f6d6902c751d5b6b29395a6c5bf3c966ef7f8ca3f629d931df85` |
| `26.1103.60933` | `25894` | `0.167.0-alpha.3.2` | `a77588b2dcfa9b394d722dd8c2c8b13d3a09bacd49c839cc715245742dba6048` |
| `26.1106.51428` | `26605` | `0.167.0-alpha.4.1` | `3695c17edcab8d66c1e5f2faa03e07826a59a39bf78b369ec87854547d4843c1` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
