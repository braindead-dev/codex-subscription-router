# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.27.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.1023.50603` | `22391` | `0.165.0-alpha.1.2` | `94a7fac7fdf222261170204df0406249822431cf4db3627d5524b6ba033ce3e6` |
| `26.1023.51421` | `22530` | `0.165.0-alpha.1.2` | `1c8ef1bef66e5fc416ae66eff8e6529972aa3601e27a1c7b5f61d6a292b957d3` |
| `26.1027.30824` | `22943` | `0.166.0-alpha.5.1` | `36bf3d2b35e95e11852074b9ca9459b89efbecd6b8311345d060cf66de8f0ba6` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
