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
    ("26.908.40834", "8881"): SourceBuild(
        "bb40cd8811887363104a19291346af9595632e0e956316a1086b274fb8e3eafc"
    ),
    ("26.917.51856", "10492"): SourceBuild(
        "55861ddbcc5d965642441e167a349fefcc932426c85b61a5d5b7a9a2fc639d70"
    ),
    ("26.924.22138", "11645"): SourceBuild(
        "d0ba973179d2f717affd39e012b64a095464a54a51c6bccb7bc6b3d2a1cfba80"
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
        "--discard-existing",
        action="store_true",
        help="With --force, delete the existing destination instead of keeping a "
        "backup; for staging builds that are rebuilt often.",
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


def codex_entrypoint(resources: Path) -> Path:
    """The Codex binary the desktop launches: the packaged CLI app's
    executable since build 11645, a loose `codex` before it."""
    packaged = resources / "codex-cli" / "CodexCLI.app" / "Contents" / "MacOS" / "codex"
    return packaged if packaged.is_file() else resources / "codex"


def codex_signing_target(resources: Path) -> Path:
    """What to re-seal after the entrypoint is swapped: the enclosing CLI app,
    or the loose binary itself."""
    entrypoint = codex_entrypoint(resources)
    bundle = entrypoint.parent.parent.parent
    return bundle if bundle.suffix == ".app" else entrypoint


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
    codex_target = codex_signing_target(app / "Contents" / "Resources")
    if codex_target.suffix == ".app":
        # A re-sealed CLI app no longer matches its profile, so the official
        # binary keeps only the runtime entitlements it needs to run.
        (codex_target / "Contents" / "embedded.provisionprofile").unlink(missing_ok=True)
        sign_runtime_executable(
            codex_entrypoint(app / "Contents" / "Resources").with_name("codex.real"),
            identity,
        )
    run(["codesign", "--force", "--sign", identity, "--timestamp=none", str(codex_target)])
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
    usage_header: tuple[str, str]
    profile_avatar: tuple[str, str]
    profile_name: tuple[str, str]
    profile_identity: tuple[str, str]
    plugin_scope: tuple[str, str]
    thread_identifiers: dict[str, str]
    thread_anchor: str
    thread_sections: tuple[str, str]
    composer_actions: tuple[tuple[str, str], ...]
    fork_titles: tuple[str, str]
    fork_identifiers: dict[str, str]
    # Snippets that exist once in the build and name identifiers the
    # injected sources borrow; the porting tool reads them, the patcher
    # verifies them.
    identifier_probes: tuple[str, ...]
    usage_status: tuple[str, str]


RENDERER_BUILD_8881 = RendererBuild(
    marker=(
        "function Dm(e,t){let n=e.get(Om);"
        "if(n==null)throw Error(`AppServerManager RPC is not connected`);"
        "return n.forHost(t)}"
    ),
    data_anchor=(
        "function Dm(e,t){let n=e.get(Om);"
        "if(n==null)throw Error(`AppServerManager RPC is not connected`);"
        "return n.forHost(t)}"
    ),
    menu_identifiers={
        "e7": "Uz",
        "kXc": "vGt",
        "Lo": "Of",
        "Q": "o_",
        "BW": "ud",
        "QLs": "NL",
        "_H": "kg",
        "CH": "tS",
        "jLa": "$N",
        "lt": "rf",
        "Rv": "se",
        "RD": "DUt",
    },
    menu_anchor=(
        "function cGt(e){let t=(0,uGt.c)(41),{accountIcon:n,accountSwitcher:r,"
        "additionalItems:i,displayName:a,hasWorkspaceAccount:o,identityItems:s,"
        "isPetVisible:c,onCopyUserId:l,onLogOut:u,onOpenProfile:d,onOpenSettings:f,"
        "onOpenWorkspaceSettings:p,onTogglePet:m,personalPlanLabel:h,petShortcut:g,"
        "settingsShortcut:_,usageItems:v}=e"
    ),
    usage_slot=(
        "(j=(0,Gz.jsx)(cGt,{accountIcon:d,accountSwitcher:St,additionalItems:x,"
        "displayName:S,hasWorkspaceAccount:f,identityItems:C,isPetVisible:o,"
        "onCopyUserId:w,onLogOut:E,onOpenProfile:D,onOpenSettings:dt,"
        "onOpenWorkspaceSettings:O,personalPlanLabel:g,onTogglePet:k,petShortcut:he,"
        "settingsShortcut:me,usageItems:kt})",
        "(j=(0,Gz.jsx)(cGt,{accountIcon:d,accountSwitcher:St,additionalItems:x,"
        "displayName:S,hasWorkspaceAccount:f,identityItems:C,isPetVisible:o,"
        "onCopyUserId:w,onLogOut:E,onOpenProfile:D,onOpenSettings:dt,"
        "onOpenWorkspaceSettings:O,personalPlanLabel:g,onTogglePet:k,petShortcut:he,"
        "settingsShortcut:me,usageItems:(0,Gz.jsx)(CodexMuxAccountMenu,{})})",
    ),
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
        "function iji(){let e=(0,cG.c)(1);KD(),fm(null);let t;return e[0]===Symbol.for(`react.memo_cache_sentinel`)?(t={queryKey:[`rate-limit-reset-credits`],queryFn:oji,select:aji,refetchInterval:Qx.ONE_MINUTE,staleTime:Qx.FIVE_SECONDS},e[0]=t):t=e[0],xm(t)}",
        "function iji(){KD(),fm(null);let e=window.__codexMuxResetAccountId;return xm({queryKey:[`rate-limit-reset-credits`,e??`primary`],queryFn:e?()=>codexMuxRateLimitResets(e):oji,select:aji,refetchInterval:Qx.ONE_MINUTE,staleTime:Qx.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function sji(){let e=(0,cG.c)(3),t=_m(),n=Xx(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:cji,onSuccess:(e,r)=>{let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){let n=e.code===`reset`?e.credit?.id??i:i;t.setQueryData([`rate-limit-reset-credits`],e=>Wki(e,a,n))}Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},e[0]=n,e[1]=t,e[2]=r):r=e[2],wm(r)}",
        "function sji(){let e=_m(),t=Xx(),n=window.__codexMuxResetAccountId,r=[`rate-limit-reset-credits`,n??`primary`];return wm({mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):cji,onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?n.credit?.id??a:a;e.setQueryData(r,e=>Wki(e,o,t))}Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="function NL(e){let t=(0,_It.c)(20),{defaultResetCreditsOpen:n,",
    usage_header=(
        "(ge=(0,ML.jsx)(dC,{children:(0,ML.jsx)(Sh,{title:(0,ML.jsx)(MC,{asChild:!0,"
        "children:(0,ML.jsx)(`h2`,{className:`m-0`,children:(0,ML.jsx)(Z,"
        "{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,"
        "defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`"
        "})})})})}),t[41]=ge)",
        "(ge=(0,ML.jsxs)(dC,{children:[(0,ML.jsx)(Sh,{title:(0,ML.jsx)(MC,{asChild:!0,"
        "children:(0,ML.jsx)(`h2`,{className:`m-0`,children:(0,ML.jsx)(Z,"
        "{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,"
        "defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`"
        "})})})}),window.__codexMuxResetAccountSelector??null]}),t[41]=ge)",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "(0,$.jsxs)(`label`,{\"aria-disabled\":tt,"
        "className:zn(`group relative flex rounded-full outline-none "
        "focus-within:ring-1 focus-within:ring-ring`,r?`size-28`:`size-20`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:["
        "globalThis.CodexMuxProfileAvatarStack?.("
        "{onSelect:()=>W.refetch()})??null,"
        "(0,$.jsxs)(`label`,{\"aria-disabled\":tt,"
        "className:zn(globalThis.CodexMuxProfileAvatarStack?`hidden`:"
        "`group relative flex rounded-full outline-none "
        "focus-within:ring-1 focus-within:ring-ring`,r?`size-28`:`size-20`,",
    ),
    profile_name=(
        "displayName:Ot??(0,$.jsx)(J,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "displayName:globalThis.__codexMuxSelectedProfileAccountId?"
        "(Ot??(0,$.jsx)(J,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,"
        "description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "username:Et==null?null:(0,$.jsx)(J,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:Et}})",
        "username:globalThis.__codexMuxSelectedProfileAccountId&&Et!=null?"
        "(0,$.jsx)(J,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:Et}}):null",
    ),
    plugin_scope=(
        "subtitle:k,action:F,children:w})",
        "subtitle:k,action:F,children:[globalThis.CodexMuxPluginScope?.()??null,w]})",
    ),
    thread_identifiers={"K": "Z"},
    thread_anchor=(
        "function iO(e){let t=(0,aO.c)(4),{onOpenPullRequestSidePanel:n,"
        "onForceShow:r,registerEnvironmentActionCommands:i}=e,a=vi(S);"
    ),
    thread_sections=(
        "(C=(0,oO.jsxs)(oO.Fragment,{children:[m,h,g,_,v,y,b,x]})",
        "(C=(0,oO.jsxs)(oO.Fragment,{children:[m,h,g,_,v,"
        "(0,oO.jsx)(CodexMuxThreadSubscription,{}),y,b,x]})",
    ),
    composer_actions=(
        (
            "(0,B8.jsxs)(CP.FooterActions,{ref:Ke,spacing:Rt,children:[zt,It,Bt]})",
            "(0,B8.jsxs)(CP.FooterActions,{ref:Ke,spacing:Rt,"
            "children:[globalThis.codexMuxComposerAccount?.()??null,zt,It,Bt]})",
        ),
        (
            "(0,B8.jsxs)(CP.FooterActions,{spacing:`none`,children:[It,"
            "(0,B8.jsx)(`div`,{className:`ms-2 flex items-center`,children:pt})]})",
            "(0,B8.jsxs)(CP.FooterActions,{spacing:`none`,"
            "children:[globalThis.codexMuxComposerAccount?.()??null,It,"
            "(0,B8.jsx)(`div`,{className:`ms-2 flex items-center`,children:pt})]})",
        ),
    ),
    fork_titles=(
        "function PUn(e,t){let n=new Map,r=i=>{let a=n.get(i),"
        "o=t.getConversation(i)?.title?.trim()??``;",
        "function PUn(e,t){codexMuxForkTitles(e,t);let n=new Map,r=i=>{let a=n.get(i),"
        "o=t.getConversation(i)?.title?.trim()??``;",
    ),
    fork_identifiers={
        "CODEX_MUX_SERVICES": "Iq",
        "codexMuxConversationTurns": "Awn",
        "codexMuxTurnWithId": "gT",
        "codexMuxRememberDescription": "AHn",
    },
    identifier_probes=(
        "rateLimitReachedType:null}}}var Wz,vGt,Gz,yGt,bGt,xGt=t((()=>{Wz=a(),",
        "(0,Uz.jsxs)(kg,{className:d==null?`opacity-100`:void 0,"
        "disabled:d==null&&l==null,",
        "(0,Uz.jsx)(tS.ItemIcon,{size:`sm`,children:n})",
        "function NL(e){let t=(0,_It.c)(20),{defaultResetCreditsOpen:n,"
        "initialAvailableCount:r,isRateLimitReached:i,onClose:a,onResetComplete:o}=e,"
        "s=Of(o_),c=yf(),l=rf(),",
        "ud(l,eRt,{availableResetCount:M,analyticsEnabled:r,",
        "cR=e=>(0,sR.jsxs)(`svg`,{width:20,height:20,viewBox:`0 0 20 20`,fill:`none`,"
        "xmlns:`http://www.w3.org/2000/svg`,...e,children:[(0,sR.jsx)(`path`,{d:`M10.8343 12.",
        "function $N(e){return Ygt(e).src}function Ygt(e){let t=(0,Qgt.c)(5),",
        ",l=Of(se),u=yf(),d=(0,Yat.useContext)(at),f=i===`restricted`||d===`restricted`,",
        "Oz=n(i(),1),DUt=n(Uw(),1),",
        "(0,oO.jsx)(Z.Section,{sectionKey:`usage`,title:(0,oO.jsx)(X,"
        "{id:`codex.localConversation.usage.title`,",
        "Iq=await z0i.services,Iq.threadReadState!=null",
        "function Awn(e){return e==null?null:hT(e)}"
        "function gT(e,t){return Awn(e)?.find(e=>e.turnId===t)??null}",
        "jUn(t,i,t.getConversationCwd(i),()=>dx(e,NYt)).then(t=>{t!=null&&AHn(e,i,t)})",
    ),
    usage_status=(
        "async function LCa({additionalHeaders:e,signal:t}){try{let n=await DS.safeGet("
        "`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":SS.toLowerCase(),...e},signal:t}),",
        "async function LCa({additionalHeaders:e,signal:t}){try{"
        "let n=await codexMuxFilterUsageStatus(await DS.safeGet("
        "`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":SS.toLowerCase(),...e},signal:t})),",
    ),
)


