#!/usr/bin/env python3
"""Derive a renderer profile for a new ChatGPT build from a supported one.

Every ChatGPT release renames the minified identifiers the patcher anchors
on while the code around them rarely changes. Each anchor of the reference
build is turned into a pattern whose identifiers are wildcards that must
repeat consistently, matched exactly once across the new build's renderer
bundles, and the captured identifiers rewrite the anchors, replacements,
and injected-source identifier maps into a profile for the new build.

    python3 scripts/port_renderer.py --source /path/to/ChatGPT.app [--reference BUILD]

The reference defaults to the newest supported build.

The result is printed for review; a profile is added to patch_app.py by
hand, and the patcher still refuses a build whose asar hash is untested.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
KEEP = {
    "function", "return", "let", "const", "var", "if", "else", "for", "of", "in",
    "new", "null", "void", "typeof", "await", "async", "true", "false", "this",
    "throw", "try", "catch", "children", "className", "Symbol", "Error", "Promise", "JSON",
    "Object", "Math", "Map", "Set", "window", "document", "globalThis",
    "undefined", "e", "t", "n", "r", "i", "a", "o", "s", "c", "l", "u", "d",
    "f", "p", "m", "h", "g", "_", "v", "y", "b", "x", "S", "C", "w", "T", "E",
    "D", "O", "k", "A", "j", "M", "N", "P", "F", "I", "L", "R", "z", "B", "V",
    "H", "U", "W", "G", "K", "q", "J", "Y", "X",
}
KEEP_PREFIXES = ("codexMux", "CodexMux", "__codexMux")


def tokens(source: str) -> list[tuple[str, str]]:
    """Split JavaScript into (kind, text) with strings kept whole."""
    out: list[tuple[str, str]] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char in "`'\"":
            end = index + 1
            while end < length and source[end] != char:
                end += 2 if source[end] == "\\" else 1
            out.append(("string", source[index : end + 1]))
            index = end + 1
            continue
        match = IDENTIFIER.match(source, index)
        if match:
            out.append(("identifier", match.group(0)))
            index = match.end()
            continue
        out.append(("punct", char))
        index += 1
    return out


def is_minified(parts: list[tuple[str, str]], position: int) -> bool:
    """A local name the minifier owns: not a keyword, property, or key."""
    kind, text = parts[position]
    if kind != "identifier" or text in KEEP or text.startswith(KEEP_PREFIXES):
        return False
    if len(text) == 1 and text not in ("Q", "Z", "$"):
        return False
    previous = parts[position - 1][1] if position > 0 else ""
    if previous == ".":
        return False
    following = parts[position + 1][1] if position + 1 < len(parts) else ""
    before = parts[position - 2][1] if position > 1 else ""
    if following == ":" and previous in "{," and before != "?":
        return False
    return True


def pattern_for(anchor: str) -> tuple[re.Pattern[str], list[str]]:
    parts = tokens(anchor)
    names: list[str] = []
    # Start at an identifier boundary, not every suffix of a long embedded
    # string. Otherwise a leading wildcard retries quadratically on base64.
    regex: list[str] = [r"(?<![\w$])"] if parts and parts[0][0] == "identifier" else []
    for position, (kind, text) in enumerate(parts):
        if kind == "identifier" and is_minified(parts, position):
            if text in names:
                regex.append(f"(?P=i{names.index(text)})")
            else:
                names.append(text)
                regex.append(f"(?P<i{len(names) - 1}>[A-Za-z_$][\\w$]*)")
        else:
            regex.append(re.escape(text))
    return re.compile("".join(regex)), names


def rewrite(template: str, mapping: dict[str, str]) -> tuple[str, set[str]]:
    parts = tokens(template)
    out: list[str] = []
    unresolved: set[str] = set()
    for position, (kind, text) in enumerate(parts):
        if kind == "identifier" and is_minified(parts, position):
            if text in mapping:
                out.append(mapping[text])
            else:
                unresolved.add(text)
                out.append(text)
        else:
            out.append(text)
    return "".join(out), unresolved


class Port:
    def __init__(self, bundles: dict[str, str]) -> None:
        self.bundles = bundles
        self.mapping: dict[str, str] = {}
        self.conflicts: dict[str, set[str]] = {}
        self.problems: list[str] = []

    def learn(self, old: str, new: str) -> None:
        current = self.mapping.get(old)
        if current is None:
            self.mapping[old] = new
        elif current != new:
            self.conflicts.setdefault(old, {current}).add(new)

    def locate(self, anchor: str, label: str) -> tuple[str, str] | None:
        pattern, names = pattern_for(anchor)
        hits: list[tuple[str, re.Match[str]]] = []
        for name, text in self.bundles.items():
            hits.extend((name, match) for match in pattern.finditer(text))
        if len(hits) != 1:
            self.problems.append(f"{label}: {len(hits)} matches for {anchor[:70]!r}")
            return None
        bundle, match = hits[0]
        for index, old in enumerate(names):
            self.learn(old, match.group(f"i{index}"))
        return bundle, match.group(0)

    def port_text(self, template: str, label: str) -> str:
        text, unresolved = rewrite(template, self.mapping)
        if unresolved:
            self.problems.append(f"{label}: unresolved identifiers {sorted(unresolved)}")
        return text

    def port_pair(self, pair: tuple[str, str], label: str) -> tuple[str, str]:
        located = self.locate(pair[0], label)
        anchor = located[1] if located else self.port_text(pair[0], label)
        return anchor, self.port_text(pair[1], label + " replacement")

    def port_identifiers(self, table: dict[str, str], label: str) -> dict[str, str]:
        # Values are identifiers or small expressions such as `Ur()`.
        return {
            placeholder: self.port_text(old, f"{label} {placeholder}")
            for placeholder, old in table.items()
        }


def load_patcher():
    spec = importlib.util.spec_from_file_location(
        "patch_app", PROJECT_ROOT / "scripts" / "patch_app.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def renderer_bundles(app: Path) -> dict[str, str]:
    asar = app / "Contents" / "Resources" / "app.asar"
    with tempfile.TemporaryDirectory() as scratch:
        subprocess.run(
            [str(PROJECT_ROOT / "node_modules" / ".bin" / "asar"), "extract", str(asar), scratch],
            check=True,
            capture_output=True,
        )
        assets = Path(scratch) / "webview" / "assets"
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(assets.glob("*.js"))
        }


def build_number(app: Path) -> str:
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    return info["CFBundleVersion"]


def render(build: str, profile: dict[str, object], reference) -> str:
    """The profile as Python source, in the patcher's field order."""
    lines = [f"RENDERER_BUILD_{build} = RendererBuild("]
    for field in dataclasses.fields(reference):
        value = profile.get(field.name, getattr(reference, field.name))
        lines.append(f"    {field.name}={python_literal(value, 1)},")
    lines.append(")")
    return "\n".join(lines)


