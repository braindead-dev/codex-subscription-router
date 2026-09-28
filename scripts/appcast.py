#!/usr/bin/env python3
"""Report the newest official ChatGPT macOS build and optionally fetch it.

    python3 scripts/appcast.py                 # newest build, and whether the patcher knows it
    python3 scripts/appcast.py --fetch DIR     # also unpack it to DIR/ChatGPT-<build>.app

Exits 0 when the newest build is supported and 10 when it still needs a port.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ElementTree
from pathlib import Path

APPCAST = "https://persistent.oaistatic.com/codex-app-prod/appcast.xml"
SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NEEDS_PORT = 10
# The CDN refuses Python's default client name.
HEADERS = {"User-Agent": "codex-subscription-router/appcast"}


def builds() -> list[tuple[str, str, str]]:
    """Every (version, build, archive url) the appcast offers, newest first."""
    request = urllib.request.Request(APPCAST, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        channel = ElementTree.fromstring(response.read()).find("channel")
    items = []
    for item in channel.findall("item"):
        build = item.findtext(f"{SPARKLE}version") or ""
        version = item.findtext(f"{SPARKLE}shortVersionString") or ""
        enclosure = item.find("enclosure")
        if build.isdigit() and enclosure is not None:
            items.append((int(build), version, enclosure.get("url", "")))
    return [(version, str(build), url) for build, version, url in sorted(items, reverse=True)]


def newest_build() -> tuple[str, str, str]:
    return builds()[0]


def supported(version: str, build: str) -> bool:
    spec = importlib.util.spec_from_file_location(
        "patch_app", PROJECT_ROOT / "scripts" / "patch_app.py"
    )
    patcher = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = patcher
    spec.loader.exec_module(patcher)
    return (version, build) in patcher.SUPPORTED_BUILDS


def fetch(url: str, build: str, directory: Path) -> Path:
    target = directory / f"ChatGPT-{build}.app"
    if target.exists():
        return target
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as scratch:
        archive = Path(scratch) / "ChatGPT.zip"
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=HEADERS), timeout=900
        ) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
        unpacked = Path(scratch) / "unpacked"
        # ditto keeps the bundle's symlinks and modes; unzip is the non-macOS fallback.
        if shutil.which("ditto"):
            subprocess.run(["ditto", "-x", "-k", str(archive), str(unpacked)], check=True)
        else:
            subprocess.run(["unzip", "-q", str(archive), "-d", str(unpacked)], check=True)
        (unpacked / "ChatGPT.app").rename(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", type=Path, metavar="DIR")
    args = parser.parse_args()

    version, build, url = newest_build()
    known = supported(version, build)
    print(f"newest: {version} ({build}) {'supported' if known else 'needs a port'}")
    if args.fetch:
        print(f"source: {fetch(url, build, args.fetch.expanduser())}")
    return 0 if known else NEEDS_PORT


if __name__ == "__main__":
    raise SystemExit(main())
