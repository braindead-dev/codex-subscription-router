#!/usr/bin/env python3
"""Create an independently signed ChatGPT.app copy with Codex multiplexing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
DEFAULT_SOURCE = Path("/Applications/ChatGPT.app")
DEFAULT_DESTINATION = Path.home() / "Applications" / "Codex Subscription Router.app"
DEFAULT_STATE_ROOT = Path.home() / ".codex-mux"
CONTROL_PORT = 48123
DESKTOP_PROFILE_NAME = "Codex Subscription Router"
# The name shown in the Dock, menu bar, and app switcher. Paths, identifiers,
# and the desktop profile keep DESKTOP_PROFILE_NAME so a rename never moves
# state or invalidates macOS privacy grants.
DESKTOP_DISPLAY_NAME = (
    os.environ.get("CODEX_MUX_DISPLAY_NAME", "").strip() or DESKTOP_PROFILE_NAME
)
DESKTOP_BUNDLE_IDENTIFIER = "app.cdxmux.multi"
OPENAI_DESKTOP_CODE_IDENTIFIER = "com.openai.codex"
OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER = "com.openai.sky.CUAService"
COMPUTER_USE_BUNDLE_IDENTIFIER = "com.cdxmux.sky.CUAService"
COMPUTER_USE_DISPLAY_NAME = "Codex Subscription Router Computer Use"
COMPUTER_USE_APP_NAME = f"{COMPUTER_USE_DISPLAY_NAME}.app"
LAUNCH_SERVICES_REGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
# Marks the __asar_integrity section Electron 154 compiles into its framework:
# an enabled flag, a format version, and a digest of Info.plist's integrity entry.
ASAR_INTEGRITY_SENTINEL = b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"
ASAR_UNPACK_DIRECTORIES = (
    "node_modules/{@worklouder,better-sqlite3,node-mac-permissions,node-pty,objc-js}"
)
PREFERRED_SIGNING_IDENTITY_PREFIXES = (
    "Developer ID Application:",
    "Apple Development:",
)
OPENAI_INTERNAL_TEAM_IDENTIFIER = "HX7739G8FX"
OPENAI_DISTRIBUTION_TEAM_IDENTIFIER = "2DC432GLL2"


@dataclass(frozen=True)
class SourceBuild:
    """What one official build must contain before it is patched."""

    asar_sha256: str
    cua_identifier_replacements: int = 49
    asar_cua_identifier_replacements: int = 16
    cua_service_layout: tuple[tuple[str, int], ...] = (("Codex Computer Use.app", 17),)


# The newest three official builds, keyed by (version, build). Adding a build
# removes the oldest one here and its RENDERER_BUILD profile.
SUPPORTED_BUILDS = {
    ("26.928.20755", "12246"): SourceBuild(
        "2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee"
    ),
    ("26.928.21956", "12404"): SourceBuild(
        "3bda98f2265ad23677dfe0163d1cc7855beade6bef11d27f830f6663d7658406"
    ),
    ("26.928.31416", "12553"): SourceBuild(
        "9d4dda5c04d42e32cbd378557359c8c06fa798805a3c991f8a7b4b2295a8b732"
    ),
}
# Counts assumed for a build passed with --allow-untested-source.
UNTESTED_BUILD = SourceBuild(asar_sha256="")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {PROJECT_VERSION}")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing destination after moving it to a timestamped backup.",
    )
    parser.add_argument(
        "--stage",
        type=Path,
        help="Build for --destination but leave the app pair in this directory "
        "instead of replacing the installed one; the app may keep running.",
    )
    parser.add_argument(
        "--install-staged",
        type=Path,
        metavar="STAGE",
        help="Install the pair a --stage run left in STAGE; the app must be quit.",
    )
    parser.add_argument(
        "--allow-adhoc-signing",
        action="store_true",
        help="Allow an ad-hoc signature (Appshots and Computer Use may stop working).",
    )
    parser.add_argument(
        "--allow-untested-source",
        action="store_true",
        help="Continue after an explicit version, build, or ASAR hash mismatch.",
    )
    parser.add_argument(
        "--allow-signing-team-change",
        action="store_true",
        help="Replace an existing build signed by a different Apple team.",
    )
    return parser.parse_args()


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required tool not found: {name}")


def resolve_signing_identity(allow_adhoc: bool) -> str:
    configured = os.environ.get("CODEX_MUX_SIGNING_IDENTITY", "").strip()
    if configured:
        return configured
    identities = output(["security", "find-identity", "-v", "-p", "codesigning"])
    available = re.findall(
        r'^\s*\d+\)\s+[0-9A-F]+\s+"([^"]+)"',
        identities,
        re.MULTILINE,
    )
    for prefix in PREFERRED_SIGNING_IDENTITY_PREFIXES:
        for identity in available:
            if identity.startswith(prefix):
                return identity
    if allow_adhoc:
        print(
            "Warning: using an ad-hoc signature; Appshots and Computer Use may be unavailable.",
            file=sys.stderr,
        )
        return "-"
    raise RuntimeError(
        "no team-backed code-signing identity found; set CODEX_MUX_SIGNING_IDENTITY "
        "or explicitly pass --allow-adhoc-signing"
    )


def signing_team_identifier(identity: str) -> str | None:
    if identity == "-":
        return None
    match = re.search(r"\(([A-Z0-9]{10})\)$", identity)
    if match is None:
        raise RuntimeError(
            "the signing identity must end with its 10-character Apple team ID"
        )
    return match.group(1)


def signed_code_metadata(path: Path) -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["codesign", "--display", "--verbose=4", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    details = result.stdout + result.stderr
    identifier_match = re.search(r"^Identifier=(.+)$", details, re.MULTILINE)
    team_match = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    identifier = identifier_match.group(1).strip() if identifier_match else None
    team = team_match.group(1).strip() if team_match else None
    if team == "not set":
        team = None
    return identifier, team


def verify_signed_code(
    path: Path,
    expected_identifier: str,
    expected_team: str | None,
) -> None:
    run(["codesign", "--verify", "--deep", "--strict", str(path)])
    identifier, team = signed_code_metadata(path)
    if identifier != expected_identifier:
        raise RuntimeError(
            f"unexpected signing identifier on {path}: {identifier!r}"
        )
    if team != expected_team:
        raise RuntimeError(f"unexpected signing team on {path}: {team!r}")


def existing_signing_team(path: Path) -> str | None:
    if not path.exists():
        return None
    plist_path = path / "Contents" / "Info.plist"
    if plist_path.is_file():
        try:
            with plist_path.open("rb") as handle:
                recorded = plistlib.load(handle).get("CodexMuxSigningTeamIdentifier")
            if isinstance(recorded, str) and recorded != "":
                return None if recorded == "adhoc" else recorded
        except (OSError, plistlib.InvalidFileException):
            pass
    _, team = signed_code_metadata(path)
    return team


def pgrep_literal(text: str) -> str:
    """Escape a path for pgrep's extended regular expressions."""
    return re.sub(r"([][.^$*+?(){}|\\])", r"\\\1", text)


# Helpers that outlive the app: Chromium's crash reporters and the desktop's
# modifier-key monitor. They hold no state and are ended when the bundle is replaced.
LINGERING_HELPERS = ("crashpad_handler", "bare-modifier-monitor")


