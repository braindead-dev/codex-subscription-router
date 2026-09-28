# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds are
supported; porting a new one drops the oldest.

## Release 0.2.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.908.40834` | `8881` | `0.154.0-alpha.6.2` | `bb40cd8811887363104a19291346af9595632e0e956316a1086b274fb8e3eafc` |
| `26.917.51856` | `10492` | `0.155.0-alpha.16` | `55861ddbcc5d965642441e167a349fefcc932426c85b61a5d5b7a9a2fc639d70` |
| `26.924.22138` | `11645` | `0.158.0-alpha.2.1` | `d0ba973179d2f717affd39e012b64a095464a54a51c6bccb7bc6b3d2a1cfba80` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