# Build 10492 (26.917.51856, Codex 0.155.0-alpha.16) moves the profile menu,
# usage sheet, and reset-credit code into the initial bundle, drops title
# reconsideration, and redesigns the profile page. React, the JSX runtime,
# and ReactDOM are live imports from the shared chunk.
RENDERER_BUILD_10492 = RendererBuild(
    marker="function wd(e,t){let n=e.get(Td);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    data_anchor="function wd(e,t){let n=e.get(Td);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    menu_identifiers={
        "e7": "j4",
        "kXc": "mys",
        "Lo": "Ar",
        "Q": "X",
        "BW": "Nj",
        "QLs": "uIo",
        "_H": "Jr",
        "CH": "co",
        "jLa": "ads",
        "lt": "ut",
        "Rv": "Rb",
        "RD": "Ur()",
    },
    menu_anchor="function rys(e){let t=(0,ays.c)(43),{accountIcon:n,accountSwitcher:r,additionalItems:i,displayName:a,hasWorkspaceAccount:o,identityItems:s,isPetVisible:c,onCopyUserId:l,onLogOut:u,onOpenProfile:d,onOpenSettings:f,onOpenWorkspaceSettings:p,onTogglePet:m,personalPlanLabel:h,petShortcut:g,settingsShortcut:_,usageItems:v}=e",
    usage_slot=(
        "(M=(0,M4.jsx)(rys,{accountIcon:o,accountSwitcher:Ft,additionalItems:x,displayName:S,hasWorkspaceAccount:f,identityItems:C,isPetVisible:c,onCloseMenu:w,onCopyUserId:T,onLogOut:E,onOpenPersonalization:D,onOpenProfile:O,onOpenSettings:wt,onOpenWorkspaceSettings:k,personalPlanLabel:m,onTogglePet:j,petShortcut:De,settingsShortcut:Ee,usageItems:Ht})",
        "(M=(0,M4.jsx)(rys,{accountIcon:o,accountSwitcher:Ft,additionalItems:x,displayName:S,hasWorkspaceAccount:f,identityItems:C,isPetVisible:c,onCloseMenu:w,onCopyUserId:T,onLogOut:E,onOpenPersonalization:D,onOpenProfile:O,onOpenSettings:wt,onOpenWorkspaceSettings:k,personalPlanLabel:m,onTogglePet:j,petShortcut:De,settingsShortcut:Ee,usageItems:(0,M4.jsx)(CodexMuxAccountMenu,{})})",
    ),
    plugin_request=(
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?",
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);t=codexMuxScopePluginRequest(e,t);return e===`config/read`?",
    ),
    plugin_request_checks=(
        "listMcpServers(e,t){return dCt(this,this.mcpServerStatusPromises,e,t)}",
        "let s=e.sendRequest(`mcpServerStatus/list`,n,i);",
    ),
    reset_query=(
        "function zai(){let e=(0,uz.c)(1);HS(),cr(null);let t;return e[0]===Symbol.for(`react.memo_cache_sentinel`)?(t={queryKey:[`rate-limit-reset-credits`],queryFn:Vai,select:Bai,refetchInterval:_.ONE_MINUTE,staleTime:_.FIVE_SECONDS},e[0]=t):t=e[0],Yi(t)}",
        "function zai(){HS(),cr(null);let e=window.__codexMuxResetAccountId;return Yi({queryKey:[`rate-limit-reset-credits`,e??`primary`],queryFn:e?()=>codexMuxRateLimitResets(e):Vai,select:Bai,refetchInterval:_.ONE_MINUTE,staleTime:_.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function Hai(){let e=(0,uz.c)(3),t=ut(),n=Zg(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:Uai,onSuccess:(e,r)=>{let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){let n=e.code===`reset`?e.credit?.id??i:i;t.setQueryData([`rate-limit-reset-credits`],e=>uai(e,a,n))}Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},e[0]=n,e[1]=t,e[2]=r):r=e[2],qa(r)}",
        "function Hai(){let e=ut(),t=Zg(),n=window.__codexMuxResetAccountId,r=[`rate-limit-reset-credits`,n??`primary`];return qa({mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):Uai,onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?n.credit?.id??a:a;e.setQueryData(r,e=>uai(e,o,t))}Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="function uIo(e){let t=(0,dIo.c)(19),{defaultResetCreditsOpen:n,",
    usage_header=(
        "(Se=(0,H$.jsx)(lo,{children:(0,H$.jsx)(_e,{title:(0,H$.jsx)(si,{asChild:!0,children:(0,H$.jsx)(`h2`,{className:`m-0`,children:(0,H$.jsx)(Y,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})})}),t[41]=Se)",
        "(Se=(0,H$.jsxs)(lo,{children:[(0,H$.jsx)(_e,{title:(0,H$.jsx)(si,{asChild:!0,children:(0,H$.jsx)(`h2`,{className:`m-0`,children:(0,H$.jsx)(Y,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})}),window.__codexMuxResetAccountSelector??null]}),t[41]=Se)",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`div`,{\"aria-disabled\":Gt,className:nt(`group relative flex rounded-full outline-none`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:[globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>Ke.refetch()})??null,(0,$.jsxs)(`div`,{\"aria-disabled\":Gt,className:nt(globalThis.CodexMuxProfileAvatarStack?`hidden`:`group relative flex rounded-full outline-none`,",
    ),
    profile_name=(
        "In=Sn??(0,$.jsx)(J,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "In=globalThis.__codexMuxSelectedProfileAccountId?(Sn??(0,$.jsx)(J,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "Ln=gn&&(0,$.jsx)(J,{id:`profile.usernameValue`,defaultMessage:`@{username}`,description:`Profile username shown with an at-sign prefix`,values:{username:gn}})",
        "Ln=globalThis.__codexMuxSelectedProfileAccountId&&gn&&(0,$.jsx)(J,{id:`profile.usernameValue`,defaultMessage:`@{username}`,description:`Profile username shown with an at-sign prefix`,values:{username:gn}})",
    ),
    plugin_scope=(
        "subtitle:k,action:F,children:D})",
        "subtitle:k,action:F,children:[globalThis.CodexMuxPluginScope?.()??null,D]})",
    ),
    thread_identifiers={
        "K": "Z",
    },
    thread_anchor="function TE(e){let t=(0,EE.c)(4),{onOpenPullRequestSidePanel:n,onForceShow:r,registerEnvironmentActionCommands:i}=e,a=J(bl),",
    thread_sections=(
        "(A=(0,DE.jsxs)(DE.Fragment,{children:[S,C,w,T,E,D,O,k]})",
        "(A=(0,DE.jsxs)(DE.Fragment,{children:[S,C,w,T,E,(0,DE.jsx)(CodexMuxThreadSubscription,{}),D,O,k]})",
    ),
    composer_actions=(
        (
            "(0,D3.jsxs)(BS.FooterActions,{ref:$e,spacing:Ut,children:[Wt,Vt,Gt]})",
            "(0,D3.jsxs)(BS.FooterActions,{ref:$e,spacing:Ut,children:[globalThis.codexMuxComposerAccount?.()??null,Wt,Vt,Gt]})",
        ),
        (
            "(0,D3.jsxs)(BS.FooterActions,{spacing:`none`,children:[Vt,(0,D3.jsx)(`div`,{className:`ms-2 flex items-center`,children:bt})]})",
            "(0,D3.jsxs)(BS.FooterActions,{spacing:`none`,children:[globalThis.codexMuxComposerAccount?.()??null,Vt,(0,D3.jsx)(`div`,{className:`ms-2 flex items-center`,children:bt})]})",
        ),
    ),
    fork_titles=(
        "function v9t(e,t){t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
        "function v9t(e,t){codexMuxForkTitles(e,t);t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
    ),
    fork_identifiers={
        "CODEX_MUX_SERVICES": "$H",
        "codexMuxConversationTurns": "g1t",
        "codexMuxTurnWithId": "_1t",
        "codexMuxRememberDescription": "iEn",
    },
    identifier_probes=(
        "var ays,j4;function oys(){return(oys=n((()=>{ays=c(),Cbe(),_te(),pe(),Cr(),gr(),xo(),j4=K()})))()}function sys(e){let t=(0,pys.c)(3),",
        "pys=c(),Shs(),Aa(),Sme(),Ase(),m_e(),Cbe(),_te(),Lae(),Ua(),W(),bs(),mys=Z(),",
        "function Nj(e,t,n,r){e.set(Ij,e=>{let i=e.modals.find(e=>Dpr(e.ModalComponent,t)),a={",
        "function uIo(e){let t=(0,dIo.c)(19),{defaultResetCreditsOpen:n,initialAvailableCount:r,isRateLimitReached:i,onClose:a,onResetComplete:o}=e,s=Ar(X),",
        "function ads(e){return ods(e).src}",
        "Rb=hi(`RouteScope`",
        "(0,j4.jsx)(co.ItemIcon,{size:`sm`,children:n})",
        "(0,j4.jsx)(Jr,{className:p==null?`opacity-100`:void 0,",
        "t=ut(),n=Zg(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:Uai,",
        "fPe=Ur(),Ec(),BMe(),pPe=(0,Oc.createContext)(null),mPe={draggable:",
        "$H=await jGi.services,$H.threadReadState!=null",
        "function g1t(e){return e==null?null:Ly(e)}function _1t(e,t){return g1t(e)?.find(e=>e.turnId===t)??null}",
        "function iEn(e,t,n){let r={...df(aEn,{}),[t]:n};",
        "(r=(0,DE.jsx)(Z.Section,{sectionKey:`usage`,",
        "let Ke=Br(Ge),qe=i?Re:Ke.data,",
    ),
    usage_status=(
        "async function $On({additionalHeaders:e,signal:t}){try{return LOn(await vg.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":rg.toLowerCase(),...e},signal:t}))}",
        "async function $On({additionalHeaders:e,signal:t}){try{return LOn(await codexMuxFilterUsageStatus(await vg.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":rg.toLowerCase(),...e},signal:t})))}",
    ),
)