def bundle_processes(path: Path) -> list[tuple[int, str]]:
    result = subprocess.run(
        ["pgrep", "-fl", pgrep_literal(str(path))],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    processes = []
    for line in result.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit():
            processes.append((int(pid), command))
    return processes


def running_components(path: Path) -> list[str]:
    return [
        command for _, command in bundle_processes(path)
        if not any(helper in command for helper in LINGERING_HELPERS)
    ]


def stop_lingering_helpers(path: Path) -> None:
    for pid, command in bundle_processes(path):
        if any(helper in command for helper in LINGERING_HELPERS):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def ensure_components_are_stopped(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if path.exists() and running_components(path):
            raise RuntimeError(
                f"quit the running component before replacing it: {path}"
            )


MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce",  # 32-bit, big endian
    b"\xfe\xed\xfa\xcf",  # 64-bit, big endian
    b"\xce\xfa\xed\xfe",  # 32-bit, little endian
    b"\xcf\xfa\xed\xfe",  # 64-bit, little endian
    b"\xca\xfe\xba\xbe",  # universal binary
    b"\xbe\xba\xfe\xca",  # universal binary, little endian
}


def is_mach_o(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACH_O_MAGICS
    except OSError:
        return False


def arm64_swift_small_string(value: str) -> bytes:
    """Encode the instructions used to materialize a 10-byte Swift string."""
    encoded = value.encode("ascii")
    if len(encoded) != 10:
        raise ValueError("a signing team identifier must contain 10 ASCII bytes")

    def instruction(base: int, immediate: int, register: int, shift: int = 0) -> bytes:
        word = base | ((shift // 16) << 21) | (immediate << 5) | register
        return word.to_bytes(4, "little")

    chunks = [
        int.from_bytes(encoded[index : index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]
    return b"".join(
        (
            instruction(0xD2800000, chunks[0], 0),
            instruction(0xF2800000, chunks[1], 0, 16),
            instruction(0xF2800000, chunks[2], 0, 32),
            instruction(0xF2800000, chunks[3], 0, 48),
            instruction(0xD2800000, chunks[4], 1),
            instruction(0xF2800000, 0xEA00, 1, 48),
        )
    )


def replace_same_length_identifier(
    path: Path, original: str, replacement: str
) -> int:
    """Replace an embedded identifier without changing binary or bundle offsets."""
    original_bytes = original.encode("ascii")
    replacement_bytes = replacement.encode("ascii")
    if len(original_bytes) != len(replacement_bytes):
        raise RuntimeError("replacement identifiers must have the same byte length")
    data = path.read_bytes()
    count = data.count(original_bytes)
    if count:
        path.write_bytes(data.replace(original_bytes, replacement_bytes))
    return count


def computer_use_package(app: Path) -> Path:
    return (
        app
        / "Contents"
        / "Resources"
        / "cua_node"
        / "lib"
        / "node_modules"
        / "@oai"
        / "sky"
    )


def retire_stale_cached_computer_use_app() -> None:
    """Remove only a prior custom helper copied into the shared Codex home;
    every install ships its own."""
    cached_app = (
        Path.home() / ".codex" / "computer-use" / "Codex Computer Use.app"
    )
    plist_path = cached_app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return
    try:
        with plist_path.open("rb") as handle:
            bundle_identifier = plistlib.load(handle).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return
    if bundle_identifier != COMPUTER_USE_BUNDLE_IDENTIFIER:
        return
    if LAUNCH_SERVICES_REGISTER.is_file():
        run([str(LAUNCH_SERVICES_REGISTER), "-u", str(cached_app)])
    shutil.rmtree(cached_app)
    print(f"Stale cached Computer Use helper removed from {cached_app}")


def patch_computer_use_identity(
    app: Path,
    team_identifier: str | None,
    expected_replacements: int = UNTESTED_BUILD.cua_identifier_replacements,
    service_layout: tuple[tuple[str, int], ...] = UNTESTED_BUILD.cua_service_layout,
) -> None:
    """Give the copied CUA service an independent identity and trusted callers."""
    package = computer_use_package(app)
    for profile in package.rglob("embedded.provisionprofile"):
        profile.unlink()

    identifier_replacements = 0
    for candidate in package.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            identifier_replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if identifier_replacements != expected_replacements:
        raise RuntimeError(
            "expected "
            f"{expected_replacements} Computer Use identity "
            f"references, found {identifier_replacements}"
        )

    expected_service_paths = {relative for relative, _ in service_layout}
    actual_service_paths = {
        str(candidate.relative_to(package))
        for candidate in package.rglob("Codex Computer Use.app")
        if candidate.is_dir()
    }
    if actual_service_paths != expected_service_paths:
        raise RuntimeError(
            "unexpected Computer Use service layout: "
            f"expected {sorted(expected_service_paths)}, "
            f"found {sorted(actual_service_paths)}"
        )

    for relative, expected_distribution_matches in service_layout:
        service = package / relative
        executable = service / "Contents" / "MacOS" / "SkyComputerUseService"
        if not executable.is_file():
            raise RuntimeError(f"bundled Computer Use service was not found: {relative}")

        plist_path = service / "Contents" / "Info.plist"
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
        info["CFBundleIdentifier"] = COMPUTER_USE_BUNDLE_IDENTIFIER
        info["CFBundleDisplayName"] = COMPUTER_USE_DISPLAY_NAME
        info["CFBundleName"] = COMPUTER_USE_DISPLAY_NAME
        for key in list(info):
            if key.startswith("SU"):
                del info[key]
        with plist_path.open("wb") as handle:
            plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

        if team_identifier is None:
            continue
        binary = executable.read_bytes()
        replacement = arm64_swift_small_string(team_identifier)
        for original_team, description, expected_raw_matches in (
            (OPENAI_INTERNAL_TEAM_IDENTIFIER, "internal", 1),
            (
                OPENAI_DISTRIBUTION_TEAM_IDENTIFIER,
                "distribution",
                expected_distribution_matches,
            ),
        ):
            original = arm64_swift_small_string(original_team)
            match_count = binary.count(original)
            if match_count != 2:
                raise RuntimeError(
                    f"expected two Computer Use {description}-team checks in "
                    f"{relative}, found {match_count}; the official app layout may "
                    "have changed"
                )
            binary = binary.replace(original, replacement)

            raw_original = original_team.encode("ascii")
            raw_replacement = team_identifier.encode("ascii")
            raw_match_count = binary.count(raw_original)
            if raw_match_count != expected_raw_matches:
                raise RuntimeError(
                    f"expected {expected_raw_matches} Computer Use {description}-team "
                    f"constants in {relative}, found {raw_match_count}; the official "
                    "app layout may have changed"
                )
            binary = binary.replace(raw_original, raw_replacement)

        original_bundle_id = b"com.openai.codex\0"
        replacement_bundle_id = DESKTOP_BUNDLE_IDENTIFIER.encode("ascii") + b"\0"
        if len(replacement_bundle_id) != len(original_bundle_id):
            raise RuntimeError(
                "the independent bundle identifier must match the CUA identifier length"
            )
        if binary.count(original_bundle_id) != 1:
            raise RuntimeError(
                f"could not find the Computer Use production bundle ID in {relative}"
            )
        executable.write_bytes(binary.replace(original_bundle_id, replacement_bundle_id))


def patch_asar_computer_use_identity(
    extracted: Path,
    expected_replacements: int = UNTESTED_BUILD.asar_cua_identifier_replacements,
) -> None:
    """Keep desktop launch, temp-file, and service references on the new CUA ID."""
    replacements = 0
    for candidate in extracted.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if replacements != expected_replacements:
        raise RuntimeError(
            "expected "
            f"{expected_replacements} Computer Use references "
            f"in app.asar, found {replacements}"
        )


def sign_native_code_tree(root: Path, identity: str) -> None:
    """Sign native modules before ASAR records their final sizes."""
    if not root.is_dir():
        return
    for candidate in root.rglob("*"):
        if not is_mach_o(candidate):
            continue
        run(
            [
                "codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp=none",
                "--options",
                "runtime",
                str(candidate),
            ]
        )


TEAM_SCOPED_ENTITLEMENTS = (
    "com.apple.application-identifier",
    "com.apple.developer.aps-environment",
    "com.apple.developer.team-identifier",
    "com.apple.security.application-groups",
    "keychain-access-groups",
)


def sanitized_runtime_entitlements(executable: Path) -> dict[str, object] | None:
    """Keep runtime capabilities while removing the official app's team grants."""
    result = subprocess.run(
        ["codesign", "--display", "--entitlements", ":-", str(executable)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not result.stdout.strip():
        return None
    try:
        entitlements = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException as error:
        raise RuntimeError(
            f"could not read signing entitlements from {executable}"
        ) from error
    if not isinstance(entitlements, dict):
        raise RuntimeError(f"invalid signing entitlements on {executable}")
    for key in TEAM_SCOPED_ENTITLEMENTS:
        entitlements.pop(key, None)
    return entitlements or None


AUTO_ENTITLEMENTS = object()


def sign_runtime_executable(
    executable: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    """Re-sign an embedded runtime without breaking JIT-backed processes."""
    if entitlements is AUTO_ENTITLEMENTS:
        entitlements = sanitized_runtime_entitlements(executable)
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is None:
        command.append("--preserve-metadata=identifier")
    else:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(executable)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(executable)])


def bundle_main_executable(bundle: Path) -> Path | None:
    plist_path = bundle / "Contents" / "Info.plist"
    executable_root = bundle / "Contents" / "MacOS"
    if bundle.suffix == ".framework":
        plist_path = bundle / "Versions" / "Current" / "Resources" / "Info.plist"
        executable_root = bundle / "Versions" / "Current"
    if not plist_path.is_file():
        return None
    with plist_path.open("rb") as handle:
        executable_name = plistlib.load(handle).get("CFBundleExecutable")
    if not isinstance(executable_name, str) or executable_name == "":
        return None
    executable = executable_root / executable_name
    return executable if executable.is_file() else None


def sign_runtime_bundle(
    bundle: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    if entitlements is AUTO_ENTITLEMENTS:
        executable = bundle_main_executable(bundle)
        entitlements = (
            sanitized_runtime_entitlements(executable)
            if executable is not None
            else None
        )
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is not None:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(bundle)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(bundle)])


def capture_computer_use_entitlements(
    app: Path,
    service_layout: tuple[tuple[str, int], ...] = UNTESTED_BUILD.cua_service_layout,
) -> dict[Path, dict[str, object] | None]:
    package = computer_use_package(app)
    entitlements: dict[Path, dict[str, object] | None] = {}
    for relative, _ in service_layout:
        service = package / relative
        if not service.is_dir():
            raise RuntimeError(f"bundled Computer Use service was not found: {relative}")
        entitlements.update(
            {
                executable.relative_to(package): sanitized_runtime_entitlements(
                    executable
                )
                for executable in service.rglob("*")
                if is_mach_o(executable)
            }
        )
    return entitlements


def sign_computer_use_code(
    app: Path,
    identity: str,
    preserved_entitlements: dict[Path, dict[str, object] | None],
    service_layout: tuple[tuple[str, int], ...] = UNTESTED_BUILD.cua_service_layout,
) -> None:
    """Keep the Computer Use service and its callers on one signing team."""
    resources = app / "Contents" / "Resources"
    package = computer_use_package(app)
    for relative, _ in service_layout:
        service = package / relative
        if not service.is_dir():
            raise RuntimeError(f"bundled Computer Use service was not found: {relative}")

        for executable in sorted(
            (candidate for candidate in service.rglob("*") if is_mach_o(candidate)),
            key=lambda candidate: len(candidate.parts),
            reverse=True,
        ):
            executable_relative = executable.relative_to(package)
            sign_runtime_executable(
                executable,
                identity,
                entitlements=preserved_entitlements.get(executable_relative),
            )

        bundle_suffixes = {".app", ".appex", ".bundle", ".framework", ".xpc"}
        bundles = [
            candidate
            for candidate in service.rglob("*")
            if candidate.is_dir() and candidate.suffix in bundle_suffixes
        ]
        bundles.append(service)
        for bundle in sorted(
            set(bundles),
            key=lambda candidate: len(candidate.parts),
            reverse=True,
        ):
            identifier = (
                COMPUTER_USE_BUNDLE_IDENTIFIER if bundle == service else None
            )
            executable = bundle_main_executable(bundle)
            entitlements = (
                preserved_entitlements.get(executable.relative_to(package))
                if executable is not None
                else None
            )
            sign_runtime_bundle(bundle, identity, identifier, entitlements)
            run(["codesign", "--verify", "--deep", "--strict", str(bundle)])

    for executable_name in ("node", "node_repl"):
        executable = resources / "cua_node" / "bin" / executable_name
        sign_runtime_executable(executable, identity)
    sign_runtime_executable(
        app / "Contents" / "MacOS" / "ChatGPT",
        identity,
        OPENAI_DESKTOP_CODE_IDENTIFIER,
        runtime=False,
    )


def codex_cli_app(resources: Path) -> Path:
    """The packaged Codex CLI app whose `codex` executable the desktop launches."""
    return resources / "codex-cli" / "CodexCLI.app"


def codex_entrypoint(resources: Path) -> Path:
    return codex_cli_app(resources) / "Contents" / "MacOS" / "codex"


def sign_independent_app(
    app: Path,
    identity: str,
    team_identifier: str | None,
    expected_cua_replacements: int = UNTESTED_BUILD.cua_identifier_replacements,
    service_layout: tuple[tuple[str, int], ...] = UNTESTED_BUILD.cua_service_layout,
) -> None:
    """Apply one stable identity throughout the modified Electron bundle."""
    computer_use_entitlements = capture_computer_use_entitlements(app, service_layout)
    patch_computer_use_identity(
        app,
        team_identifier,
        expected_cua_replacements,
        service_layout,
    )
    sign_computer_use_code(app, identity, computer_use_entitlements, service_layout)
    resources = app / "Contents" / "Resources"
    # A re-sealed CLI app no longer matches its profile, so the official binary
    # keeps only the runtime entitlements it needs to run.
    (codex_cli_app(resources) / "Contents" / "embedded.provisionprofile").unlink(missing_ok=True)
    sign_runtime_executable(codex_entrypoint(resources).with_name("codex.real"), identity)
    run(["codesign", "--force", "--sign", identity, "--timestamp=none", str(codex_cli_app(resources))])
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(app),
        ]
    )


def load_or_create_token() -> str:
    DEFAULT_STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    DEFAULT_STATE_ROOT.chmod(0o700)
    token_path = DEFAULT_STATE_ROOT / "control-token"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise RuntimeError(f"invalid control token at {token_path}")
        token_path.chmod(0o600)
        return token
    token = secrets.token_hex(32)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def build_proxy(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "go",
            "build",
            "-trimpath",
            "-ldflags=-s -w",
            "-o",
            str(destination),
            "./cmd/codex-mux",
        ],
        cwd=PROJECT_ROOT,
    )
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_launcher(app: Path) -> None:
    """Pass Chromium its isolated profile before Electron's main process starts."""
    launcher = app / "Contents" / "MacOS" / "CodexSubscriptionRouterLauncher"
    run(
        [
            "xcrun",
            "clang",
            "-Os",
            "-Wall",
            "-Wextra",
            "-o",
            str(launcher),
            str(PROJECT_ROOT / "native" / "launcher.c"),
        ]
    )


def ensure_asar_tool() -> Path:
    asar = PROJECT_ROOT / "node_modules" / ".bin" / "asar"
    package_manifest = PROJECT_ROOT / "node_modules" / "@electron" / "asar" / "package.json"
    expected = json.loads(
        (PROJECT_ROOT / "package.json").read_text(encoding="utf-8")
    )["devDependencies"]["@electron/asar"]
    if not asar.exists() or not package_manifest.is_file():
        raise RuntimeError("run `npm ci --ignore-scripts` before patching")
    actual = json.loads(package_manifest.read_text(encoding="utf-8")).get("version")
    if actual != expected:
        raise RuntimeError(
            f"installed @electron/asar is {actual!r}, expected {expected!r}; "
            "run `npm ci --ignore-scripts`"
        )
    return asar


def replace_javascript_identifiers(source: str, replacements: dict[str, str]) -> str:
    """Retarget injected source to the minified imports in a supported build."""
    for original, replacement in replacements.items():
        pattern = rf"(?<![A-Za-z0-9_$]){re.escape(original)}(?![A-Za-z0-9_$])"
        source, count = re.subn(pattern, replacement, source)
        if count == 0:
            raise RuntimeError(
                f"could not retarget injected JavaScript identifier {original!r}"
            )
    return source


@dataclass(frozen=True)
class RendererBuild:
    """Anchors and minified identifiers of one official renderer layout.

    Every anchor must match exactly once so a build that moves any of them
    fails closed instead of producing a partially patched renderer. Pairs are
    (anchor, replacement).
    """

    marker: str
    data_anchor: str
    menu_identifiers: dict[str, str]
    menu_anchor: str
    usage_slot: tuple[str, str]
    plugin_request: tuple[str, str]
    plugin_request_checks: tuple[str, ...]
    reset_query: tuple[str, str]
    reset_mutation: tuple[str, str]
    usage_modal: str
    usage_windows: str
    usage_header: tuple[str, str]
    profile_avatar: tuple[str, str]
    profile_name: tuple[str, str]
    profile_identity: tuple[str, str]
    plugin_scope: tuple[str, str]
    thread_identifiers: dict[str, str]
    thread_anchor: str
    thread_sections: tuple[str, str]
    composer_actions: tuple[str, ...]
    fork_titles: tuple[str, str]
    fork_identifiers: dict[str, str]
    # Snippets that exist once in the build and name identifiers the
    # injected sources borrow; the porting tool reads them, the patcher
    # verifies them.
    identifier_probes: tuple[str, ...]
    usage_status: tuple[str, str]


RENDERER_BUILD_12246 = RendererBuild(
    marker="function qP(e,t){let n=e.get(JP);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    data_anchor="function qP(e,t){let n=e.get(JP);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    menu_identifiers={
        "e7": "$()",
        "kXc": "Yh()",
        "Lo": "Pe",
        "Q": "Z",
        "BW": "qHt",
        "QLs": "hDi",
        "_H": "HJe",
        "CH": "lf",
        "jLa": "bza",
        "lt": "Qa",
        "Rv": "sh",
        "RD": "jd()",
    },
    menu_anchor="function bza(e,t){return xza(e,t).src}",
    usage_slot=(
        "(O=(0,$.jsx)(Pi,{accountIcon:s,accountSwitcher:Fn,additionalItems:g,displayName:y,hasWorkspaceAccount:l,identityItems:b,isPetVisible:d,onCloseMenu:o,onCopyUserId:x,onLogOut:S,onOpenChatGptAnalytics:C,onOpenPersonalization:w,onOpenProfile:T,onOpenSettings:vn,onOpenWorkspaceSettings:E,personalPlanLabel:p,onTogglePet:D,petShortcut:Ze,settingsShortcut:Xe,usageItems:Jn})",
        "(O=(0,$.jsx)(Pi,{accountIcon:s,accountSwitcher:Fn,additionalItems:g,displayName:y,hasWorkspaceAccount:l,identityItems:b,isPetVisible:d,onCloseMenu:o,onCopyUserId:x,onLogOut:S,onOpenChatGptAnalytics:C,onOpenPersonalization:w,onOpenProfile:T,onOpenSettings:vn,onOpenWorkspaceSettings:E,personalPlanLabel:p,onTogglePet:D,petShortcut:Ze,settingsShortcut:Xe,usageItems:(0,$.jsx)(globalThis.CodexMuxAccountMenu,{})})",
    ),
    plugin_request=(
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?",
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);t=codexMuxScopePluginRequest(e,t);return e===`config/read`?",
    ),
    plugin_request_checks=(
        "listMcpServers(e,t){return Gen(this,this.mcpServerStatusPromises,e,t,",
        "l=e.sendRequest(`mcpServerStatus/list`,n,a)",
    ),
    reset_query=(
        "function ovr(){let e=(0,RR.c)(1);kh(),W(null);let t;return e[0]===Symbol.for(`react.memo_cache_sentinel`)?(t={queryKey:[`rate-limit-reset-credits`],queryFn:cvr,select:svr,refetchInterval:Yd.ONE_MINUTE,staleTime:Yd.FIVE_SECONDS},e[0]=t):t=e[0],jf(t)}",
        "function ovr(){kh(),W(null);let e=window.__codexMuxResetAccountId;return jf({queryKey:[`rate-limit-reset-credits`,e??`primary`],queryFn:e?()=>codexMuxRateLimitResets(e):cvr,select:svr,refetchInterval:Yd.ONE_MINUTE,staleTime:Yd.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function lvr(){let e=(0,RR.c)(3),t=Qa(),n=Pm(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:uvr,onSuccess:(e,r)=>{let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){let n=e.code===`reset`?e.credit?.id??i:i;t.setQueryData([`rate-limit-reset-credits`],e=>N_r(e,a,n))}Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},e[0]=n,e[1]=t,e[2]=r):r=e[2],Hh(r)}",
        "function lvr(){let e=Qa(),t=Pm(),n=window.__codexMuxResetAccountId,r=[`rate-limit-reset-credits`,n??`primary`];return Hh({mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):uvr,onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?n.credit?.id??a:a;e.setQueryData(r,e=>N_r(e,o,t))}Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="function Et(e){let t=(0,Dt.c)(19),{defaultResetCreditsOpen:r,",
    usage_windows="let x=b;if(v!=null){",
    usage_header=(
        "(je=(0,$.jsx)(se,{children:(0,$.jsx)(O,{title:(0,$.jsx)(R,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(w,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})})}),t[41]=je)",
        "(je=(0,$.jsxs)(se,{children:[(0,$.jsx)(O,{title:(0,$.jsx)(R,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(w,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})}),window.__codexMuxResetAccountSelector??null]}),t[41]=je)",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`div`,{\"aria-disabled\":_n,onPointerEnter:e=>Vt(e.pointerType!==`touch`),onPointerLeave:()=>Vt(!1),onPointerCancel:()=>Vt(!1),className:rn(`group relative flex rounded-full outline-none`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:[globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>yt.refetch()})??null,(0,$.jsxs)(`div`,{\"aria-disabled\":_n,onPointerEnter:e=>Vt(e.pointerType!==`touch`),onPointerLeave:()=>Vt(!1),onPointerCancel:()=>Vt(!1),className:rn(globalThis.CodexMuxProfileAvatarStack?`hidden`:`group relative flex rounded-full outline-none`,",
    ),
    profile_name=(
        "sr=Hn??(0,$.jsx)(J,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "sr=globalThis.__codexMuxSelectedProfileAccountId?(Hn??(0,$.jsx)(J,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "zn=In?Rn:null,Bn=i?ae?.display_name?.trim()||null:bt?.displayName??null,",
        "zn=globalThis.__codexMuxSelectedProfileAccountId&&In?Rn:null,Bn=i?ae?.display_name?.trim()||null:bt?.displayName??null,",
    ),
    plugin_scope=(
        "(C=(0,ao.jsx)(Sn,{title:h,subtitle:g,action:S,children:m})",
        "(C=(0,ao.jsx)(Sn,{title:h,subtitle:g,action:S,children:[globalThis.CodexMuxPluginScope?.()??null,m]})",
    ),
    thread_identifiers={
        "K": "Q",
    },
    thread_anchor="function hE(e){let t=(0,gE.c)(4),{onOpenPullRequestSidePanel:n,onForceShow:r,registerEnvironmentActionCommands:i}=e,a=p(_o),",
    thread_sections=(
        "(k=(0,vE.jsxs)(vE.Fragment,{children:[b,x,S,C,w,T,E,D,O]})",
        "(k=(0,vE.jsxs)(vE.Fragment,{children:[b,x,S,C,w,T,(0,vE.jsx)(CodexMuxThreadSubscription,{}),E,D,O]})",
    ),
    composer_actions=(
        "(0,ZW.jsxs)(qS.FooterActions,{ref:ft,spacing:an,children:[on,nn,sn]})",
        "(0,ZW.jsxs)(qS.FooterActions,{spacing:`none`,children:[nn,(0,ZW.jsx)(`div`,{className:`ms-2 flex items-center`,children:Nt})]})",
    ),
    fork_titles=(
        "function W_r(e,t){t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
        "function W_r(e,t){codexMuxForkTitles(e,t);t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
    ),
    fork_identifiers={
        "CODEX_MUX_SERVICES": "i6",
        "codexMuxConversationTurns": "HJn",
        "codexMuxTurnWithId": "XQ",
        "codexMuxRememberDescription": "yGr",
    },
    identifier_probes=(
        "function bza(e,t){return xza(e,t).src}",
        "function qHt(e,t,n,r){e.set(DL,e=>{let i=e.modals.find(e=>EL(e.ModalComponent,t)),",
        "function hDi(e){let t=(0,_Di.c)(7),n;t[0]===e.onClose?n=t[1]:(n=(0,dG.jsx)(gDi,{onClose:e.onClose}),t[0]=e.onClose,t[1]=n);let r;t[2]===e?r=t[3]:(r=(0,dG.jsx)(yDi,{...e}),t[2]=e,t[3]=r);let i;return t[4]!==n||t[5]!==r?(i=(0,dG.jsx)(vDi.Suspense,{fallback:n,children:r}),t[4]=n,t[5]=r,t[6]=i):i=t[6],i}function gDi(e){let t=(0,_Di.c)(8),{onClose:n,failed:r}=e,i=r!==void 0&&r,a;t[0]===n?a=t[1]:(a=e=>{e||n()},t[0]=n,t[1]=a);let o;t[2]===i?o=t[3]:(o=i?(0,dG.jsx)(J,{id:`codex.rateLimitResetModal.loadError.title`,",
        "c=Pe(Z),l=SSi(),u=xd(),d=wu(),f=JW(),p=tm(),",
        "r=Pe(sh),[i,a]=(0,vZt.useState)(!1),o;if(t[0]!==r||t[1]!==n.tabId){",
        "t=Qa(),n=Pm(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:uvr,",
        "i6=await r6.services,i6.threadReadState!=null",
        "function HJn(e){return e==null?null:YQ(e)}function XQ(e,t){return HJn(e)?.find(e=>e.turnId===t)??null}",
        "function yGr(e,t,n){let r={...tL(bGr,{}),[t]:n};",
        "let yt=ki(vt),bt=i?lt:yt.data,",
        "(r=(0,vE.jsx)(Q.Section,{sectionKey:`usage`,",
        "Fjt=$(),Ijt=Dp(Tjt)})))()}var Rjt,zjt,Bjt,Vjt,",
        "sut=Yh(),cut=(0,sut.createContext)(Irt)})))()}var uut,dut,fut,put,",
        "R$t=jd(),yT(),pQt(),z$t=(0,ST.createContext)(null)",
        "XAt=Kp(Z,()=>Gr().homeModePreferences??Ake({",
        "let e=Kp(sh,[]),t=u(sh,e=>null);return{entries$:ld(sh,({",
        "o1.jsx)(HJe,{onSelect:()=>u?.(e),",
        "(s=(0,Y$.jsx)(lf.Item,{leftIconAsset:rIe,onClick:r,children:o})",
    ),
    usage_status=(
        "async function rJr({additionalHeaders:e,signal:t}){try{return zqr(await OU.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":dmn.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t}))}",
        "async function rJr({additionalHeaders:e,signal:t}){try{return zqr(await codexMuxFilterUsageStatus(await OU.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":dmn.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t})))}",
    ),
)


