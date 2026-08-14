"""Background update checks and safe in-place installation helpers."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_URL = "https://github.com/FgSousace/Service-Dependency-Mapper"
REPOSITORY_SLUG = "FgSousace/Service-Dependency-Mapper"
DEFAULT_BRANCH = "main"
UPDATE_MANIFEST_URL = (
    f"https://raw.githubusercontent.com/{REPOSITORY_SLUG}/{DEFAULT_BRANCH}/update.json"
)
PACKAGE_SOURCE = f"git+{REPOSITORY_URL}.git@{DEFAULT_BRANCH}"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 256 * 1024 * 1024
INSTALLER_TIMEOUT = 60.0

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class UpdateError(RuntimeError):
    """Raised when checking for or installing an update fails safely."""


@dataclass(frozen=True)
class UpdateInfo:
    """Result of comparing the installed version with the update manifest."""

    current_version: str
    latest_version: str
    update_available: bool
    summary: str
    repository_url: str = REPOSITORY_URL
    installer_url: str | None = None

    @property
    def versions_match(self) -> bool:
        """Return whether the installed version matches the update feed."""

        return version_key(self.current_version) == version_key(self.latest_version)

    @property
    def current_is_newer(self) -> bool:
        """Return whether the update feed is older than the installed version."""

        return version_key(self.current_version) > version_key(self.latest_version)


@dataclass(frozen=True)
class UpdateResult:
    """Description of a successfully installed update."""

    method: str
    output: str


def version_key(value: str) -> tuple[int, int, int, int]:
    """Return a comparison key for the stable semantic versions used here."""

    match = re.fullmatch(
        r"[vV]?(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z][0-9A-Za-z.-]*))?"
        r"(?:\+[0-9A-Za-z.-]+)?",
        value.strip(),
    )
    if match is None:
        raise ValueError(f"Invalid semantic version: {value!r}")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    stable = 1 if match.group(4) is None else 0
    return major, minor, patch, stable


def is_newer_version(candidate: str, current: str) -> bool:
    """Return whether ``candidate`` is newer than ``current``."""

    return version_key(candidate) > version_key(current)


def _cache_busted_url(url: str, token: str) -> str:
    separator = "&" if urllib.parse.urlsplit(url).query else "?"
    return f"{url}{separator}_sdmap_cache={urllib.parse.quote(token, safe='')}"


def fetch_update_info(
    current_version: str,
    *,
    timeout: float = 4.0,
    manifest_url: str = UPDATE_MANIFEST_URL,
    opener: Callable[..., Any] | None = None,
    cache_token: str | None = None,
) -> UpdateInfo:
    """Fetch the small update manifest and compare it with the local version."""

    open_url = opener or urllib.request.urlopen
    request_url = _cache_busted_url(
        manifest_url,
        cache_token or str(time.time_ns()),
    )
    request = urllib.request.Request(
        request_url,
        headers={
            "Accept": "application/json",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
            "User-Agent": f"Service-Dependency-Mapper/{current_version}",
        },
    )
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise UpdateError("The update manifest is unexpectedly large.")
        document = json.loads(raw.decode("utf-8"))
    except UpdateError:
        raise
    except (
        json.JSONDecodeError,
        OSError,
        TimeoutError,
        UnicodeDecodeError,
        urllib.error.URLError,
    ) as exc:
        raise UpdateError(f"Could not check for updates: {exc}") from exc

    if not isinstance(document, dict):
        raise UpdateError("The update manifest has an invalid format.")
    latest_version = document.get("version")
    summary = document.get("summary", "")
    if not isinstance(latest_version, str):
        raise UpdateError("The update manifest does not contain a version.")
    if not isinstance(summary, str):
        raise UpdateError("The update manifest contains an invalid summary.")
    installer_url = document.get("installer_url")
    if installer_url is not None and not isinstance(installer_url, str):
        raise UpdateError("The update manifest contains an invalid installer URL.")
    try:
        update_available = is_newer_version(latest_version, current_version)
    except ValueError as exc:
        raise UpdateError(str(exc)) from exc

    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_version.strip().lstrip("vV"),
        update_available=update_available,
        summary=summary.strip()[:500],
        installer_url=installer_url.strip() if installer_url else None,
    )


def find_source_checkout(start: str | Path | None = None) -> Path | None:
    """Find a Git checkout containing the running editable installation."""

    candidate = Path(start) if start is not None else Path(__file__)
    candidate = candidate.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / "pyproject.toml").is_file() and (directory / ".git").exists():
            return directory
    return None


def _run_command(
    command: Sequence[str],
    *,
    cwd: Path | None,
    runner: CommandRunner,
    timeout: float = 180.0,
) -> str:
    try:
        completed = runner(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"Could not run {command[0]!r}: {exc}") from exc

    stdout = _decode_process_output(completed.stdout)
    stderr = _decode_process_output(completed.stderr)
    output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    if completed.returncode != 0:
        detail = output[-1600:] or f"exit code {completed.returncode}"
        raise UpdateError(f"Update command failed: {detail}")
    return output


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _normalize_remote(remote: str) -> str:
    return remote.strip().lower().replace("\\", "/").replace(":", "/")


def _update_source_checkout(
    root: Path,
    *,
    runner: CommandRunner,
    python_executable: str,
) -> UpdateResult:
    branch = _run_command(
        ("git", "branch", "--show-current"),
        cwd=root,
        runner=runner,
    ).strip()
    if branch != DEFAULT_BRANCH:
        raise UpdateError(
            f"Automatic update requires the {DEFAULT_BRANCH!r} branch; "
            f"the checkout is currently on {branch or 'a detached commit'}."
        )

    status = _run_command(
        ("git", "status", "--porcelain"),
        cwd=root,
        runner=runner,
    )
    if status.strip():
        raise UpdateError(
            "The project contains local changes. Commit or stash them before "
            "using the automatic updater."
        )

    remote = _run_command(
        ("git", "remote", "get-url", "origin"),
        cwd=root,
        runner=runner,
    )
    expected_slug = REPOSITORY_SLUG.lower()
    if expected_slug not in _normalize_remote(remote):
        raise UpdateError(
            "The origin remote does not point to the official project repository."
        )

    pull_output = _run_command(
        ("git", "pull", "--ff-only", "origin", DEFAULT_BRANCH),
        cwd=root,
        runner=runner,
    )
    install_output = _run_command(
        (
            python_executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "-e",
            str(root),
        ),
        cwd=root,
        runner=runner,
    )
    return UpdateResult(
        method="source checkout",
        output="\n".join(part for part in (pull_output, install_output) if part),
    )


def installer_download_url(version: str) -> str:
    """Return the official release asset URL for an installer version."""

    normalized_version = version.strip().lstrip("vV")
    version_key(normalized_version)
    filename = f"Service-Dependency-Mapper-Setup-{normalized_version}.exe"
    return f"{REPOSITORY_URL}/releases/download/v{normalized_version}/{filename}"


def _validate_installer_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    expected_prefix = f"/{REPOSITORY_SLUG}/releases/download/"
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or not parsed.path.startswith(expected_prefix)
        or not parsed.path.lower().endswith(".exe")
    ):
        raise UpdateError(
            "The update manifest does not point to an official "
            "GitHub release installer."
        )


def _download_installer(
    version: str,
    installer_url: str | None,
    *,
    opener: Callable[..., Any] | None = None,
) -> Path:
    """Download an official Windows installer with a strict size limit."""

    url = installer_url or installer_download_url(version)
    _validate_installer_url(url)
    normalized_version = version.strip().lstrip("vV")
    destination = Path(tempfile.gettempdir()) / (
        f"Service-Dependency-Mapper-Setup-{normalized_version}.exe"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "Cache-Control": "no-cache",
            "User-Agent": f"Service-Dependency-Mapper/{normalized_version}",
        },
    )
    open_url = opener or urllib.request.urlopen
    total = 0
    try:
        with (
            open_url(request, timeout=INSTALLER_TIMEOUT) as response,
            destination.open("wb") as output,
        ):
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_INSTALLER_BYTES:
                    raise UpdateError("The downloaded installer is unexpectedly large.")
                output.write(chunk)
    except UpdateError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        destination.unlink(missing_ok=True)
        raise UpdateError(f"Could not download the Windows installer: {exc}") from exc
    if total == 0:
        destination.unlink(missing_ok=True)
        raise UpdateError("The downloaded Windows installer is empty.")
    return destination


def _launch_windows_installer(
    version: str,
    installer_url: str | None,
) -> UpdateResult:
    installer = _download_installer(version, installer_url)
    try:
        subprocess.Popen(
            [str(installer), "/SP-", "/CLOSEAPPLICATIONS"],
            cwd=str(installer.parent),
        )
    except OSError as exc:
        raise UpdateError(f"Could not launch the Windows installer: {exc}") from exc
    return UpdateResult(
        method="Windows installer",
        output=f"Launched {installer}",
    )


def install_update(
    *,
    project_root: str | Path | None = None,
    runner: CommandRunner | None = None,
    python_executable: str | None = None,
    latest_version: str | None = None,
    installer_url: str | None = None,
    frozen: bool | None = None,
) -> UpdateResult:
    """Install an update using the method appropriate for this distribution."""

    frozen_installation = (
        bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    )
    if frozen_installation:
        if latest_version is None:
            raise UpdateError("The Windows installer version is missing.")
        return _launch_windows_installer(latest_version, installer_url)

    command_runner = runner or subprocess.run
    executable = python_executable or sys.executable
    root = (
        Path(project_root).expanduser().resolve()
        if project_root is not None
        else find_source_checkout()
    )
    if root is not None:
        return _update_source_checkout(
            root,
            runner=command_runner,
            python_executable=executable,
        )

    output = _run_command(
        (
            executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            PACKAGE_SOURCE,
        ),
        cwd=None,
        runner=command_runner,
    )
    return UpdateResult(method="package installation", output=output)
