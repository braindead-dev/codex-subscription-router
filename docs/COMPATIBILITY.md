# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. Only the newest official build is
ported, and at most three builds stay supported.

## Release 0.4.4

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.924.22138` | `11645` | `0.158.0-alpha.2.1` | `d0ba973179d2f717affd39e012b64a095464a54a51c6bccb7bc6b3d2a1cfba80` |
| `26.928.20755` | `12246` | `0.159.0` | `2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
