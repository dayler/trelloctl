"""M16 acceptance test: the wheel installs without extras in a clean environment (RNF-1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest
from tests.acceptance.m16_support import ROOT

from trelloctl import __version__


def run(args: list[str | Path], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(a) for a in args],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        **kwargs,  # type: ignore[call-overload]
    )


@pytest.mark.tc("TC-M16-003")
def test_the_wheel_installs_without_extras_in_a_clean_environment(tmp_path: Path) -> None:
    offline = {**os.environ, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    for name in ("PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PYTHONPATH", "PYTHONHOME"):
        offline.pop(name, None)

    # Build without build isolation (the current environment provides hatchling) and offline.
    out = tmp_path / "dist"
    built = run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", out],
        cwd=ROOT,
        env=offline,
    )
    assert built.returncode == 0, built.stderr + built.stdout
    (wheel,) = out.glob("trelloctl-*.whl")

    # The metadata declares no dependency outside the `mcp` extra.
    with zipfile.ZipFile(wheel) as archive:
        meta_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        metadata = archive.read(meta_name).decode("utf-8")
    requires = [ln for ln in metadata.splitlines() if ln.startswith("Requires-Dist:")]
    assert requires, "the mcp extra should be declared"
    assert all("extra == 'mcp'" in ln or 'extra == "mcp"' in ln for ln in requires), requires

    # Install it, without extras and without any index, in a fresh virtual environment.
    env_dir = tmp_path / "clean"
    venv.EnvBuilder(with_pip=True, clear=True, system_site_packages=False).create(env_dir)
    python = (
        env_dir
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    installed = run([python, "-m", "pip", "install", "--no-index", "--no-deps", wheel], env=offline)
    assert installed.returncode == 0, installed.stderr + installed.stdout
    resolved = run([python, "-m", "pip", "install", "--no-index", wheel], env=offline)
    assert resolved.returncode == 0, resolved.stderr + resolved.stdout  # no dependency to resolve

    listing = run([python, "-m", "pip", "list", "--format=json"], env=offline)
    names = {item["name"].lower() for item in json.loads(listing.stdout)}
    assert names - {"pip", "setuptools", "wheel"} == {"trelloctl"}

    version = run([python, "-I", "-m", "trelloctl", "--version"], env=offline)
    assert version.returncode == 0, version.stderr
    assert __version__ in version.stdout
