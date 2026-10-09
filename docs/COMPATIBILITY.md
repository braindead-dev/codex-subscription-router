# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.15.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.930.61225` | `13520` | `0.160.1` | `2801c7cf820be653e302d6e6f4ac74a59df0ec219e078e31a83dd88f7aed9ffe` |
| `26.1002.52244` | `13536` | `0.162.0-alpha.2` | `40efd7acdf03a24817fcd7f35684fc2173b154df06774243cb4ab227e36fa915` |
| `26.1007.21159` | `20052` | `0.162.0-alpha.17.2` | `97b8e5fddfced82d782c5f3f3e7c5aba7940b8cb1068dd214ffa3d71687d9fdb` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
