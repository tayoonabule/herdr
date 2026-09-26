"""Windows SDK setup and cross-linting; macOS needs an SDK discoverable by xcrun."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SDK_ROOT = Path.home() / ".local/share/herdr/windows-cross"
LIBC_ENV = "LIBGHOSTTY_VT_WINDOWS_LIBC"
TARGET = "x86_64-pc-windows-msvc"


def libc_contents(root: Path) -> str:
    paths = {
        "include_dir": root / "sdk/include/ucrt",
        "sys_include_dir": root / "crt/include",
        "crt_dir": root / "sdk/lib/ucrt/x86_64",
        "msvc_lib_dir": root / "crt/lib/x86_64",
        "kernel32_lib_dir": root / "sdk/lib/um/x86_64",
    }
    required = {
        "include_dir": "stdlib.h",
        "sys_include_dir": "vcruntime.h",
        "crt_dir": "ucrt.lib",
        "msvc_lib_dir": "vcruntime.lib",
        "kernel32_lib_dir": "kernel32.lib",
    }
    for key, filename in required.items():
        if not (paths[key] / filename).is_file():
            raise ValueError(f"Windows SDK is incomplete: missing {paths[key] / filename}")
    return "".join(f"{key}={value}\n" for key, value in paths.items()) + "gcc_dir=\n"


def libc_path() -> Path:
    override = os.environ.get(LIBC_ENV)
    path = Path(override).expanduser() if override else SDK_ROOT / "libc.txt"
    if not path.is_file():
        raise ValueError(
            f"Windows cross-check needs SDK configuration at {path}.\n"
            "Run `just setup-windows-cross` once, or set "
            f"{LIBC_ENV} to an existing Zig libc configuration."
        )
    return path.resolve()


def ensure_macos_host_libsystem(config: Path) -> None:
    if sys.platform != "darwin":
        return
    # Zig 0.16 passes the build-wide Windows --libc config to native Zig
    # generators too. They still need macOS libSystem to link. Keep the Windows
    # headers and libraries intact, and expose only the host SDK library path
    # at the sysroot location Zig derives from this xwin installation.
    sdk = Path(subprocess.check_output(
        ["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True,
    ).strip())
    host_lib = sdk / "usr/lib"
    if not (host_lib / "libSystem.tbd").is_file():
        raise ValueError(f"macOS SDK is missing libSystem.tbd: {host_lib}")
    link = config.parent / "usr/lib"
    if link.is_symlink():
        if link.resolve() == host_lib.resolve():
            return
        raise ValueError(f"refusing to replace existing SDK symlink: {link}")
    if link.exists():
        raise ValueError(f"refusing to replace existing Windows SDK path: {link}")
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(host_lib, target_is_directory=True)
    except FileExistsError:
        # Concurrent worktrees share this SDK. Accept the same link if another
        # lint process created it first, but never replace an unknown path.
        if not link.is_symlink() or link.resolve() != host_lib.resolve():
            raise ValueError(f"refusing to replace existing Windows SDK path: {link}") from None


def setup(accept_license: bool) -> None:
    if not shutil.which("xwin"):
        raise ValueError("Install xwin first: cargo install xwin --locked")
    if not shutil.which(os.environ.get("ZIG", "zig")):
        raise ValueError("Install Zig 0.16.0 first, or set ZIG to its executable.")
    SDK_ROOT.mkdir(parents=True, exist_ok=True)
    # SDK downloads can be large; /tmp is often RAM-backed on Linux.
    temp_parent = "/var/tmp" if sys.platform.startswith("linux") else None
    with tempfile.TemporaryDirectory(prefix="herdr-windows-sdk-", dir=temp_parent) as cache:
        command = ["xwin", "--arch", "x86_64", "--cache-dir", cache]
        if accept_license:
            command.append("--accept-license")
        subprocess.run(command + ["splat", "--copy", "--output", str(SDK_ROOT)], check=True)
    config = SDK_ROOT / "libc.txt"
    config.write_text(libc_contents(SDK_ROOT))
    subprocess.run(
        [os.environ.get("ZIG", "zig"), "libc", "-target", "x86_64-windows-msvc", str(config)],
        check=True,
    )
    print(f"Windows SDK configured at {config}. Run `just windows-lint` or `just check`.")


def lint() -> None:
    config = libc_path()
    ensure_macos_host_libsystem(config)
    env = {**os.environ, LIBC_ENV: str(config), "LIBGHOSTTY_VT_SIMD": "false"}
    subprocess.run(["rustup", "target", "add", TARGET], check=True)
    subprocess.run(
        ["cargo", "clippy", "--bin", "herdr", "--locked", "--target", TARGET, "--", "-D", "warnings"],
        env=env,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup", help="Download Microsoft's SDK using xwin")
    setup_parser.add_argument(
        "--accept-license", action="store_true",
        help="Explicitly accept Microsoft's SDK license instead of xwin's interactive prompt",
    )
    commands.add_parser("lint", help="Run Windows clippy with the configured SDK")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup(args.accept_license)
        else:
            lint()
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        return error.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
