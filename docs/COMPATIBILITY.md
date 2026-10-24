# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.24.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1016.62342` | `21477` | `0.163.0-alpha.11.2` | `5b5cf52887cfd4de19cb5862155a8ef6cf58830c0a2fc592b72060cfcc3be869` |
| `26.1021.22027` | `21747` | `0.164.0-alpha.11.1` | `c288204a062a691a3f252d547a6bfea6b56f6e0b3122c46476dda35fcb68e787` |
| `26.1021.31345` | `21828` | `0.164.0-alpha.11.1` | `130145706a912db74e4d260ad7538f10f1e05e88af955f9afba42cdf01e64606` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