RENDERER_BUILD_12404 = RendererBuild(
    marker="function ZHa(e,t){return QHa(e,t).src}",
    data_anchor="function KP(e,t){let n=e.get(qP);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    menu_identifiers={
        "e7": "$()",
        "kXc": "Vh()",
        "Lo": "Fe",
        "Q": "Z",
        "BW": "qHt",
        "QLs": "QAi",
        "_H": "B",
        "CH": "bl",
        "jLa": "ZHa",
        "lt": "to",
        "Rv": "th",
        "RD": "Od()",
    },
    menu_anchor="function ZHa(e,t){return QHa(e,t).src}",
    usage_slot=(
        "(O=(0,$.jsx)(Pi,{accountIcon:o,accountSwitcher:Ln,additionalItems:g,displayName:_,hasWorkspaceAccount:c,identityItems:v,isPetVisible:d,onCloseMenu:s,onCopyUserId:x,onLogOut:S,onOpenChatGptAnalytics:C,onOpenPersonalization:w,onOpenProfile:T,onOpenSettings:_n,onOpenWorkspaceSettings:E,personalPlanLabel:f,onTogglePet:D,petShortcut:et,settingsShortcut:$e,usageItems:qn})",
        "(O=(0,$.jsx)(Pi,{accountIcon:o,accountSwitcher:Ln,additionalItems:g,displayName:_,hasWorkspaceAccount:c,identityItems:v,isPetVisible:d,onCloseMenu:s,onCopyUserId:x,onLogOut:S,onOpenChatGptAnalytics:C,onOpenPersonalization:w,onOpenProfile:T,onOpenSettings:_n,onOpenWorkspaceSettings:E,personalPlanLabel:f,onTogglePet:D,petShortcut:et,settingsShortcut:$e,usageItems:(0,$.jsx)(globalThis.CodexMuxAccountMenu,{})})",
    ),
    plugin_request=(
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?",
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);t=codexMuxScopePluginRequest(e,t);return e===`config/read`?",
    ),
    plugin_request_checks=(
        "listMcpServers(e,t){return Gen(this,this.mcpServerStatusPromises,e,t,",
        "l=e.sendRequest(`mcpServerStatus/list`,n,a)",
    ),
    reset_query=(
        "function ber(){let e=(0,xI.c)(1);Th(),W(null);let t;return e[0]===Symbol.for(`react.memo_cache_sentinel`)?(t={queryKey:[`rate-limit-reset-credits`],queryFn:Ser,select:xer,refetchInterval:Wd.ONE_MINUTE,staleTime:Wd.FIVE_SECONDS},e[0]=t):t=e[0],wf(t)}",
        "function ber(){Th(),W(null);let e=window.__codexMuxResetAccountId;return wf({queryKey:[`rate-limit-reset-credits`,e??`primary`],queryFn:e?()=>codexMuxRateLimitResets(e):Ser,select:xer,refetchInterval:Wd.ONE_MINUTE,staleTime:Wd.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function Cer(){let e=(0,xI.c)(3),t=to(),n=jm(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:wer,onSuccess:(e,r)=>{let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){let n=e.code===`reset`?e.credit?.id??i:i;t.setQueryData([`rate-limit-reset-credits`],e=>q9n(e,a,n))}Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},e[0]=n,e[1]=t,e[2]=r):r=e[2],Ih(r)}",
        "function Cer(){let e=to(),t=jm(),n=window.__codexMuxResetAccountId,r=[`rate-limit-reset-credits`,n??`primary`];return Ih({mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):wer,onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?n.credit?.id??a:a;e.setQueryData(r,e=>q9n(e,o,t))}Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="function Et(e){let t=(0,Dt.c)(19),{defaultResetCreditsOpen:n,",
    usage_windows="let x=b;if(v!=null){",
    usage_header=(
        "(Me=(0,$.jsx)(S,{children:(0,$.jsx)(v,{title:(0,$.jsx)(p,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(k,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})})}),t[41]=Me)",
        "(Me=(0,$.jsxs)(S,{children:[(0,$.jsx)(v,{title:(0,$.jsx)(p,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(k,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})}),window.__codexMuxResetAccountSelector??null]}),t[41]=Me)",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`div`,{\"aria-disabled\":hn,onPointerEnter:e=>Vt(e.pointerType!==`touch`),onPointerLeave:()=>Vt(!1),onPointerCancel:()=>Vt(!1),className:on(`group relative flex rounded-full outline-none`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:[globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>bt.refetch()})??null,(0,$.jsxs)(`div`,{\"aria-disabled\":hn,onPointerEnter:e=>Vt(e.pointerType!==`touch`),onPointerLeave:()=>Vt(!1),onPointerCancel:()=>Vt(!1),className:on(globalThis.CodexMuxProfileAvatarStack?`hidden`:`group relative flex rounded-full outline-none`,",
    ),
    profile_name=(
        "lr=Vn??(0,$.jsx)(q,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "lr=globalThis.__codexMuxSelectedProfileAccountId?(Vn??(0,$.jsx)(q,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "Rn=Fn?Ln:null,zn=r?ae?.display_name?.trim()||null:St?.displayName??null,",
        "Rn=globalThis.__codexMuxSelectedProfileAccountId&&Fn?Ln:null,zn=r?ae?.display_name?.trim()||null:St?.displayName??null,",
    ),
    plugin_scope=(
        "(C=(0,ao.jsx)(Xn,{title:h,subtitle:g,action:S,children:m})",
        "(C=(0,ao.jsx)(Xn,{title:h,subtitle:g,action:S,children:[globalThis.CodexMuxPluginScope?.()??null,m]})",
    ),
    thread_identifiers={
        "K": "Q",
    },
    thread_anchor="function _E(e){let t=(0,vE.c)(4),{onOpenPullRequestSidePanel:n,onForceShow:r,registerEnvironmentActionCommands:i}=e,a=h(wo),",
    thread_sections=(
        "(k=(0,bE.jsxs)(bE.Fragment,{children:[b,x,S,C,w,T,E,D,O]})",
        "(k=(0,bE.jsxs)(bE.Fragment,{children:[b,x,S,C,w,T,(0,bE.jsx)(CodexMuxThreadSubscription,{}),E,D,O]})",
    ),
    composer_actions=(
        "(0,ZW.jsxs)(yb.FooterActions,{ref:ft,spacing:an,children:[on,nn,sn]})",
        "(0,ZW.jsxs)(yb.FooterActions,{spacing:`none`,children:[nn,(0,ZW.jsx)(`div`,{className:`ms-2 flex items-center`,children:Nt})]})",
    ),
    fork_titles=(
        "function W_r(e,t){t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
        "function W_r(e,t){codexMuxForkTitles(e,t);t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
    ),
    fork_identifiers={
        "CODEX_MUX_SERVICES": "i6",
        "codexMuxConversationTurns": "HJn",
        "codexMuxTurnWithId": "XQ",
        "codexMuxRememberDescription": "yGr",
    },
    identifier_probes=(
        "function ZHa(e,t){return QHa(e,t).src}",
        "function qHt(e,t,n,r){e.set(DL,e=>{let i=e.modals.find(e=>EL(e.ModalComponent,t)),",
        "function QAi(e){let t=(0,eji.c)(7),n;t[0]===e.onClose?n=t[1]:(n=(0,IW.jsx)($Ai,{onClose:e.onClose}),t[0]=e.onClose,t[1]=n);let r;t[2]===e?r=t[3]:(r=(0,IW.jsx)(nji,{...e}),t[2]=e,t[3]=r);let i;return t[4]!==n||t[5]!==r?(i=(0,IW.jsx)(tji.Suspense,{fallback:n,children:r}),t[4]=n,t[5]=r,t[6]=i):i=t[6],i}function $Ai(e){let t=(0,eji.c)(8),{onClose:n,failed:r}=e,i=r!==void 0&&r,a;t[0]===n?a=t[1]:(a=e=>{e||n()},t[0]=n,t[1]=a);let o;t[2]===i?o=t[3]:(o=i?(0,IW.jsx)(J,{id:`codex.rateLimitResetModal.loadError.title`,",
        "c=Fe(Z),l=aEi(),u=vd(),d=Eu(),f=yW(),p=Yp(),",
        "r=Fe(th),[i,a]=(0,yzr.useState)(!1),o;if(t[0]!==r||t[1]!==n.tabId){",
        "t=to(),n=jm(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:wer,",
        "i6=await r6.services,i6.threadReadState!=null",
        "function HJn(e){return e==null?null:YQ(e)}function XQ(e,t){return HJn(e)?.find(e=>e.turnId===t)??null}",
        "function yGr(e,t,n){let r={...tL(bGr,{}),[t]:n};",
        "let bt=qr(yt),St=r?dt:bt.data,",
        "(r=(0,bE.jsx)(Q.Section,{sectionKey:`usage`,",
        "CCr=$(),wCr=vp(pCr)})))()}var ECr,DCr,OCr,kCr,",
        "Out=Vh(),kut=(0,Out.createContext)(sit)})))()}var jut,Mut,Nut,Put,Fut,Iut,Lut,Rut,zut,But,Vut,Hut,Uut,Gy,Wut,Gut,Kut,qut,Jut,Yut,Xut,Zut,Ky,Qut,$ut,edt,tdt,ndt;",
        "u1n=Od(),rF(),zQn(),d1n=(0,oF.createContext)(null)",
        "ujt=Bp(Z,()=>Gr().homeModePreferences??Oke({",
        "let e=Bp(th,[]),t=u(th,e=>null);return{entries$:sd(th,({",
        "QQ.jsx)(B,{onSelect:()=>u?.(e),",
        "(s=(0,VQ.jsx)(bl.Item,{leftIconAsset:hLe,onClick:r,children:o})",
    ),
    usage_status=(
        "async function rJr({additionalHeaders:e,signal:t}){try{return zqr(await OU.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":dmn.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t}))}",
        "async function rJr({additionalHeaders:e,signal:t}){try{return zqr(await codexMuxFilterUsageStatus(await OU.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":dmn.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t})))}",
    ),
)


