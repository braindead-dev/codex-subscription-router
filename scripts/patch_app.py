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
import stat
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
ASAR_UNPACK_DIRECTORIES = (
    "node_modules/{@worklouder,better-sqlite3,node-mac-permissions,node-pty,objc-js}"
)
PREFERRED_SIGNING_IDENTITY_PREFIXES = (
    "Developer ID Application:",
    "Apple Development:",
)
OPENAI_INTERNAL_TEAM_IDENTIFIER = "HX7739G8FX"
OPENAI_DISTRIBUTION_TEAM_IDENTIFIER = "2DC432GLL2"
TESTED_SOURCE_BUILDS = {
    (
        "26.803.61601",
        "6396",
    ): "d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf",
    (
        "26.810.52044",
        "6662",
    ): "6e7e8791b8bf69a586ff994721fff518af391d9efdc66cd2e620dd2a4aedc90f",
    (
        "26.901.22334",
        "7746",
    ): "405f0e1600fc63851abe4c763ec0546f56c32da312c2c2745e2b997c579ce0d0",
}
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS = 49
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD = {
    ("26.803.61601", "6396"): 49,
    ("26.810.52044", "6662"): 99,
    ("26.901.22334", "7746"): 49,
}
DEFAULT_CUA_SERVICE_LAYOUT = (("Codex Computer Use.app", 17),)
EXPECTED_CUA_SERVICE_LAYOUT_BY_BUILD = {
    ("26.803.61601", "6396"): DEFAULT_CUA_SERVICE_LAYOUT,
    ("26.810.52044", "6662"): (
        ("Codex Computer Use.app", 17),
        ("bin/mac/normal/Codex Computer Use.app", 13),
    ),
    ("26.901.22334", "7746"): DEFAULT_CUA_SERVICE_LAYOUT,
}
EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS = 17
EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD = {
    ("26.803.61601", "6396"): 17,
    ("26.810.52044", "6662"): 20,
    ("26.901.22334", "7746"): 16,
}


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