# Build 11645 (26.924.22138, Codex 0.158.0-alpha.2.1) moves the profile menu and
# usage sheet into lazy chunks and the data layer into the shared chunk, and
# ships the CLI as a nested CodexCLI.app. Our menu stays in app-initial.
RENDERER_BUILD_11645 = RendererBuild(
    marker="function GU(e,t){let n=e.get(KU);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    data_anchor="function GU(e,t){let n=e.get(KU);if(n==null)throw Error(`AppServerManager RPC is not connected`);return n.forHost(t)}",
    menu_identifiers={
        "e7": "G()",
        "kXc": "Yp()",
        "Lo": "Jl",
        "Q": "Q",
        "BW": "tj",
        "QLs": "ryr",
        "_H": "Yge",
        "CH": "yp",
        "jLa": "zyi",
        "lt": "kr",
        "Rv": "kl",
        "RD": "Ur()",
    },
    menu_anchor="function zyi(e){return Byi(e).src}",
    usage_slot=(
        "(D=(0,Y.jsx)(In,{accountIcon:o,accountSwitcher:rr,additionalItems:_,displayName:b,hasWorkspaceAccount:c,identityItems:x,isPetVisible:d,onCloseMenu:s,onCopyUserId:S,onLogOut:C,onOpenPersonalization:w,onOpenProfile:ee,onOpenSettings:J,onOpenWorkspaceSettings:T,personalPlanLabel:m,onTogglePet:E,petShortcut:bt,settingsShortcut:yt,usageItems:sr})",
        "(D=(0,Y.jsx)(In,{accountIcon:o,accountSwitcher:rr,additionalItems:_,displayName:b,hasWorkspaceAccount:c,identityItems:x,isPetVisible:d,onCloseMenu:s,onCopyUserId:S,onLogOut:C,onOpenPersonalization:w,onOpenProfile:ee,onOpenSettings:J,onOpenWorkspaceSettings:T,personalPlanLabel:m,onTogglePet:E,petShortcut:bt,settingsShortcut:yt,usageItems:(0,Y.jsx)(globalThis.CodexMuxAccountMenu,{})})",
    ),
    plugin_request=(
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?",
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);t=codexMuxScopePluginRequest(e,t);return e===`config/read`?",
    ),
    plugin_request_checks=(
        "listMcpServers(e,t){return Y$t(this,this.mcpServerStatusPromises,e,t,",
        "l=e.sendRequest(`mcpServerStatus/list`,n,a)",
    ),
    reset_query=(
        "function Ppn(){let e=(0,kP.c)(1);ko(),Z(null);let t;return e[0]===Symbol.for(`react.memo_cache_sentinel`)?(t={queryKey:[`rate-limit-reset-credits`],queryFn:Ipn,select:Fpn,refetchInterval:Ds.ONE_MINUTE,staleTime:Ds.FIVE_SECONDS},e[0]=t):t=e[0],oh(t)}",
        "function Ppn(){ko(),Z(null);let e=window.__codexMuxResetAccountId;return oh({queryKey:[`rate-limit-reset-credits`,e??`primary`],queryFn:e?()=>codexMuxRateLimitResets(e):Ipn,select:Fpn,refetchInterval:Ds.ONE_MINUTE,staleTime:Ds.FIVE_SECONDS})}",
    ),
    reset_mutation=(
        "function Lpn(){let e=(0,kP.c)(3),t=kr(),n=nr(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:Rpn,onSuccess:(e,r)=>{let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){let n=e.code===`reset`?e.credit?.id??i:i;t.setQueryData([`rate-limit-reset-credits`],e=>spn(e,a,n))}Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},e[0]=n,e[1]=t,e[2]=r):r=e[2],eu(r)}",
        "function Lpn(){let e=kr(),t=nr(),n=window.__codexMuxResetAccountId,r=[`rate-limit-reset-credits`,n??`primary`];return eu({mutationFn:n?i=>codexMuxConsumeRateLimitReset(n,i):Rpn,onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?n.credit?.id??a:a;e.setQueryData(r,e=>spn(e,o,t))}Promise.all([t([`rate-limit-status`]),t(r)])}})}",
    ),
    usage_modal="function Et(e){let t=(0,Dt.c)(19),{defaultResetCreditsOpen:n,",
    usage_header=(
        "(Ae=(0,$.jsx)(r,{children:(0,$.jsx)(o,{title:(0,$.jsx)(s,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(O,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})})}),t[41]=Ae)",
        "(Ae=(0,$.jsxs)(r,{children:[(0,$.jsx)(o,{title:(0,$.jsx)(s,{asChild:!0,children:(0,$.jsx)(`h2`,{className:`m-0`,children:(0,$.jsx)(O,{id:`codex.rateLimitResetPromptModal.usageTrackingHeading`,defaultMessage:`Usage`,description:`Heading for the Codex usage limit modal`})})})}),window.__codexMuxResetAccountSelector??null]}),t[41]=Ae)",
    ),
    profile_avatar=(
        "avatar:(0,$.jsxs)($.Fragment,{children:[(0,$.jsxs)(`div`,{\"aria-disabled\":$t,className:Se(`group relative flex rounded-full outline-none`,",
        "avatar:(0,$.jsxs)($.Fragment,{children:[globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>rt.refetch()})??null,(0,$.jsxs)(`div`,{\"aria-disabled\":$t,className:Se(globalThis.CodexMuxProfileAvatarStack?`hidden`:`group relative flex rounded-full outline-none`,",
    ),
    profile_name=(
        "Qn=kn??(0,$.jsx)(Y,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})",
        "Qn=globalThis.__codexMuxSelectedProfileAccountId?(kn??(0,$.jsx)(Y,{id:`profile.nameFallback`,defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})):null",
    ),
    profile_identity=(
        "Tn=bn?wn:null,En=r?H?.display_name?.trim()||null:it?.displayName??null,",
        "Tn=globalThis.__codexMuxSelectedProfileAccountId&&bn?wn:null,En=r?H?.display_name?.trim()||null:it?.displayName??null,",
    ),
    plugin_scope=(
        "(C=(0,ao.jsx)(Pn,{title:h,subtitle:g,action:S,children:m})",
        "(C=(0,ao.jsx)(Pn,{title:h,subtitle:g,action:S,children:[globalThis.CodexMuxPluginScope?.()??null,m]})",
    ),
    thread_identifiers={
        "K": "Q",
    },
    thread_anchor="function yD(e){let t=(0,bD.c)(4),{onOpenPullRequestSidePanel:n,onForceShow:r,registerEnvironmentActionCommands:i}=e,a=zr(Er),",
    thread_sections=(
        "(k=(0,SD.jsxs)(SD.Fragment,{children:[x,S,C,w,T,E,D,O]})",
        "(k=(0,SD.jsxs)(SD.Fragment,{children:[x,S,C,w,T,(0,SD.jsx)(CodexMuxThreadSubscription,{}),E,D,O]})",
    ),
    composer_actions=(
        (
            "(0,K8.jsxs)(nE.FooterActions,{ref:st,spacing:en,children:[tn,Qt,nn]})",
            "(0,K8.jsxs)(nE.FooterActions,{ref:st,spacing:en,children:[globalThis.codexMuxComposerAccount?.()??null,tn,Qt,$t]})",
        ),
        (
            "(0,K8.jsxs)(nE.FooterActions,{spacing:`none`,children:[Qt,(0,K8.jsx)(`div`,{className:`ms-2 flex items-center`,children:Ot})]})",
            "(0,K8.jsxs)(nE.FooterActions,{spacing:`none`,children:[globalThis.codexMuxComposerAccount?.()??null,Qt,(0,K8.jsx)(`div`,{className:`ms-2 flex items-center`,children:Ot})]})",
        ),
    ),
    fork_titles=(
        "function nor(e,t){t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
        "function nor(e,t){codexMuxForkTitles(e,t);t.addTurnCompletedListener(n=>{if(n.status===`inProgress`||n.turnId==null)return;",
    ),
    fork_identifiers={
        "CODEX_MUX_SERVICES": "c5",
        "codexMuxConversationTurns": "WRn",
        "codexMuxTurnWithId": "f1",
        "codexMuxRememberDescription": "Vjr",
    },
    identifier_probes=(
        "function zyi(e){return Byi(e).src}",
        "function tj(e,t,n,r){e.set(ij,e=>{let i=e.modals.find(e=>u6t(e.ModalComponent,t)),",
        "function ryr(e){let t=(0,ayr.c)(7),n;t[0]===e.onClose?n=t[1]:(n=(0,nW.jsx)(iyr,{onClose:e.onClose}),t[0]=e.onClose,t[1]=n);let r;t[2]===e?r=t[3]:(r=(0,nW.jsx)(syr,{...e}),t[2]=e,t[3]=r);let i;return t[4]!==n||t[5]!==r?(i=(0,nW.jsx)(oyr.Suspense,{fallback:n,children:r}),t[4]=n,t[5]=r,t[6]=i):i=t[6],i}function iyr(e){let t=(0,ayr.c)(8),{onClose:n,failed:r}=e,i=r!==void 0&&r,a;t[0]===n?a=t[1]:(a=e=>{e||n()},t[0]=n,t[1]=a);let o;t[2]===i?o=t[3]:(o=i?(0,nW.jsx)(q,{id:`codex.rateLimitResetModal.loadError.title`,",
        "c=Jl(Q),l=OU(),u=Ss(),d=Yi(),f=AU(),",
        "r=Jl(kl),[i,a]=(0,Egt.useState)(!1),o;if(t[0]!==r||t[1]!==n.tabId){",
        "t=kr(),n=nr(),r;return e[0]!==n||e[1]!==t?(r={mutationFn:Rpn,",
        "c5=await s5.services,c5.threadReadState!=null",
        "function WRn(e){return e==null?null:d1(e)}function f1(e,t){return WRn(e)?.find(e=>e.turnId===t)??null}",
        "function Vjr(e,t,n){let r={...VI(Hjr,{}),[t]:n};",
        "let rt=xr(nt),it=r?Je:rt.data,",
        "(r=(0,SD.jsx)(Q.Section,{sectionKey:`usage`,",
        "w5e=G(),T5e=Uu(m5e)})))()}var D5e,O5e,",
        "M5e=Yp(),E5e(),Ao(),A5e(),iv=G()})))()}function N5e({defaultWidth:e,",
        "U7e=Ur(),Y(),W7e=Ld(Q,e=>({",
        "W8e=Vo(Q,()=>up().homeModePreferences??ble({",
        "let e=Vo(kl,[]),t=Ld(kl,e=>null);return{entries$:Oa(kl,({",
        "w$.jsx)(Yge,{onSelect:()=>u?.(e),",
        "p$.jsx)(yp.Item,{leftIconAsset:lPe,onClick:r,",
        "n=kr(),r=Z(z$t),i;e[0]===t?i=e[1]:(i=e=>{",
    ),
    usage_status=(
        "async function vPr({additionalHeaders:e,signal:t}){try{return ePr(await IW.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":fW.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t}))}",
        "async function vPr({additionalHeaders:e,signal:t}){try{return ePr(await codexMuxFilterUsageStatus(await IW.safeGet(`/wham/usage`,{additionalHeaders:{\"OAI-App-Brand\":fW.toLowerCase(),\"x-openai-codex-pricing-chooser\":`1`,...e},signal:t})))}",
    ),
)