RENDERER_BUILD_12553 = RendererBuild(
    marker="function SHa(e,t){return CHa(e,t).src}",
    data_anchor="function KP(e,t){let n=e.get(qP);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    menu_identifiers={
        "e7": "$()",
        "kXc": "Jh()",
        "Lo": "Fe",
        "Q": "Z",
        "BW": "qHt",
        "QLs": "qAi",
        "_H": "B",
        "CH": "kl",
        "jLa": "SHa",
        "lt": "ro",
        "Rv": "ch",
        "RD": "Vd()",
    },
    menu_anchor="function SHa(e,t){return CHa(e,t).src}",
    usage_slot=(
        "(O=(0,$.jsx)(Pi,{accountIcon:o,accountSwitcher:Fn,additionalItems:g,displayName:_,hasWorkspaceAccount:c,identityItems:v,isPetVisible:d,onCloseMenu:s,onCopyUserId:x,onLogOut:S,onOpenChatGptAnalytics:C,onOpenPersonalization:w,onOpenProfile:T,onOpenSettings:_n,onOpenWorkspaceSettings:E,personalPlanLabel:f,onTogglePet:D,petShortcut:et,settingsShortcut:$e,usageItems:Kn})",
        "(O=(0,$.jsx)(Pi,{accountIcon:o,accountSwitcher:Fn,additionalItems:g,displayName:_,hasWorkspaceAccount:c,identityItems:v,isPetVisible:d,onCloseMenu:s,onCopyUserId:x,onLogOut:S,onOpenChatGptAnalytics:C,onOpenPersonalization:w,onOpenProfile:T,onOpenSettings:_n,onOpenWorkspaceSettings:E,personalPlanLabel:f,onTogglePet:D,petShortcut:et,settingsShortcut:$e,usageItems:(0,$.jsx)(globalThis.CodexMuxAccountMenu,{})})",
    ),
    plugin_request=(
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?",
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);t=codexMuxScopePluginRequest(e,t);return e===`config/read`?",
    ),
    plugin_request_checks=(
        "listMcpServers(e,t){return Gen(this,this.mcpServerStatusPromises,e,t,",
        "l=e.sendRequest(`mcpServerStatus/list`,n,a)",
    ),
    reset_query=(
        "function _er(){let e=(0,TI.c)(1);Mh(),W(null);let t;return e[0]===Symbol.for(`react.memo_cache_sentinel`)?(t={queryKey:[`rate-limit-reset-credits`],queryFn:yer,select:ver,refetchInterval:rf.ONE_MINUTE,staleTime:rf.FIVE_SECONDS},e[0]=t):t=e[0],Nf(t)}",
        "function _er(){Mh(),W(null);let e=window.__codexMuxResetAccountId;return Nf({queryKey:[`rate-limit-reset-credits`,e??`primary`],queryFn:e?()=>codexMuxRateLimitResets(e):yer,select:ver,refetchInterval:rf.ONE_MINUTE,staleTime:rf.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function ber(){let e=(0,TI.c)(3),t=ro(),n=zm(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:xer,onSuccess:(e,r)=>{let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){let n=e.code===`reset`?e.credit?.id??i:i;t.setQueryData([`rate-limit-reset-credits`],e=>W9n(e,a,n))}Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},e[0]=n,e[1]=t,e[2]=r):r=e[2],Uh(r)}",
        "function ber(){let e=ro(),t=zm(),n=window.__codexMuxResetAccountId,r=[`rate-limit-reset-credits`,n??`primary`];return Uh({mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):xer,onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?n.credit?.id??a:a;e.setQueryData(r,e=>W9n(e,o,t))}Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="function Et(e){let t=(0,Dt.c)(19),{defaultResetCreditsOpen:n,",
    usage_windows="let x=b;if(v!=null){",
    usage_header=(
        "(Me=(0,$.jsx)(S,{children:(0,$.jsx)(v,{title:(0,$.jsx)(p,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(k,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})})}),t[41]=Me)",
        "(Me=(0,$.jsxs)(S,{children:[(0,$.jsx)(v,{title:(0,$.jsx)(p,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(k,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})}),window.__codexMuxResetAccountSelector??null]}),t[41]=Me)",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`div`,{\"aria-disabled\":hn,onPointerEnter:e=>Vt(e.pointerType!==`touch`),onPointerLeave:()=>Vt(!1),onPointerCancel:()=>Vt(!1),className:on(`group relative flex rounded-full outline-none`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:[globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>bt.refetch()})??null,(0,$.jsxs)(`div`,{\"aria-disabled\":hn,onPointerEnter:e=>Vt(e.pointerType!==`touch`),onPointerLeave:()=>Vt(!1),onPointerCancel:()=>Vt(!1),className:on(globalThis.CodexMuxProfileAvatarStack?`hidden`:`group relative flex rounded-full outline-none`,",
    ),
    profile_name=(
        "lr=Vn??(0,$.jsx)(q,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "lr=globalThis.__codexMuxSelectedProfileAccountId?(Vn??(0,$.jsx)(q,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "Rn=Fn?Ln:null,zn=r?ae?.display_name?.trim()||null:St?.displayName??null,",
        "Rn=globalThis.__codexMuxSelectedProfileAccountId&&Fn?Ln:null,zn=r?ae?.display_name?.trim()||null:St?.displayName??null,",
    ),
    plugin_scope=(
        "(C=(0,ao.jsx)(Yn,{title:h,subtitle:g,action:S,children:m})",
        "(C=(0,ao.jsx)(Yn,{title:h,subtitle:g,action:S,children:[globalThis.CodexMuxPluginScope?.()??null,m]})",
    ),
    thread_identifiers={
        "K": "Q",
    },
    thread_anchor="function _E(e){let t=(0,vE.c)(4),{onOpenPullRequestSidePanel:n,onForceShow:r,registerEnvironmentActionCommands:i}=e,a=h(wo),",
    thread_sections=(
        "(k=(0,bE.jsxs)(bE.Fragment,{children:[b,x,S,C,w,T,E,D,O]})",
        "(k=(0,bE.jsxs)(bE.Fragment,{children:[b,x,S,C,w,T,(0,bE.jsx)(CodexMuxThreadSubscription,{}),E,D,O]})",
    ),
    composer_actions=(
        "(0,ZW.jsxs)(Jb.FooterActions,{ref:ft,spacing:an,children:[on,nn,sn]})",
        "(0,ZW.jsxs)(Jb.FooterActions,{spacing:`none`,children:[nn,(0,ZW.jsx)(`div`,{className:`ms-2 flex items-center`,children:Nt})]})",
    ),
    fork_titles=(
        "function W_r(e,t){t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
        "function W_r(e,t){codexMuxForkTitles(e,t);t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
    ),
    fork_identifiers={
        "CODEX_MUX_SERVICES": "i6",
        "codexMuxConversationTurns": "HJn",
        "codexMuxTurnWithId": "XQ",
        "codexMuxRememberDescription": "yGr",
    },
    identifier_probes=(
        "function SHa(e,t){return CHa(e,t).src}",
        "function qHt(e,t,n,r){e.set(DL,e=>{let i=e.modals.find(e=>EL(e.ModalComponent,t)),",
        "function qAi(e){let t=(0,YAi.c)(7),n;t[0]===e.onClose?n=t[1]:(n=(0,HW.jsx)(JAi,{onClose:e.onClose}),t[0]=e.onClose,t[1]=n);let r;t[2]===e?r=t[3]:(r=(0,HW.jsx)(ZAi,{...e}),t[2]=e,t[3]=r);let i;return t[4]!==n||t[5]!==r?(i=(0,HW.jsx)(XAi.Suspense,{fallback:n,children:r}),t[4]=n,t[5]=r,t[6]=i):i=t[6],i}function JAi(e){let t=(0,YAi.c)(8),{onClose:n,failed:r}=e,i=r!==void 0&&r,a;t[0]===n?a=t[1]:(a=e=>{e||n()},t[0]=n,t[1]=a);let o;t[2]===i?o=t[3]:(o=i?(0,HW.jsx)(J,{id:`codex.rateLimitResetModal.loadError.title`,",
        "c=Fe(Z),l=eEi(),u=Ad(),d=Lu(),f=TW(),p=rm(),",
        "r=Fe(ch),[i,a]=(0,dzr.useState)(!1),o;if(t[0]!==r||t[1]!==n.tabId){",
        "t=ro(),n=zm(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:xer,",
        "i6=await r6.services,i6.threadReadState!=null",
        "function HJn(e){return e==null?null:YQ(e)}function XQ(e,t){return HJn(e)?.find(e=>e.turnId===t)??null}",
        "function yGr(e,t,n){let r={...tL(bGr,{}),[t]:n};",
        "let bt=Xr(yt),St=r?dt:bt.data,",
        "(r=(0,bE.jsx)(Q.Section,{sectionKey:`usage`,",
        "uCr=$(),dCr=Ep(eCr)})))()}var pCr,mCr,hCr,gCr,",
        "gut=Jh(),_ut=(0,gut.createContext)(tit)})))()}var yut,but,xut,Sut,Cut,wut,Tut,Eut,Dut,Out,kut,Aut,jut,rb,Mut,Nut,Put,Fut,Iut,Lut,Rut,zut,ib,But,Vut,Hut,Uut,Wut;",
        "i1n=Vd(),uF(),NQn(),a1n=(0,pF.createContext)(null)",
        "qAt=qp(Z,()=>Gr().homeModePreferences??gke({",
        "let e=qp(ch,[]),t=u(ch,e=>null);return{entries$:yd(ch,({",
        "O$.jsx)(B,{onSelect:()=>u?.(e),",
        "(s=(0,_$.jsx)(kl.Item,{leftIconAsset:oLe,onClick:r,children:o})",
    ),
    usage_status=(
        "async function rJr({additionalHeaders:e,signal:t}){try{return zqr(await OU.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":dmn.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t}))}",
        "async function rJr({additionalHeaders:e,signal:t}){try{return zqr(await codexMuxFilterUsageStatus(await OU.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":dmn.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t})))}",
    ),
)


