from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> int:
    """Run only the repository-owned PostgreSQL synthetic execution self-test."""

    if not os.getenv("MEDTRUST_TEST_DATABASE_URL"):
        print("MEDTRUST_TEST_DATABASE_URL must point to a disposable migrated PostgreSQL database")
        return 2
    import pytest

    test_file = (
        Path(__file__).resolve().parents[2]
        / "tests"
        / "integration"
        / "test_execution_callback_processor_postgresql.py"
    )
    return pytest.main(
        [
            str(test_file),
            "-q",
            "-k",
            "local_builtin_executor_synthetic_self_test",
        ]
    )


if __name__ == "__main__":
    sys.exit(main())
