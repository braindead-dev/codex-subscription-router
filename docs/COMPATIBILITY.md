# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.10.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.930.21537` | `12776` | `0.159.0-alpha.12.1` | `c662897ab25e819cd97a7981cf34d10eefcb9243095d527d27adff71bc18af0a` |
| `26.930.31428` | `12913` | `0.160.0` | `46c5cc24a58b468c6015cc4e04bbcd8ff37c4b977765c3dbadb386ddf755eb94` |
| `26.930.31730` | `12947` | `0.160.0` | `87a934de9a00a04d2e534693db87756321ca4f3413f6caa55d3a0d32a5543836` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