RENDERER_BUILDS = (RENDERER_BUILD_8881, RENDERER_BUILD_10492, RENDERER_BUILD_11645)

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
        "let y=v;if(g!=null){",
        "let y=window.__codexMuxSelectedUsageWindows??v;if(g!=null){",
        "the native usage-window selection",
    )
    renderer.replace(*build.usage_header, "the native Usage sheet header")
    for anchor, replacement in build.composer_actions:
        renderer.replace(anchor, replacement, "the native composer footer actions")
    for depleted_anchor in DEPLETED_ALERT_ANCHORS:
        renderer.replace(
            depleted_anchor,
            "defaultMessage:`All connected subscriptions are depleted`",
            "a native subscription depletion alert",
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


def remove_updater_initialization(bootstrap: str) -> str:
    """Drop the one updater `initialize()` the bootstrap awaits between
    importing the main process and running it, whether it stands as its own
    statement or leads a comma expression."""
    start = bootstrap.find("phase:`bootstrap-import-main`")
    end = bootstrap.find("runMainAppStartup:", start)
    if start < 0 or end < 0:
        raise RuntimeError("could not disable updates in the copied ChatGPT app")
    window = bootstrap[start:end]
    calls = list(re.finditer(r"await [A-Za-z_$][\w$]*\.initialize\(\)[;,]", window))
    if len(calls) != 1:
        raise RuntimeError("could not disable updates in the copied ChatGPT app")
    call = calls[0]
    return bootstrap[: start + call.start()] + bootstrap[start + call.end() :]


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
    bootstrap = remove_updater_initialization(bootstrap)
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
    discard_existing: bool = False,
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

        backup_suffix = time.strftime("%Y%m%d-%H%M%S")
        backup_directory = DEFAULT_STATE_ROOT / "backups" / backup_suffix
        app_backup = backup_directory / destination.name
        helper_backup = backup_directory / installed_computer_use_app.name
        had_app = destination.exists()
        had_helper = installed_computer_use_app.exists()
        if discard_existing:
            if had_app:
                shutil.rmtree(destination)
            if had_helper:
                shutil.rmtree(installed_computer_use_app)
            had_app = had_helper = False
        if had_app or had_helper:
            prune_backups(DEFAULT_STATE_ROOT / "backups", keep=0)
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
            args.discard_existing,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