def ensure_components_are_stopped(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if not path.exists():
            continue
        result = subprocess.run(
            ["pgrep", "-f", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
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
    """Move aside only a prior custom helper copied into the shared Codex home."""
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
    backup = cached_app.with_name(
        f"Codex Computer Use backup-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    cached_app.rename(backup)
    print(f"Stale cached Computer Use helper moved to {backup}")


def patch_computer_use_identity(
    app: Path,
    team_identifier: str | None,
    expected_replacements: int = EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
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
    expected_replacements: int = EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS,
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
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
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
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
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


def sign_independent_app(
    app: Path,
    identity: str,
    team_identifier: str | None,
    expected_cua_replacements: int = EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
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
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(app / "Contents" / "Resources" / "codex"),
        ]
    )
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
    ui_bundle_glob: str
    data_anchor: str
    menu_identifiers: dict[str, str]
    menu_anchor: str
    usage_slot: tuple[str, str]
    plugin_request: tuple[str, str]
    plugin_request_checks: tuple[str, ...]
    reset_query: tuple[str, str]
    reset_mutation: tuple[str, str]
    usage_modal: str
    usage_header: tuple[str, str]
    profile_avatar: tuple[str, str]
    profile_name: tuple[str, str]
    profile_identity: tuple[str, str]
    plugin_bundle_glob: str
    plugin_scope: tuple[str, str]
    thread_identifiers: dict[str, str]
    thread_anchor: str
    thread_sections: tuple[str, str]


RENDERER_BUILD_6396 = RendererBuild(
    marker="function wXc({sidebarFooter:e,triggerButton:t})",
    ui_bundle_glob="app-initial-*.js",
    data_anchor="function wXc({sidebarFooter:e,triggerButton:t})",
    menu_identifiers={},
    menu_anchor="function wXc({sidebarFooter:e,triggerButton:t})",
    usage_slot=("usageItems:Ge", "usageItems:(0,e7.jsx)(CodexMuxAccountMenu,{})"),
    plugin_request=(
        "function gm(e,t,n){return n==null?h6e.sendRequest(e,t):"
        "h6e.sendRequest(e,t,n)}",
        "function gm(e,t,n){let r=codexMuxScopePluginRequest(e,t);"
        "return n==null?h6e.sendRequest(e,r):h6e.sendRequest(e,r,n)}",
    ),
    plugin_request_checks=(
        '"list-apps":q9((e,{priority:t,source:n,timeoutMs:r,'
        "trace:i,...a})=>e.sendRequest(`app/list`,a,",
        '"list-installed-apps":q9((e,t)=>e.sendRequest(`app/installed`,t))',
        '"read-apps":q9((e,t)=>e.sendRequest(`app/read`,t))',
        '"login-mcp-server":q9((e,t)=>e.sendRequest(`mcpServer/oauth/login`,t))',
        '"list-mcp-server-status":K9((e,{priority:t,'
        "source:n,timeoutMs:r,trace:i,...a})=>e.listMcpServers(a,",
        "listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})",
        "let i=this.sendRequest(`mcpServerStatus/list`,e,t);",
    ),
    reset_query=(
        "function l6r(){let e=(0,$F.c)(1),t;return "
        "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
        "(t={queryKey:[`rate-limit-reset-credits`],queryFn:u6r,"
        "refetchInterval:vm.ONE_MINUTE,staleTime:vm.FIVE_SECONDS},e[0]=t):"
        "t=e[0],Lt(t)}",
        "function l6r(){let e=window.__codexMuxResetAccountId;return Lt({"
        "queryKey:[`rate-limit-reset-credits`,e??`primary`],"
        "queryFn:e?()=>codexMuxRateLimitResets(e):u6r,"
        "refetchInterval:vm.ONE_MINUTE,staleTime:vm.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function d6r(){let e=(0,$F.c)(3),t=lt(),n=zO(),r;return "
        "e[0]!==n||e[1]!==t?(r={mutationFn:f6r,onSuccess:(e,r)=>{"
        "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
        "let n=e.code===`reset`?e.credit?.id??i:i;"
        "t.setQueryData([`rate-limit-reset-credits`],e=>F3r(e,a,n))}"
        "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
        "e[0]=n,e[1]=t,e[2]=r):r=e[2],$t(r)}",
        "function d6r(){let e=lt(),t=zO(),n=window.__codexMuxResetAccountId,"
        "r=[`rate-limit-reset-credits`,n??`primary`];return $t({"
        "mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):f6r,"
        "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
        "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
        "n.credit?.id??a:a;e.setQueryData(r,e=>F3r(e,o,t))}"
        "Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="QLs",
    usage_header=(
        "let ve;t[46]===ge?ve=t[47]:"
        "(ve=(0,k2.jsxs)(LL,{children:[ge,_e]}),t[46]=ge,t[47]=ve);",
        "let ve=(0,k2.jsxs)(LL,{children:[ge,_e,"
        "window.__codexMuxResetAccountSelector??null]});",
    ),
    profile_avatar=(
        "children:[(0,$.jsxs)(`div`,{className:`relative mb-4 size-20`,"
        "children:[",
        "children:[globalThis.CodexMuxProfileAvatarStack?.("
        "{onSelect:()=>A.refetch()})??null,"
        "(0,$.jsxs)(`div`,{className:"
        "globalThis.CodexMuxProfileAvatarStack?"
        "`hidden`:`relative mb-4 size-20`,children:[",
    ),
    profile_name=(
        "className:`flex w-full justify-center`",
        "className:globalThis.__codexMuxSelectedProfileAccountId&&"
        "!A.isFetching?`flex w-full justify-center`:`hidden`",
    ),
    profile_identity=(
        "className:`mt-1 flex min-h-7 items-center gap-1.5 text-base leading-5 "
        "font-normal text-token-text-tertiary`",
        "className:globalThis.__codexMuxSelectedProfileAccountId&&"
        "!A.isFetching?`mt-1 flex min-h-7 items-center gap-1.5 text-base "
        "leading-5 font-normal text-token-text-tertiary`:`hidden`",
    ),
    plugin_bundle_glob="plugins-settings-*.js",
    plugin_scope=(
        "action:F,children:w})",
        "action:F,children:[globalThis.CodexMuxPluginScope?.()??null,w]})",
    ),
    thread_identifiers={},
    thread_anchor="function bE(){let e=(0,wE.c)(57)",
    thread_sections=(
        "children:[c,l,u,d,f,p,m,h,g,_,v,y,b,x]",
        "children:[c,l,u,d,f,(0,zE.jsx)(CodexMuxThreadSubscription,{}),"
        "p,m,h,g,_,v,y,b,x]",
    ),
)

RENDERER_BUILD_6662 = RendererBuild(
    marker="function Icl(e){let t=(0,Vcl.c)(248),",
    ui_bundle_glob="app-initial-*.js",
    data_anchor="function Icl(e){let t=(0,Vcl.c)(248),",
    menu_identifiers={
        "e7": "$5",
        "kXc": "Hcl",
        "Lo": "Fo",
        "BW": "RU",
        "QLs": "E$s",
        "_H": "GV",
        "S2": "E0",
        "CH": "ZV",
        "jLa": "x$a",
        "lt": "ct",
    },
    menu_anchor="function Icl(e){let t=(0,Vcl.c)(248),",
    usage_slot=("usageItems:Ct", "usageItems:(0,$5.jsx)(CodexMuxAccountMenu,{})"),
    plugin_request=(
        "function Bp(e,t,n){return n==null?N8e.sendRequest(e,t):"
        "N8e.sendRequest(e,t,n)}",
        "function Bp(e,t,n){let r=codexMuxScopePluginRequest(e,t);"
        "return n==null?N8e.sendRequest(e,r):N8e.sendRequest(e,r,n)}",
    ),
    plugin_request_checks=(
        '"list-apps":J9((e,{priority:t,source:n,timeoutMs:r,'
        "trace:i,...a})=>e.sendRequest(`app/list`,a,",
        '"list-installed-apps":J9((e,t)=>e.sendRequest(`app/installed`,t))',
        '"read-apps":J9((e,t)=>e.sendRequest(`app/read`,t))',
        '"login-mcp-server":J9((e,t)=>e.sendRequest(`mcpServer/oauth/login`,t))',
        '"list-mcp-server-status":q9((e,{priority:t,'
        "source:n,timeoutMs:r,trace:i,...a})=>e.listMcpServers(a,",
        "listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})",
        "let i=this.sendRequest(`mcpServerStatus/list`,e,t);",
    ),
    reset_query=(
        "function Ooi(){let e=(0,SI.c)(1),t;return "
        "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
        "(t={queryKey:[`rate-limit-reset-credits`],queryFn:koi,"
        "refetchInterval:Wp.ONE_MINUTE,staleTime:Wp.FIVE_SECONDS},e[0]=t):"
        "t=e[0],It(t)}",
        "function Ooi(){let e=window.__codexMuxResetAccountId;return It({"
        "queryKey:[`rate-limit-reset-credits`,e??`primary`],"
        "queryFn:e?()=>codexMuxRateLimitResets(e):koi,"
        "refetchInterval:Wp.ONE_MINUTE,staleTime:Wp.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function Aoi(){let e=(0,SI.c)(3),t=ct(),n=Uw(),r;return "
        "e[0]!==n||e[1]!==t?(r={mutationFn:joi,onSuccess:(e,r)=>{"
        "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
        "let n=e.code===`reset`?e.credit?.id??i:i;"
        "t.setQueryData([`rate-limit-reset-credits`],e=>eoi(e,a,n))}"
        "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
        "e[0]=n,e[1]=t,e[2]=r):r=e[2],Qt(r)}",
        "function Aoi(){let e=ct(),t=Uw(),n=window.__codexMuxResetAccountId,"
        "r=[`rate-limit-reset-credits`,n??`primary`];return Qt({"
        "mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):joi,"
        "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
        "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
        "n.credit?.id??a:a;e.setQueryData(r,e=>eoi(e,o,t))}"
        "Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="E$s",
    usage_header=(
        "let _e;t[46]===he?_e=t[47]:"
        "(_e=(0,I0.jsxs)(WL,{children:[he,ge]}),t[46]=he,t[47]=_e);",
        "let _e=(0,I0.jsxs)(WL,{children:[he,ge,"
        "window.__codexMuxResetAccountSelector??null]});",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "(0,$.jsxs)(`label`,{\"aria-disabled\":z.isPending,"
        "className:Le(`group relative flex size-20 rounded-full outline-none "
        "focus-within:ring-1 focus-within:ring-ring`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "globalThis.CodexMuxProfileAvatarStack?.("
        "{onSelect:()=>M.refetch()})??null,"
        "(0,$.jsxs)(`label`,{\"aria-disabled\":z.isPending,"
        "className:Le(globalThis.CodexMuxProfileAvatarStack?`hidden`:"
        "`group relative flex size-20 rounded-full outline-none "
        "focus-within:ring-1 focus-within:ring-ring`,",
    ),
    profile_name=(
        "displayName:Ze??(0,$.jsx)(o,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "displayName:globalThis.__codexMuxSelectedProfileAccountId?"
        "(Ze??(0,$.jsx)(o,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,"
        "description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "username:Ke==null?null:(0,$.jsx)(o,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:Ke}})",
        "username:globalThis.__codexMuxSelectedProfileAccountId&&Ke!=null?"
        "(0,$.jsx)(o,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:Ke}}):null",
    ),
    plugin_bundle_glob="plugins-page-*.js",
    plugin_scope=(
        "ee=(0,tc.jsxs)(tc.Fragment,{children:[H,U]})",
        "ee=(0,tc.jsxs)(tc.Fragment,{children:["
        "globalThis.CodexMuxPluginScope?.()??null,H,U]})",
    ),
    thread_identifiers={
        "$n": "jf",
        "sr": "Pa",
        "TE": "jy",
        "zE": "CE",
        "K": "q",
    },
    thread_anchor="function bE(){let e=(0,SE.c)(1)",
    thread_sections=(
        "children:[c,l,u,d,f,p,m,h,g,_,v,y,b,x]",
        "children:[c,l,u,d,f,(0,CE.jsx)(CodexMuxThreadSubscription,{}),"
        "p,m,h,g,_,v,y,b,x]",
    ),
)

# Build 7746 splits the renderer: data access and RPC live in app-initial,
# while the menu, usage, and alert surfaces render from app-primary.
RENDERER_BUILD_7746 = RendererBuild(
    marker=(
        "function wb(e,t){let n=e.get(Tb);"
        "if(n==null)throw Error(`AppServerManager RPC is not connected`);"
        "return n.forHost(t)}"
    ),
    ui_bundle_glob="app-primary-*.js",
    data_anchor=(
        "function wb(e,t){let n=e.get(Tb);"
        "if(n==null)throw Error(`AppServerManager RPC is not connected`);"
        "return n.forHost(t)}"
    ),
    menu_identifiers={
        "e7": "Pq",
        "kXc": "lgn",
        "Lo": "DO",
        "Q": "_S",
        "BW": "ru",
        "QLs": "jG",
        "_H": "fa",
        "S2": "sK",
        "CH": "Oo",
        "jLa": "jB",
        "lt": "Su",
    },
    menu_anchor="function $hn(e){let t=(0,tgn.c)(35),",
    usage_slot=("usageItems:Tt", "usageItems:(0,Iq.jsx)(CodexMuxAccountMenu,{})"),
    plugin_request=(
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error("
        "`AppServerRequestClient is missing a message dispatcher`);"
        "return e===`config/read`?",
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error("
        "`AppServerRequestClient is missing a message dispatcher`);"
        "t=codexMuxScopePluginRequest(e,t);return e===`config/read`?",
    ),
    plugin_request_checks=(
        "listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})",
        "let i=this.sendRequest(`mcpServerStatus/list`,e,t);",
    ),
    reset_query=(
        "function l2i(){let e=(0,RK.c)(1);HR(),lb(null);let t;return "
        "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
        "(t={queryKey:[`rate-limit-reset-credits`],queryFn:d2i,select:u2i,"
        "refetchInterval:gD.ONE_MINUTE,staleTime:gD.FIVE_SECONDS},e[0]=t):"
        "t=e[0],vb(t)}",
        "function l2i(){HR(),lb(null);let e=window.__codexMuxResetAccountId;"
        "return vb({queryKey:[`rate-limit-reset-credits`,e??`primary`],"
        "queryFn:e?()=>codexMuxRateLimitResets(e):d2i,select:u2i,"
        "refetchInterval:gD.ONE_MINUTE,staleTime:gD.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function f2i(){let e=(0,RK.c)(3),t=mb(),n=mD(),r;return "
        "e[0]!==n||e[1]!==t?(r={mutationFn:p2i,onSuccess:(e,r)=>{"
        "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
        "let n=e.code===`reset`?e.credit?.id??i:i;"
        "t.setQueryData([`rate-limit-reset-credits`],e=>Y1i(e,a,n))}"
        "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
        "e[0]=n,e[1]=t,e[2]=r):r=e[2],xb(r)}",
        "function f2i(){let e=mb(),t=mD(),n=window.__codexMuxResetAccountId,"
        "r=[`rate-limit-reset-credits`,n??`primary`];return xb({"
        "mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):p2i,"
        "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
        "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
        "n.credit?.id??a:a;e.setQueryData(r,e=>Y1i(e,o,t))}"
        "Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="jG",
    usage_header=(
        "let ge;t[46]===me?ge=t[47]:"
        "(ge=(0,AG.jsxs)(Gv,{children:[me,he]}),t[46]=me,t[47]=ge);",
        "let ge=(0,AG.jsxs)(Gv,{children:[me,he,"
        "window.__codexMuxResetAccountSelector??null]});",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "(0,$.jsxs)(`label`,{\"aria-disabled\":L.isPending,"
        "className:re(`group relative flex size-20 rounded-full outline-none "
        "focus-within:ring-1 focus-within:ring-ring`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "globalThis.CodexMuxProfileAvatarStack?.("
        "{onSelect:()=>j.refetch()})??null,"
        "(0,$.jsxs)(`label`,{\"aria-disabled\":L.isPending,"
        "className:re(globalThis.CodexMuxProfileAvatarStack?`hidden`:"
        "`group relative flex size-20 rounded-full outline-none "
        "focus-within:ring-1 focus-within:ring-ring`,",
    ),
    profile_name=(
        "displayName:Re??(0,$.jsx)(J,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "displayName:globalThis.__codexMuxSelectedProfileAccountId?"
        "(Re??(0,$.jsx)(J,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,"
        "description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "username:Ie==null?null:(0,$.jsx)(J,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:Ie}})",
        "username:globalThis.__codexMuxSelectedProfileAccountId&&Ie!=null?"
        "(0,$.jsx)(J,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:Ie}}):null",
    ),
    plugin_bundle_glob="plugins-page-*.js",
    plugin_scope=(
        "action:ee,children:oe})",
        "action:ee,children:[globalThis.CodexMuxPluginScope?.()??null,oe]})",
    ),
    thread_identifiers={
        "$n": "zd",
        "sr": "Gi",
        "TE": "nT",
        "zE": "mT",
        "K": "Q",
    },
    thread_anchor="function dT(){let e=(0,pT.c)(1),",
    thread_sections=(
        "children:[d,f,p,m,h,g,_,v,y,b,x,S,C,w,T,E,D]",
        "children:[d,f,p,m,h,g,_,v,y,b,x,S,C,"
        "(0,mT.jsx)(CodexMuxThreadSubscription,{}),w,T,E,D]",
    ),
)

RENDERER_BUILDS = (RENDERER_BUILD_6396, RENDERER_BUILD_6662, RENDERER_BUILD_7746)

USAGE_QUERY_PATTERN = re.compile(
    r"queryKey:\[`rate-limit-status`\],(?P<select>select:e=>e,)?"
    r"queryFn:async\(\)=>\{try\{(?P<lead>return |let e=)await "
    r"(?P<call>[A-Za-z_$][\w$]*\.safeGet\(`/wham/usage`"
    r"(?:,\{additionalHeaders:\{\"OAI-App-Brand\":[A-Za-z_$][\w$]*"
    r"\.toLowerCase\(\)\}\})?\))"
)
PROFILE_QUERY_PATTERN = re.compile(
    r"let e=await [A-Za-z_$][\w$]*\.safeGet\(`/wham/profiles/me`\)"
)
DEPLETED_ALERT_ANCHORS = (
    "defaultMessage:`You’re out of Codex and Work usage`",
    "defaultMessage:`You’ve used all Codex and Work usage`",
    "defaultMessage:`You’ve reached your usage limit`",
)


class RendererBundle:
    """One renderer file whose anchors are each replaced exactly once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.text = path.read_text(encoding="utf-8")

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
        self.path.write_text(self.text, encoding="utf-8")


def single_bundle(assets: Path, glob: str, anchor: str, description: str) -> Path:
    matches = [
        path
        for path in assets.glob(glob)
        if anchor in path.read_text(encoding="utf-8")
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {description}, found {len(matches)}")
    return matches[0]


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
    initial_bundles = list(assets.glob("app-initial-*.js"))
    if len(initial_bundles) != 1:
        raise RuntimeError(
            f"expected one ChatGPT initial renderer bundle, found {len(initial_bundles)}"
        )
    data = RendererBundle(initial_bundles[0])
    if "function codexMuxRequest(" in data.text:
        raise RuntimeError("source app already contains the Codex multiplexer")
    build = next(
        (candidate for candidate in RENDERER_BUILDS if candidate.marker in data.text),
        None,
    )
    if build is None:
        raise RuntimeError("the ChatGPT renderer layout is not supported")
    ui = (
        data
        if build.ui_bundle_glob == "app-initial-*.js"
        else RendererBundle(
            single_bundle(
                assets, build.ui_bundle_glob, build.menu_anchor, "ChatGPT UI bundle"
            )
        )
    )

    data.inject(
        build.data_anchor,
        injected_source("account-data.js", token, {}),
        "the native app-server RPC accessor",
    )
    for check in build.plugin_request_checks:
        if data.text.count(check) != 1:
            raise RuntimeError(
                "could not verify the native Plugins request-to-RPC mapping"
            )
    data.replace(*build.plugin_request, "the native app-server request bridge")
    data.substitute(
        USAGE_QUERY_PATTERN,
        r"queryKey:[`rate-limit-status`],\g<select>queryFn:async()=>{try{"
        r"\g<lead>await codexMuxFilterUsageStatus(await \g<call>)",
        "the native rate-limit status query",
    )
    data.substitute(
        PROFILE_QUERY_PATTERN,
        "let e=await codexMuxProfileData("
        "globalThis.__codexMuxSelectedProfileAccountId??null)",
        "the native profile stats request",
    )
    data.replace(*build.reset_query, "the native reset-credit query")
    data.replace(*build.reset_mutation, "the native reset-credit mutation")

    ui.inject(
        build.menu_anchor,
        injected_source("account-menu.js", token, build.menu_identifiers),
        "the native ChatGPT profile menu component",
    )
    ui.replace(*build.usage_slot, "the native ChatGPT usage menu slot")
    ui.replace(
        f"function {build.usage_modal}(e){{",
        f"function {build.usage_modal}(e){{CodexMuxUseResetAccountState();",
        "the native Usage modal component",
    )
    ui.replace(
        "let y=v;if(g!=null){",
        "let y=window.__codexMuxSelectedUsageWindows??v;if(g!=null){",
        "the native usage-window selection",
    )
    ui.replace(*build.usage_header, "the native Usage sheet header")
    for depleted_anchor in DEPLETED_ALERT_ANCHORS:
        ui.replace(
            depleted_anchor,
            "defaultMessage:`All connected subscriptions are depleted`",
            "a native subscription depletion alert",
        )
    data.save()
    if ui is not data:
        ui.save()

    profile = RendererBundle(
        single_bundle(
            assets,
            "profile-*.js",
            build.profile_avatar[0],
            "native Profile settings bundle",
        )
    )
    profile.replace(*build.profile_avatar, "the native Profile avatar")
    profile.replace(*build.profile_name, "the native Profile display name")
    profile.replace(
        *build.profile_identity, "the native Profile username and plan badge"
    )
    profile.save()

    plugins = RendererBundle(
        single_bundle(
            assets,
            build.plugin_bundle_glob,
            build.plugin_scope[0],
            "native Plugins settings bundle",
        )
    )
    plugins.replace(*build.plugin_scope, "the native Plugins settings content")
    plugins.save()

    thread = RendererBundle(
        single_bundle(
            assets,
            "local-conversation-thread-*.js",
            build.thread_anchor,
            "local conversation renderer bundle",
        )
    )
    thread.inject(
        build.thread_anchor,
        injected_source("thread-subscription.js", token, build.thread_identifiers),
        "the native thread summary sources component",
    )
    thread.replace(*build.thread_sections, "the native thread summary section list")
    thread.save()


def disable_updater_lifecycle(extracted: Path) -> None:
    """Keep every updater entry point (launch gate, menu, IPC) from starting Sparkle."""
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
    bundle = bundle.replace(
        updater_anchor,
        "initializeUpdater(){return this.lastUnavailableReason="
        f"`disabled by {DESKTOP_PROFILE_NAME}`,Promise.resolve()}}",
        1,
    )
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

    # The copied app must never replace itself with an unpatched official update.
    updater_pattern = re.compile(
        r"await [A-Za-z_$][\w$]*\.initialize\(\);"
        r"(?=(?:try\{)?let\{runMainAppStartup:)"
    )
    bootstrap, updater_replacements = updater_pattern.subn("", bootstrap, count=1)
    if updater_replacements != 1:
        raise RuntimeError("could not disable updates in the copied ChatGPT app")
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
    ui_test_bridge = extracted / ".vite" / "build" / "ui-test-bridge.cjs"
    shutil.copy2(PROJECT_ROOT / "ui" / "ui-test-bridge.cjs", ui_test_bridge)
    main += (
        "\n;if(process.env.CODEX_MUX_UI_TESTS===`1`)"
        "require(require(`node:path`).join(__dirname,`ui-test-bridge.cjs`)).start();"
    )
    main_path.write_text(main, encoding="utf-8")


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
    digest = hashlib.sha256(asar_path.read_bytes()).hexdigest()
    info["ElectronAsarIntegrity"] = {
        "Resources/app.asar": {"algorithm": "SHA256", "hash": digest}
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)


def patch_app(
    source: Path,
    destination: Path,
    force: bool,
    allow_adhoc_signing: bool,
    allow_untested_source: bool,
    allow_signing_team_change: bool,
) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_dir() or not (source / "Contents" / "Resources" / "app.asar").is_file():
        raise RuntimeError(f"not a ChatGPT app bundle: {source}")
    if source == destination:
        raise RuntimeError(
            "source and destination must be different; "
            "the original app is never patched in place"
        )
    if destination.exists() and not force:
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
    expected_asar_hash = TESTED_SOURCE_BUILDS.get((source_version, source_build))
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
    if force:
        ensure_components_are_stopped((destination, installed_computer_use_app))

    with tempfile.TemporaryDirectory(prefix=".codex-subscription-router-", dir=destination.parent) as temporary:
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
        expected_cua_replacements = (
            EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD.get(
                (source_version, source_build),
                EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS,
            )
        )
        patch_asar_computer_use_identity(extracted, expected_cua_replacements)
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

        bundled_codex = resources / "codex"
        real_codex = resources / "codex.real"
        if real_codex.exists():
            raise RuntimeError("source app already contains codex.real")
        bundled_codex.rename(real_codex)
        shutil.copy2(proxy, bundled_codex)
        bundled_codex.chmod(0o755)

        patch_info_plist(staged_app, original_asar, team_identifier)
        print(f"Signing independent app copy with {signing_identity}…")
        expected_cua_identity_replacements = (
            EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD.get(
                (source_version, source_build),
                EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
            )
        )
        cua_service_layout = EXPECTED_CUA_SERVICE_LAYOUT_BY_BUILD.get(
            (source_version, source_build),
            DEFAULT_CUA_SERVICE_LAYOUT,
        )
        sign_independent_app(
            staged_app,
            signing_identity,
            team_identifier,
            expected_cua_identity_replacements,
            cua_service_layout,
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

        backup_suffix = time.strftime("%Y%m%d-%H%M%S")
        backup_directory = DEFAULT_STATE_ROOT / "backups" / backup_suffix
        app_backup = backup_directory / destination.name
        helper_backup = backup_directory / installed_computer_use_app.name
        had_app = destination.exists()
        had_helper = installed_computer_use_app.exists()
        if had_app or had_helper:
            backup_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup_directory.parent.chmod(0o700)
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
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
        patch_app(
            args.source,
            args.destination,
            args.force,
            args.allow_adhoc_signing,
            args.allow_untested_source,
            args.allow_signing_team_change,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