def python_literal(value: object, depth: int) -> str:
    pad, inner = "    " * depth, "    " * (depth + 1)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return "None"
    if isinstance(value, dict):
        items = "".join(
            f"{inner}{json.dumps(k)}: {python_literal(v, depth + 1)},\n" for k, v in value.items()
        )
        return "{\n" + items + pad + "}"
    items = "".join(f"{inner}{python_literal(v, depth + 1)},\n" for v in value)
    return "(\n" + items + pad + ")"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--reference", help="supported build to port from (default: newest)")
    args = parser.parse_args()

    patcher = load_patcher()
    reference = (
        getattr(patcher, f"RENDERER_BUILD_{args.reference}")
        if args.reference
        else patcher.RENDERER_BUILDS[-1]
    )
    bundles = renderer_bundles(args.source)
    port = Port(bundles)
    profile: dict[str, object] = {}

    marker = port.locate(reference.marker, "marker")
    profile["marker"] = marker[1] if marker else reference.marker
    data = port.locate(reference.data_anchor, "data_anchor")
    profile["data_anchor"] = data[1] if data else reference.data_anchor
    menu = port.locate(reference.menu_anchor, "menu_anchor")
    profile["menu_anchor"] = menu[1] if menu else reference.menu_anchor

    # Probes first: replacements may borrow identifiers only probes name.
    profile["identifier_probes"] = tuple(
        (port.locate(probe, "identifier_probes") or (None, probe))[1]
        for probe in reference.identifier_probes
    )
    for field in (
        "usage_slot", "plugin_request", "reset_query", "reset_mutation",
        "usage_header", "profile_avatar", "profile_name", "profile_identity",
        "plugin_scope", "thread_sections",
    ):
        profile[field] = port.port_pair(getattr(reference, field), field)
    profile["plugin_request_checks"] = tuple(
        (port.locate(check, "plugin_request_checks") or (None, check))[1]
        for check in reference.plugin_request_checks
    )
    for field in ("usage_modal", "usage_windows"):
        located = port.locate(getattr(reference, field), field)
        profile[field] = located[1] if located else getattr(reference, field)
    thread = port.locate(reference.thread_anchor, "thread_anchor")
    profile["thread_anchor"] = thread[1] if thread else reference.thread_anchor
    profile["composer_actions"] = tuple(
        (port.locate(anchor, "composer_actions") or (None, anchor))[1]
        for anchor in reference.composer_actions
    )
    profile["fork_titles"] = port.port_pair(reference.fork_titles, "fork_titles")
    profile["usage_status"] = port.port_pair(reference.usage_status, "usage_status")

    profile["menu_identifiers"] = port.port_identifiers(
        reference.menu_identifiers, "menu_identifiers"
    )
    profile["thread_identifiers"] = port.port_identifiers(
        reference.thread_identifiers, "thread_identifiers"
    )
    profile["fork_identifiers"] = port.port_identifiers(
        reference.fork_identifiers, "fork_identifiers"
    )

    print(render(build_number(args.source), profile, reference))
    if port.conflicts:
        print("\nconflicting identifier captures:", file=sys.stderr)
        for old, seen in sorted(port.conflicts.items()):
            print(f"  {old}: {sorted(seen)}", file=sys.stderr)
    if port.problems:
        print("\nunresolved:", file=sys.stderr)
        for problem in port.problems:
            print(f"  {problem}", file=sys.stderr)
    return 1 if port.problems or port.conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
