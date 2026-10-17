# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.21.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1007.71326` | `20398` | `0.162.0-alpha.17.2` | `2f231a64e6dd30111bcd09e03c149cd06c8679b25914842772423251c4608ded` |
| `26.1013.31256` | `20586` | `0.163.0-alpha.10.2` | `bf9c41fa9770fbfacfba93042ff71c24cb65c82935819377275faac08103383f` |
| `26.1013.40934` | `20770` | `0.163.0-alpha.10.2` | `5ddc4e48259aa6f979e04529b1a5a4ae54b0f5c4ad6db5c6e004924c10916ddb` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
