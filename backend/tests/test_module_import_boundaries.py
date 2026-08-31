from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def test_marketplace_models_import_before_commerce_and_compute_services() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(backend_root)
        if not existing_pythonpath
        else f"{backend_root}{os.pathsep}{existing_pythonpath}"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app.modules.marketplace.models; "
                "import app.modules.commerce.gating; "
                "import app.modules.compute.services"
            ),
        ],
        cwd=backend_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
