# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.5.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.924.51851` | `12111` | `0.158.0-alpha.2.1` | `6d18e3dcea8983b3f4d14933a6a5d20e2df609d6ad0aa3ad4a5727b1206b1213` |
| `26.928.20755` | `12246` | `0.159.0` | `2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee` |
| `26.928.21956` | `12404` | `0.159.2` | `3bda98f2265ad23677dfe0163d1cc7855beade6bef11d27f830f6663d7658406` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