RENDERER_BUILDS = (RENDERER_BUILD_12246, RENDERER_BUILD_12404, RENDERER_BUILD_12553)

PROFILE_QUERY_PATTERN = re.compile(
    r"let e=await [A-Za-z_$][\w$]*\.safeGet\(`/wham/profiles/me`\)"
)


class RendererBundle:
    """One renderer file whose anchors are each replaced exactly once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.text = path.read_text(encoding="utf-8")
        self.original = self.text

    def replace(self, anchor: str, replacement: str, description: str) -> None:
        if self.text.count(anchor) != 1:
            raise RuntimeError(f"could not find {description}")
        self.text = self.text.replace(anchor, replacement, 1)

    def substitute(
        self, pattern: re.Pattern[str], replacement: str, description: str
    ) -> None:
        self.text, count = pattern.subn(replacement, self.text, count=1)
        if count != 1:
            raise RuntimeError(f"could not find {description}")

    def inject(self, anchor: str, source: str, description: str) -> None:
        self.replace(anchor, source + "\n" + anchor, description)

    def save(self) -> None:
        if self.text != self.original:
            self.path.write_text(self.text, encoding="utf-8")


class RendererBundleSet:
    """Every renderer bundle. Builds keep moving code between eager bundles
    and lazy chunks, so each patch lands in the one bundle that holds its
    anchor exactly once."""

    def __init__(self, paths: list[Path]) -> None:
        self.bundles = [RendererBundle(path) for path in paths]

    def _holder(self, anchor: str, description: str) -> RendererBundle:
        holders = [bundle for bundle in self.bundles if anchor in bundle.text]
        if len(holders) != 1 or holders[0].text.count(anchor) != 1:
            raise RuntimeError(f"could not find {description}")
        return holders[0]

    def contains(self, anchor: str) -> bool:
        return sum(bundle.text.count(anchor) for bundle in self.bundles) == 1

    def replace(self, anchor: str, replacement: str, description: str) -> None:
        self._holder(anchor, description).replace(anchor, replacement, description)

    def inject(self, anchor: str, source: str, description: str) -> None:
        self._holder(anchor, description).inject(anchor, source, description)

    def substitute(
        self, pattern: re.Pattern[str], replacement: str, description: str
    ) -> None:
        holders = [bundle for bundle in self.bundles if pattern.search(bundle.text)]
        if len(holders) != 1:
            raise RuntimeError(f"could not find {description}")
        holders[0].substitute(pattern, replacement, description)

    def save(self) -> None:
        for bundle in self.bundles:
            bundle.save()


def injected_source(name: str, token: str, identifiers: dict[str, str]) -> str:
    source = (PROJECT_ROOT / "ui" / name).read_text(encoding="utf-8")
    source = source.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    source = source.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    return replace_javascript_identifiers(source, identifiers)


def patch_renderer(extracted: Path, token: str) -> None:
    webview = extracted / "webview"
    index_path = webview / "index.html"
    index = index_path.read_text(encoding="utf-8")

    connect_anchor = "connect-src &#39;self&#39;"
    if connect_anchor not in index:
        raise RuntimeError("could not find ChatGPT renderer CSP connect-src")
    index = index.replace(
        connect_anchor,
        f"{connect_anchor} http://127.0.0.1:{CONTROL_PORT}",
        1,
    )
    index_path.write_text(index, encoding="utf-8")

    assets = webview / "assets"
    renderer = RendererBundleSet(sorted(assets.glob("*.js")))
    if any("function codexMuxRequest(" in bundle.text for bundle in renderer.bundles):
        raise RuntimeError("source app already contains the Codex multiplexer")
    build = next(
        (candidate for candidate in RENDERER_BUILDS if renderer.contains(candidate.marker)),
        None,
    )
    if build is None:
        raise RuntimeError("the ChatGPT renderer layout is not supported")
    for probe in build.identifier_probes:
        if not renderer.contains(probe):
            raise RuntimeError(f"could not verify an identifier probe: {probe[:60]!r}")

    renderer.inject(
        build.data_anchor,
        injected_source("account-data.js", token, {}),
        "the native app-server RPC accessor",
    )
    for check in build.plugin_request_checks:
        if not renderer.contains(check):
            raise RuntimeError(
                "could not verify the native Plugins request-to-RPC mapping"
            )
    renderer.replace(*build.plugin_request, "the native app-server request bridge")
    renderer.replace(*build.usage_status, "the native rate-limit status fetch")
    renderer.substitute(
        PROFILE_QUERY_PATTERN,
        "let e=await codexMuxProfileData("
        "globalThis.__codexMuxSelectedProfileAccountId??null)",
        "the native profile stats request",
    )
    renderer.replace(*build.reset_query, "the native reset-credit query")
    renderer.inject(
        build.fork_titles[0],
        injected_source("fork-titles.js", token, build.fork_identifiers),
        "the native turn-completion setup",
    )
    renderer.replace(*build.fork_titles, "the native turn-completion setup")
    renderer.replace(*build.reset_mutation, "the native reset-credit mutation")

    renderer.inject(
        build.menu_anchor,
        injected_source("account-menu.js", token, build.menu_identifiers),
        "the native ChatGPT profile menu component",
    )
    renderer.replace(*build.usage_slot, "the native ChatGPT usage menu slot")
    renderer.replace(
        build.usage_modal,
        build.usage_modal.replace(
            "(e){", "(e){globalThis.CodexMuxUseResetAccountState();", 1
        ),
        "the native Usage modal component",
    )
    renderer.replace(
        build.usage_windows,
        build.usage_windows.replace("=", "=window.__codexMuxSelectedUsageWindows??", 1),
        "the native usage-window selection",
    )
    renderer.replace(*build.usage_header, "the native Usage sheet header")
    for anchor in build.composer_actions:
        renderer.replace(
            anchor,
            anchor.replace("children:[", "children:[globalThis.codexMuxComposerAccount?.()??null,", 1),
            "the native composer footer actions",
        )
    renderer.replace(*build.profile_avatar, "the native Profile avatar")
    renderer.replace(*build.profile_name, "the native Profile display name")
    renderer.replace(
        *build.profile_identity, "the native Profile username and plan badge"
    )
    renderer.replace(*build.plugin_scope, "the native Plugins settings content")
    renderer.inject(
        build.thread_anchor,
        injected_source("thread-subscription.js", token, build.thread_identifiers),
        "the native thread summary sources component",
    )
    renderer.replace(*build.thread_sections, "the native thread summary section list")
    renderer.save()


def attach_router_updater(bootstrap: str) -> str:
    """Hand the updater the bootstrap initializes between importing the main
    process and running it to ui/router-updater.cjs, which serves the app's
    own update UI from the router's updater instead of Sparkle."""
    start = bootstrap.find("phase:`bootstrap-import-main`")
    end = bootstrap.find("runMainAppStartup:", start)
    if start < 0 or end < 0:
        raise RuntimeError("could not find the updater in the copied ChatGPT app")
    window = bootstrap[start:end]
    calls = list(re.finditer(r"await ([A-Za-z_$][\w$]*)\.initialize\(\)(?=[;,])", window))
    if len(calls) != 1:
        raise RuntimeError("could not find the updater in the copied ChatGPT app")
    call = calls[0]
    attach = (
        "await require(require(`node:path`).join(__dirname,`router-updater.cjs`))"
        f".attach({call.group(1)})"
    )
    return bootstrap[: start + call.start()] + attach + bootstrap[start + call.end() :]


