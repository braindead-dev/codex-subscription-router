# Releasing

Releases are source-only. Never attach a patched app, ASAR, extracted official
file, signing material, or account data.

1. Bump the minor version in `VERSION`, `package.json`, and both version
   fields of `package-lock.json`. Each newly supported official build is a
   minor release.
2. Move the Unreleased changelog entries under `## [x.y.z] - YYYY-MM-DD` and
   add the release link at the bottom.
3. Rewrite the `## Release x.y.z` table in `docs/COMPATIBILITY.md` with the
   supported builds.
4. Run `npm ci --ignore-scripts`, `npm run check`, `npm run release:check`,
   and `python3 scripts/verify_build.py` on each supported build.
5. Push, wait for CI, then tag the commit `vX.Y.Z` and push the tag. The
   release workflow repeats the checks and drafts a GitHub release with
   generated notes; review and publish it. Publishing is the rollout: every
   Mac with `scripts/update.py enable` builds it within the hour and offers
   it in the app.
