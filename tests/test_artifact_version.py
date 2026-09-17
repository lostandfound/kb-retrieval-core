from __future__ import annotations

import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

from kb_retrieval_core import __version__


def test_built_wheel_metadata_matches_runtime_version(tmp_path: Path) -> None:
    project = Path(__file__).parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(tmp_path),
            str(project),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    wheels = tuple(tmp_path.glob("kb_retrieval_core-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    assert metadata["Name"] == "kb-retrieval-core"
    assert metadata["Version"] == __version__