UPDATER_MANAGER_API = (
    "setUpdateReady(e){",
    "setUpdateLifecycleState(e){",
    "inAppUpdatesLaunchPolicyResolution=",
    "hasUpdater(){return this.updater!=null}",
)


def disable_updater_lifecycle(extracted: Path) -> None:
    """Keep every updater entry point (launch gate, menu, IPC) from starting
    Sparkle, and check the manager still has what router-updater.cjs drives."""
    updater_anchor = (
        "initializeUpdater(){return this.options.enableUpdater?"
        "(this.updaterInitialization??=this.initializeUpdaterOnce(),"
        "this.updaterInitialization):Promise.resolve()}"
    )
    matches = [
        path
        for path in (extracted / ".vite" / "build").glob("*.js")
        if updater_anchor in path.read_text(encoding="utf-8")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one desktop updater lifecycle bundle, found {len(matches)}"
        )
    bundle_path = matches[0]
    bundle = bundle_path.read_text(encoding="utf-8")
    if bundle.count(updater_anchor) != 1:
        raise RuntimeError("could not find the desktop updater lifecycle")
    missing = [marker for marker in UPDATER_MANAGER_API if marker not in bundle]
    if missing:
        raise RuntimeError(f"the desktop updater manager changed shape: {missing}")
    bundle = bundle.replace(updater_anchor, "initializeUpdater(){return Promise.resolve()}", 1)
    bundle_path.write_text(bundle, encoding="utf-8")


