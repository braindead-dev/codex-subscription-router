# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.19.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1007.30912` | `20156` | `0.162.0-alpha.17.2` | `75e397e2280dde93b70a85aa926f4282c728eacc84dfeb15ba7fb6ddd84628af` |
| `26.1007.41230` | `20324` | `0.162.0-alpha.17.2` | `42da0dd3694b3eb3c79ebd243b14fa5cc6dfcd4d5c71d92d2aae1f993cb5ea43` |
| `26.1007.71326` | `20398` | `0.162.0-alpha.17.2` | `2f231a64e6dd30111bcd09e03c149cd06c8679b25914842772423251c4608ded` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
