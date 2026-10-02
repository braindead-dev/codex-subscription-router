# Compatibility

The patcher is tied to known ChatGPT desktop bundle structures. It verifies
every modified renderer, main-process, and native binary anchor and stops
instead of applying a partial patch. The newest three official builds
are supported.

## Release 0.8.0

| Official ChatGPT version | Bundle build | Codex CLI | `app.asar` SHA-256 |
| --- | --- | --- | --- |
| `26.928.31416` | `12553` | `0.159.2` | `9d4dda5c04d42e32cbd378557359c8c06fa798805a3c991f8a7b4b2295a8b732` |
| `26.928.40906` | `12694` | `0.159.2` | `ed1b376c509ba9c222fd9f8c1a29817eb53ff926679e48396f676ac674525db0` |
| `26.930.21537` | `12776` | `0.159.0-alpha.12.1` | `c662897ab25e819cd97a7981cf34d10eefcb9243095d527d27adff71bc18af0a` |

Architecture: Apple silicon (`arm64`).

A different official build is rejected by default; `--allow-untested-source`
is a diagnostic override only. Never weaken an anchor, count, or hash check to
make a new build complete. Port it with the `port-chatgpt-build` skill in
`.agents/skills/`.
