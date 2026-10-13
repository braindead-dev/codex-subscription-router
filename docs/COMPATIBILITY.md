# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.17.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1007.21159` | `20052` | `0.162.0-alpha.17.2` | `97b8e5fddfced82d782c5f3f3e7c5aba7940b8cb1068dd214ffa3d71687d9fdb` |
| `26.1007.30912` | `20156` | `0.162.0-alpha.17.2` | `75e397e2280dde93b70a85aa926f4282c728eacc84dfeb15ba7fb6ddd84628af` |
| `26.1007.41230` | `20324` | `0.162.0-alpha.17.2` | `42da0dd3694b3eb3c79ebd243b14fa5cc6dfcd4d5c71d92d2aae1f993cb5ea43` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