def relax_native_pipe_peer_authorization(extracted: Path) -> None:
    """Let an ad-hoc signed copy serve its own node_repl over the native pipes.

    The desktop authorizes browser-use and host-services pipe peers by their
    code-signing identity. An ad-hoc signature has none, so every in-app
    browser and Computer Use request from the bundled node_repl is rejected
    with missing-code-signing-identity. The pipes are owner-only sockets, so
    accepting same-user peers keeps the boundary a team-signed build relies
    on in practice while making those features usable without a certificate.
    """
    main_files = list((extracted / ".vite" / "build").glob("main-*.js"))
    if len(main_files) != 1:
        raise RuntimeError(
            f"expected one ChatGPT desktop main bundle, found {len(main_files)}"
        )
    main_path = main_files[0]
    main = main_path.read_text(encoding="utf-8")
    authorizer_pattern = re.compile(
        r"function (?P<name>[A-Za-z_$][\w$]*)\(\)\{"
        r"if\(process\.platform!==`darwin`\)return\(\)=>\(\{authorized:!0\}\);"
        r"let e=[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*\.readFromPackageMetadata\(\),"
    )
    matches = list(authorizer_pattern.finditer(main))
    if len(matches) != 1:
        raise RuntimeError("could not find the native pipe peer authorizer")
    match = matches[0]
    head = f"function {match.group('name')}(){{"
    main = (
        main[: match.start()]
        + head
        + "return()=>({authorized:!0});"
        + main[match.start() + len(head) :]
    )
    main_path.write_text(main, encoding="utf-8")


def patch_desktop_profile(
    extracted: Path, installed_computer_use_app: Path
) -> None:
    """Give the copied Electron app its own user-data and single-instance scope."""
    bootstrap_files = list((extracted / ".vite" / "build").glob("bootstrap-*.js"))
    if len(bootstrap_files) != 1:
        raise RuntimeError(
            f"expected one ChatGPT bootstrap bundle, found {len(bootstrap_files)}"
        )

    bootstrap_path = bootstrap_files[0]
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    profile_pattern = re.compile(
        r"(?P<electron>[A-Za-z_$][\w$]*)\.app\.setPath\("
        r"`userData`,[A-Za-z_$][\w$]*\(\{"
        r"appDataPath:(?P=electron)\.app\.getPath\(`appData`\),"
        r"buildFlavor:[^,}]+,env:process\.env\}\)\)"
    )

    def replacement(match: re.Match[str]) -> str:
        electron = match.group("electron")
        computer_use_pipe = json.dumps(str(DEFAULT_STATE_ROOT / "computer-use.sock"))
        computer_use_app = json.dumps(str(installed_computer_use_app))
        return (
            f"process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH={computer_use_pipe};"
            f"process.env.SKY_CUA_SERVICE_PATH={computer_use_app};"
            f"process.env.CODEX_ELECTRON_COMPUTER_USE_APP_PATH={computer_use_app};"
            "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;"
            f"{electron}.app.setPath(`userData`,"
            f"{electron}.app.getPath(`appData`)+`/{DESKTOP_PROFILE_NAME}`)"
        )

    bootstrap, replacements = profile_pattern.subn(replacement, bootstrap, count=1)
    if replacements != 1:
        raise RuntimeError("could not isolate the copied ChatGPT desktop profile")

    # Updates come from the router's own releases, never an unpatched official build.
    bootstrap = attach_router_updater(bootstrap)
    bootstrap_path.write_text(bootstrap, encoding="utf-8")
    disable_updater_lifecycle(extracted)

    main_files = list((extracted / ".vite" / "build").glob("main-*.js"))
    if len(main_files) != 1:
        raise RuntimeError(
            f"expected one ChatGPT desktop main bundle, found {len(main_files)}"
        )
    main_path = main_files[0]
    main = main_path.read_text(encoding="utf-8")
    managed_service_pattern = re.compile(
        r"(?P<prefix>[A-Za-z_$][\w$]*=new [A-Za-z_$][\w$]*\()"
        r"[A-Za-z_$][\w$]*\([A-Za-z_$][\w$]*\.codexHome\)"
        r"(?P<suffix>,\{onServiceAvailable:)"
    )
    main, managed_service_replacements = managed_service_pattern.subn(
        lambda match: (
            match.group("prefix")
            + json.dumps(str(installed_computer_use_app))
            + match.group("suffix")
        ),
        main,
        count=1,
    )
    if managed_service_replacements != 1:
        raise RuntimeError(
            "could not pin the managed Computer Use service to its installed app"
        )

    computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use."
    )
    strict_computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use via node_repl and "
        "@oai/sky only. Never use shell commands, open, AppleScript, osascript, "
        "JXA, System Events, or CGEvent synthesis for computer interactions or "
        "as a fallback. If Computer Use is unavailable, report the failure "
        "instead of using another automation method."
    )
    if main.count(computer_use_instruction) != 1:
        raise RuntimeError("could not find the Computer Use tool instruction")
    main = main.replace(
        computer_use_instruction,
        strict_computer_use_instruction,
        1,
    )
    for module in ("ui-test-bridge.cjs", "router-updater.cjs"):
        shutil.copy2(PROJECT_ROOT / "ui" / module, extracted / ".vite" / "build" / module)
    main += (
        "\n;if(process.env.CODEX_MUX_UI_TESTS===`1`)"
        "require(require(`node:path`).join(__dirname,`ui-test-bridge.cjs`)).start();"
    )
    main_path.write_text(main, encoding="utf-8")


def asar_header_digest(asar_path: Path) -> str:
    """Hash the ASAR header the way Electron's integrity check does."""
    with asar_path.open("rb") as handle:
        _, _, _, header_length = struct.unpack("<IIII", handle.read(16))
        header = handle.read(header_length)
    if len(header) != header_length:
        raise RuntimeError("could not read the repacked ASAR header")
    return hashlib.sha256(header).hexdigest()


def electron_framework(app: Path) -> Path:
    frameworks = list((app / "Contents" / "Frameworks").glob("* Framework.framework"))
    if len(frameworks) != 1:
        raise RuntimeError(f"expected one Electron framework, found {len(frameworks)}")
    return frameworks[0]


def asar_integrity_seal(binary: bytes) -> int | None:
    """Where the framework keeps its Info.plist integrity digest, or None when
    the build predates the seal or ships it disabled."""
    at = binary.find(ASAR_INTEGRITY_SENTINEL)
    if at < 0:
        return None
    if binary.find(ASAR_INTEGRITY_SENTINEL, at + 1) >= 0:
        raise RuntimeError("found more than one ASAR integrity seal")
    enabled, version = binary[at + 32], binary[at + 33]
    if not enabled:
        return None
    if version != 1:
        raise RuntimeError(f"unsupported ASAR integrity seal version {version}")
    return at + 34


def seal_asar_integrity(app: Path, identity: str) -> None:
    """Point the framework's seal at the repacked archive's Info.plist entry,
    then re-sign the framework and the helpers that load it."""
    framework = electron_framework(app)
    binary_path = framework / "Versions" / "Current" / framework.stem
    binary = bytearray(binary_path.read_bytes())
    digest_at = asar_integrity_seal(binary)
    if digest_at is None:
        return
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        integrity = plistlib.load(handle)["ElectronAsarIntegrity"]
    binary[digest_at : digest_at + 32] = hashlib.sha256(
        "".join(
            path + entry["algorithm"] + entry["hash"]
            for path, entry in sorted(integrity.items())
        ).encode()
    ).digest()
    binary_path.write_bytes(binary)
    # Like the main executable, helpers run without library validation so they
    # can load the re-signed framework beside the official libraries.
    for helper in sorted((framework / "Versions" / "Current" / "Helpers").glob("*.app")):
        sign_runtime_bundle(helper, identity, runtime=False)
    run(["codesign", "--force", "--sign", identity, "--timestamp=none", str(framework)])


def patch_info_plist(
    app: Path,
    asar_path: Path,
    team_identifier: str | None,
) -> None:
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleDisplayName"] = DESKTOP_DISPLAY_NAME
    info["CFBundleName"] = DESKTOP_DISPLAY_NAME
    # A distinct identifier keeps Launch Services and external Computer Use from
    # confusing this independently signed copy with the official ChatGPT app.
    info["CFBundleIdentifier"] = DESKTOP_BUNDLE_IDENTIFIER
    info["CFBundleExecutable"] = "CodexSubscriptionRouterLauncher"
    info["BundleSigningBaseName"] = "CodexSubscriptionRouter"
    info["CodexMuxSigningTeamIdentifier"] = team_identifier or "adhoc"
    info["CodexMuxVersion"] = PROJECT_VERSION
    info["CrProductDirName"] = DESKTOP_PROFILE_NAME
    for key in list(info):
        if key.startswith("SU"):
            del info[key]
    info["SUEnableAutomaticChecks"] = False
    info["SUAllowsAutomaticUpdates"] = False
    for url_type in info.get("CFBundleURLTypes", []):
        schemes = url_type.get("CFBundleURLSchemes", [])
        url_type["CFBundleURLSchemes"] = [
            "codex-subscription-router" if value == "codex" else value for value in schemes
        ]
    info["ElectronAsarIntegrity"] = {
        "Resources/app.asar": {
            "algorithm": "SHA256",
            "hash": asar_header_digest(asar_path),
        }
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)


