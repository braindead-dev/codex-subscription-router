# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.23.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1013.40934` | `20770` | `0.163.0-alpha.10.2` | `5ddc4e48259aa6f979e04529b1a5a4ae54b0f5c4ad6db5c6e004924c10916ddb` |
| `26.1016.51333` | `21161` | `0.163.0-alpha.11` | `9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07` |
| `26.1016.61520` | `21421` | `0.163.0-alpha.11.1` | `c9945f821fcbaf48424933a82860beac4dd4ad8b8a5444a75568fa6e38a1ad4b` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
