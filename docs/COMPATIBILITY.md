# Compatibility

The patcher is intentionally tied to known ChatGPT desktop bundle structures.
It verifies every modified renderer, main-process, and native binary anchor and
stops instead of applying a partial patch.

## Release 0.1.0

| Official ChatGPT version | Official bundle build | `app.asar` SHA-256 |
| --- | --- | --- |
| `26.803.61601` | `6396` | `d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf` |
| `26.810.52044` | `6662` | `6e7e8791b8bf69a586ff994721fff518af391d9efdc66cd2e620dd2a4aedc90f` |
| `26.814.41407` | `6720` | `8fba32f8baa6d984b0f0f4149d3da46221e3adb3b52836f85fe65e31e655a8c0` |

| Component | Tested value |
| --- | --- |
| Architecture | Apple silicon (`arm64`) |

A different official version may work when all anchors remain identical, but
it is unverified. The patcher rejects a version, build, or ASAR hash mismatch by
default; `--allow-untested-source` is an explicit diagnostic override. Never
weaken an anchor-count or binary-constant check merely to make a new build
complete. Review the upstream change and update the patch deliberately.

The local build-`6720` installation for this update uses an ad-hoc signature.
This record covers exact fail-closed patch compatibility only. The task did not
execute or prove multi-account OAuth, account switching, Appshots, or Computer
Use, and does not imply support for future ChatGPT builds.
