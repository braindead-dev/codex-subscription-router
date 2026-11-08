# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.28.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1103.31037` | `25241` | `0.167.0-alpha.3.2` | `57602118a9890cb62ddfa750996e41440396d0aac2725b6837b5391305c78acf` |
| `26.1103.32239` | `25471` | `0.167.0-alpha.3.2` | `8e55cbc7f0b7797ca1972cbf6d8b37f26dd8b79d0613235bc7448905a3bc7a2b` |
| `26.1103.41825` | `25679` | `0.167.0-alpha.3.2` | `766faa8a3da8f6d6902c751d5b6b29395a6c5bf3c966ef7f8ca3f629d931df85` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
