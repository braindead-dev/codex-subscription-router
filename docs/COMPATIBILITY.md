# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.6.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.928.20755` | `12246` | `0.159.0` | `2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee` |
| `26.928.21956` | `12404` | `0.159.2` | `3bda98f2265ad23677dfe0163d1cc7855beade6bef11d27f830f6663d7658406` |
| `26.928.31416` | `12553` | `0.159.2` | `9d4dda5c04d42e32cbd378557359c8c06fa798805a3c991f8a7b4b2295a8b732` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
