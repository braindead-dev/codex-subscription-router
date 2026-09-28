#!/usr/bin/env python3
"""Apply every app.asar patch to an official ChatGPT build and check the result.

    python3 scripts/verify_build.py --source ChatGPT-<build>.app

Runs the installer's own Computer Use, desktop profile, updater, and renderer
patches in a temporary directory, which fail closed on any moved anchor, then
syntax-checks every JavaScript file they changed. It also fails on a framework
ASAR integrity seal the installer cannot restamp. It needs Node and the pinned
@electron/asar but no signing identity, so it runs in CI and hosted agents.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        "patch_app", PROJECT_ROOT / "scripts" / "patch_app.py"
    )
    patcher = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = patcher
    spec.loader.exec_module(patcher)
    return patcher


def digests(root: Path) -> dict[Path, str]:
    return {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".js", ".cjs", ".mjs"}
    }


def syntax_error(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8")
    module = path.suffix == ".mjs" or "\nexport{" in text or text.startswith("import")
    command = ["node", "--check", str(path)]
    stdin = None
    if module and path.suffix != ".mjs":
        command = ["node", "--input-type=module", "--check"]
        stdin = text
    result = subprocess.run(command, input=stdin, capture_output=True, text=True)
    if result.returncode == 0:
        return None
    lines = result.stderr.strip().splitlines()
    return next((line for line in lines if "Error" in line), lines[0] if lines else "failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()

    patcher = load_patcher()
    app = args.source.expanduser().resolve()
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    key = (info["CFBundleShortVersionString"], info["CFBundleVersion"])
    asar_path = app / "Contents" / "Resources" / "app.asar"
    asar_hash = hashlib.sha256(asar_path.read_bytes()).hexdigest()
    spec = patcher.SUPPORTED_BUILDS.get(key, patcher.UNTESTED_BUILD)
    approved = spec.asar_sha256 == asar_hash
    print(f"source: {key[0]} ({key[1]}) app.asar {asar_hash}")
    print(f"listed in SUPPORTED_BUILDS: {'yes' if approved else 'no'}")
    framework = patcher.electron_framework(app)
    seal = patcher.asar_integrity_seal(
        (framework / "Versions" / "Current" / framework.stem).read_bytes()
    )
    print(f"framework ASAR integrity seal: {'present' if seal else 'none'}")

    with tempfile.TemporaryDirectory() as scratch:
        extracted = Path(scratch) / "asar"
        asar = patcher.ensure_asar_tool()
        subprocess.run([str(asar), "extract", str(asar_path), str(extracted)], check=True)
        before = digests(extracted)
        patcher.patch_asar_computer_use_identity(extracted, spec.asar_cua_identifier_replacements)
        patcher.patch_desktop_profile(
            extracted, Path(scratch) / patcher.COMPUTER_USE_APP_NAME
        )
        patcher.relax_native_pipe_peer_authorization(extracted)
        patcher.patch_renderer(extracted, "0" * 64)
        changed = [
            path for path, digest in digests(extracted).items() if before.get(path) != digest
        ]
        failures = [(path, error) for path in changed if (error := syntax_error(path))]

    print(f"patched and parsed {len(changed) - len(failures)}/{len(changed)} changed files")
    for path, error in failures:
        print(f"  syntax error in {path.name}: {error}")
    if failures or not approved:
        return 1
    print("verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
