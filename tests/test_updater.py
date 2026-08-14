from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from service_dependency_mapper.updater import (
    PACKAGE_SOURCE,
    UpdateError,
    fetch_update_info,
    find_source_checkout,
    install_update,
    installer_download_url,
    is_newer_version,
    version_key,
)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload


class UpdateCheckTests(unittest.TestCase):
    def test_compares_semantic_versions_numerically(self):
        self.assertTrue(is_newer_version("1.10.0", "1.9.9"))
        self.assertFalse(is_newer_version("1.2.0", "1.2.0"))
        self.assertGreater(version_key("v2.0.0"), version_key("1.99.99"))

    def test_stable_version_is_newer_than_matching_prerelease(self):
        self.assertTrue(is_newer_version("1.3.0", "1.3.0-rc1"))

    def test_distinguishes_matching_and_stale_update_feeds(self):
        matching = fetch_update_info(
            "1.4.0",
            opener=lambda _request, *, timeout: FakeResponse(b'{"version":"1.4.0"}'),
            cache_token="matching",
        )
        stale = fetch_update_info(
            "1.5.0",
            opener=lambda _request, *, timeout: FakeResponse(b'{"version":"1.4.0"}'),
            cache_token="stale",
        )

        self.assertTrue(matching.versions_match)
        self.assertFalse(matching.current_is_newer)
        self.assertFalse(stale.versions_match)
        self.assertTrue(stale.current_is_newer)

    def test_fetches_available_update(self):
        payload = json.dumps(
            {
                "version": "1.3.0",
                "summary": "Automatic update checks.",
            }
        ).encode()
        captured = {}

        def opener(request, *, timeout):
            captured["user_agent"] = request.headers["User-agent"]
            captured["cache_control"] = request.headers["Cache-control"]
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return FakeResponse(payload)

        info = fetch_update_info(
            "1.2.0",
            timeout=2.5,
            opener=opener,
            cache_token="fresh-check",
        )

        self.assertTrue(info.update_available)
        self.assertEqual(info.latest_version, "1.3.0")
        self.assertEqual(info.summary, "Automatic update checks.")
        self.assertEqual(captured["timeout"], 2.5)
        self.assertIn("1.2.0", captured["user_agent"])
        self.assertIn("_sdmap_cache=fresh-check", captured["url"])
        self.assertEqual(captured["cache_control"], "no-cache, no-store")

    def test_builds_official_installer_download_url(self):
        self.assertEqual(
            installer_download_url("v1.6.0"),
            (
                "https://github.com/FgSousace/Service-Dependency-Mapper/"
                "releases/download/v1.6.0/"
                "Service-Dependency-Mapper-Setup-1.6.0.exe"
            ),
        )

    def test_reads_installer_url_from_manifest(self):
        url = (
            "https://github.com/FgSousace/Service-Dependency-Mapper/"
            "releases/download/v1.6.0/"
            "Service-Dependency-Mapper-Setup-1.6.0.exe"
        )
        payload = json.dumps({"version": "1.6.0", "installer_url": url}).encode()
        info = fetch_update_info(
            "1.5.0",
            opener=lambda _request, *, timeout: FakeResponse(payload),
        )
        self.assertEqual(info.installer_url, url)

    def test_rejects_invalid_manifest(self):
        def opener(_request, *, timeout):
            self.assertEqual(timeout, 4.0)
            return FakeResponse(b'{"summary": "missing version"}')

        with self.assertRaisesRegex(UpdateError, "does not contain a version"):
            fetch_update_info("1.2.0", opener=opener)


class UpdateInstallTests(unittest.TestCase):
    def test_finds_source_checkout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".git").mkdir()
            (root / "pyproject.toml").write_text("", encoding="utf-8")
            nested = root / "src" / "package"
            nested.mkdir(parents=True)

            self.assertEqual(find_source_checkout(nested), root.resolve())

    def test_updates_clean_main_checkout_and_reinstalls_editable_package(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            responses = iter(
                (
                    (0, "main\n", ""),
                    (0, "", ""),
                    (
                        0,
                        "https://github.com/FgSousace/Service-Dependency-Mapper.git\n",
                        "",
                    ),
                    (0, "Fast-forward\n", ""),
                    (0, "Successfully installed\n", ""),
                )
            )
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                returncode, stdout, stderr = next(responses)
                return subprocess.CompletedProcess(
                    command,
                    returncode,
                    stdout,
                    stderr,
                )

            result = install_update(
                project_root=root,
                runner=runner,
                python_executable="python-test",
            )

        self.assertEqual(result.method, "source checkout")
        self.assertEqual(
            commands[3],
            ["git", "pull", "--ff-only", "origin", "main"],
        )
        self.assertEqual(
            commands[4][0:5], ["python-test", "-m", "pip", "install", "--upgrade"]
        )
        self.assertIn("-e", commands[4])

    def test_refuses_to_overwrite_local_changes(self):
        responses = iter(
            (
                (0, "main\n", ""),
                (0, " M src/example.py\n", ""),
            )
        )

        def runner(command, **_kwargs):
            returncode, stdout, stderr = next(responses)
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        with self.assertRaisesRegex(UpdateError, "local changes"):
            install_update(project_root=".", runner=runner)

    @patch("service_dependency_mapper.updater.subprocess.Popen")
    @patch("service_dependency_mapper.updater._download_installer")
    def test_frozen_install_launches_windows_setup(self, download, popen):
        installer = Path("Service-Dependency-Mapper-Setup-1.6.0.exe")
        download.return_value = installer

        result = install_update(
            latest_version="1.6.0",
            installer_url=(
                "https://github.com/FgSousace/Service-Dependency-Mapper/"
                "releases/download/v1.6.0/"
                "Service-Dependency-Mapper-Setup-1.6.0.exe"
            ),
            frozen=True,
        )

        self.assertEqual(result.method, "Windows installer")
        download.assert_called_once()
        popen.assert_called_once_with(
            [str(installer), "/SP-", "/CLOSEAPPLICATIONS"],
            cwd=str(installer.parent),
        )

    def test_non_checkout_install_uses_current_python_environment(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "Installed\n", "")

        with patch(
            "service_dependency_mapper.updater.find_source_checkout",
            return_value=None,
        ):
            result = install_update(
                runner=runner,
                python_executable="python-test",
            )

        self.assertEqual(result.method, "package installation")
        self.assertEqual(
            commands[0][0:5], ["python-test", "-m", "pip", "install", "--upgrade"]
        )
        self.assertEqual(commands[0][-1], PACKAGE_SOURCE)


if __name__ == "__main__":
    unittest.main()