def prune_backups(backups: Path, keep: int) -> None:
    """Drop older backups before a new one is taken; each holds a full app
    bundle, so only the copy replaced by the newest install is kept."""
    if not backups.is_dir():
        return
    dated = sorted(
        path for path in backups.iterdir()
        if path.is_dir() and re.fullmatch(r"\d{8}-\d{6}", path.name)
    )
    for stale in dated[:-keep] if keep else dated:
        shutil.rmtree(stale)
        print(f"Removed old backup {stale}")


def patch_app(
    source: Path,
    destination: Path,
    force: bool,
    allow_adhoc_signing: bool,
    allow_untested_source: bool,
    allow_signing_team_change: bool,
    stage: Path | None = None,
) -> None:
    """Build the router app for DESTINATION and install it there, or with
    STAGE leave the finished pair in that directory for install_staged."""
    source = source.expanduser().resolve()
    stage = stage.expanduser().resolve() if stage is not None else None
    destination = destination.expanduser().resolve()
    if not source.is_dir() or not (source / "Contents" / "Resources" / "app.asar").is_file():
        raise RuntimeError(f"not a ChatGPT app bundle: {source}")
    if source == destination:
        raise RuntimeError(
            "source and destination must be different; "
            "the original app is never patched in place"
        )
    if destination.exists() and not force and stage is None:
        raise RuntimeError(
            f"destination exists: {destination} "
            "(pass --force to create a recoverable backup)"
        )

    source_plist = source / "Contents" / "Info.plist"
    with source_plist.open("rb") as handle:
        source_info = plistlib.load(handle)
    source_version = str(source_info.get("CFBundleShortVersionString", "unknown"))
    source_build = str(source_info.get("CFBundleVersion", "unknown"))
    source_asar = source / "Contents" / "Resources" / "app.asar"
    source_asar_hash = hashlib.sha256(source_asar.read_bytes()).hexdigest()
    source_spec = SUPPORTED_BUILDS.get((source_version, source_build), UNTESTED_BUILD)
    expected_asar_hash = source_spec.asar_sha256
    print(
        f"Source ChatGPT version: {source_version} ({source_build}), "
        f"app.asar {source_asar_hash}"
    )
    if expected_asar_hash != source_asar_hash and not allow_untested_source:
        raise RuntimeError(
            "the source version, build, or app.asar hash is not approved; "
            "review the upstream change or pass --allow-untested-source"
        )
    if expected_asar_hash != source_asar_hash:
        print(
            "Warning: continuing with an untested official ChatGPT build; "
            "the patch will continue only while every expected anchor matches.",
            file=sys.stderr,
        )

    for tool in ("codesign", "ditto", "go", "npm", "security", "xcrun"):
        require_tool(tool)
    asar = ensure_asar_tool()
    token = load_or_create_token()
    signing_identity = resolve_signing_identity(allow_adhoc_signing)
    team_identifier = signing_team_identifier(signing_identity)
    if destination.exists():
        installed_team = existing_signing_team(destination)
        if installed_team != team_identifier and not allow_signing_team_change:
            raise RuntimeError(
                "the selected signing team differs from the installed build; "
                "reuse the prior identity or pass --allow-signing-team-change"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    installed_computer_use_app = destination.parent / COMPUTER_USE_APP_NAME
    if force and stage is None:
        ensure_components_are_stopped((destination, installed_computer_use_app))
    work_parent = destination.parent if stage is None else stage.parent
    work_parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".codex-subscription-router-", dir=work_parent) as temporary:
        temporary_path = Path(temporary)
        staged_app = temporary_path / destination.name
        staged_computer_use_app = temporary_path / COMPUTER_USE_APP_NAME
        extracted = temporary_path / "asar"
        proxy = temporary_path / "codex-mux"

        print("Building multiplexer…")
        build_proxy(proxy)
        print("Copying ChatGPT.app…")
        run(["ditto", str(source), str(staged_app)])
        install_launcher(staged_app)

        resources = staged_app / "Contents" / "Resources"
        original_asar = resources / "app.asar"
        print("Patching desktop profile and renderer…")
        run([str(asar), "extract", str(original_asar), str(extracted)])
        patch_asar_computer_use_identity(
            extracted, source_spec.asar_cua_identifier_replacements
        )
        patch_desktop_profile(extracted, installed_computer_use_app)
        if signing_identity == "-":
            relax_native_pipe_peer_authorization(extracted)
        patch_renderer(extracted, token)
        sign_native_code_tree(extracted, signing_identity)
        repacked_asar = temporary_path / "app.asar"
        run(
            [
                str(asar),
                "pack",
                "--unpack-dir",
                ASAR_UNPACK_DIRECTORIES,
                str(extracted),
                str(repacked_asar),
            ]
        )
        asar_listing = output([str(asar), "list", "--is-pack", str(repacked_asar)])
        required_unpacked_module = (
            "unpack : /node_modules/better-sqlite3/build/Release/"
            "better_sqlite3.node"
        )
        if required_unpacked_module not in asar_listing:
            raise RuntimeError("native ASAR modules were not kept unpacked")
        shutil.copy2(repacked_asar, original_asar)
        repacked_unpacked = temporary_path / "app.asar.unpacked"
        if not repacked_unpacked.is_dir():
            raise RuntimeError("ASAR pack did not produce its unpacked native tree")
        shutil.copytree(
            repacked_unpacked,
            resources / "app.asar.unpacked",
            dirs_exist_ok=True,
        )

        # The official binary keeps its own signature beside the router, which
        # finds it as `codex.real` in its own directory.
        bundled_codex = codex_entrypoint(resources)
        real_codex = bundled_codex.with_name("codex.real")
        if real_codex.exists():
            raise RuntimeError("source app already contains codex.real")
        bundled_codex.rename(real_codex)
        shutil.copy2(proxy, bundled_codex)
        bundled_codex.chmod(0o755)

        patch_info_plist(staged_app, original_asar, team_identifier)
        print(f"Signing independent app copy with {signing_identity}…")
        seal_asar_integrity(staged_app, signing_identity)
        sign_independent_app(
            staged_app,
            signing_identity,
            team_identifier,
            source_spec.cua_identifier_replacements,
            source_spec.cua_service_layout,
        )
        verify_signed_code(
            staged_app,
            DESKTOP_BUNDLE_IDENTIFIER,
            team_identifier,
        )
        verify_signed_code(
            staged_app / "Contents" / "MacOS" / "ChatGPT",
            OPENAI_DESKTOP_CODE_IDENTIFIER,
            team_identifier,
        )
        bundled_computer_use_app = (
            computer_use_package(staged_app) / "Codex Computer Use.app"
        )
        run(
            [
                "ditto",
                str(bundled_computer_use_app),
                str(staged_computer_use_app),
            ]
        )
        verify_signed_code(
            staged_computer_use_app,
            COMPUTER_USE_BUNDLE_IDENTIFIER,
            team_identifier,
        )

        if stage is not None:
            stage.mkdir(mode=0o700, exist_ok=True)
            for built, name in (
                (staged_app, destination.name),
                (staged_computer_use_app, COMPUTER_USE_APP_NAME),
            ):
                if (stage / name).exists():
                    shutil.rmtree(stage / name)
                built.rename(stage / name)
            print(stage / destination.name)
            return
        install_built(
            staged_app,
            staged_computer_use_app,
            destination,
            installed_computer_use_app,
        )


def install_staged(stage: Path, destination: Path) -> None:
    """Swap in a pair --stage built for this destination; the app must be quit."""
    stage = stage.expanduser().resolve()
    destination = destination.expanduser().resolve()
    staged_app = stage / destination.name
    staged_computer_use_app = stage / COMPUTER_USE_APP_NAME
    installed_computer_use_app = destination.parent / COMPUTER_USE_APP_NAME
    if not staged_app.is_dir() or not staged_computer_use_app.is_dir():
        raise RuntimeError(f"no staged build for {destination.name} in {stage}")
    if destination.exists() and existing_signing_team(destination) != existing_signing_team(
        staged_app
    ):
        raise RuntimeError("the staged build is signed by a different team than the installed one")
    ensure_components_are_stopped((destination, installed_computer_use_app))
    install_built(
        staged_app,
        staged_computer_use_app,
        destination,
        installed_computer_use_app,
    )
    stage.rmdir()


def install_built(
    staged_app: Path,
    staged_computer_use_app: Path,
    destination: Path,
    installed_computer_use_app: Path,
) -> None:
    """Move a finished app pair into place, keeping the replaced pair as the
    one backup."""
    backup_suffix = time.strftime("%Y%m%d-%H%M%S")
    backup_directory = DEFAULT_STATE_ROOT / "backups" / backup_suffix
    app_backup = backup_directory / destination.name
    helper_backup = backup_directory / installed_computer_use_app.name
    had_app = destination.exists()
    had_helper = installed_computer_use_app.exists()
    if had_app or had_helper:
        prune_backups(DEFAULT_STATE_ROOT / "backups", keep=0)
        backup_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup_directory.parent.chmod(0o700)
        backup_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    if had_app:
        stop_lingering_helpers(destination)
    try:
        if had_app:
            destination.rename(app_backup)
            print(f"Existing copy moved to {app_backup}")
        if had_helper:
            installed_computer_use_app.rename(helper_backup)
            print(f"Existing Computer Use helper moved to {helper_backup}")
        staged_app.rename(destination)
        staged_computer_use_app.rename(installed_computer_use_app)
    except OSError:
        failed_directory = backup_directory / "failed-install"
        failed_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.exists():
            destination.rename(failed_directory / destination.name)
        if installed_computer_use_app.exists():
            installed_computer_use_app.rename(
                failed_directory / installed_computer_use_app.name
            )
        if app_backup.exists():
            app_backup.rename(destination)
        if helper_backup.exists():
            helper_backup.rename(installed_computer_use_app)
        raise

    if LAUNCH_SERVICES_REGISTER.is_file():
        run(
            [
                str(LAUNCH_SERVICES_REGISTER),
                "-f",
                str(destination),
                str(installed_computer_use_app),
            ]
        )
    retire_stale_cached_computer_use_app()

    print(destination)
    print(installed_computer_use_app)


def main() -> int:
    args = parse_args()
    try:
        if args.install_staged:
            install_staged(args.install_staged, args.destination)
            return 0
        patch_app(
            args.source,
            args.destination,
            args.force,
            args.allow_adhoc_signing,
            args.allow_untested_source,
            args.allow_signing_team_change,
            args.stage,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
