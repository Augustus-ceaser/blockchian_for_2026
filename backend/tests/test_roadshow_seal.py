from __future__ import annotations

import hashlib
import json

from app.main import create_app
from app.core.config import Settings
from app.tools.generate_roadshow_state import _stable_bytes


def test_roadshow_seal_exposes_only_a_read_method() -> None:
    app = create_app(Settings(app_env="test"))
    operations = app.openapi()["paths"]["/api/v1/roadshow-seal/overview"]
    assert set(operations) == {"get"}


def test_manifest_digest_input_is_stable() -> None:
    first = {"z": 1, "name": "MedTrust Space", "nested": {"b": False, "a": 2}}
    second = {"nested": {"a": 2, "b": False}, "name": "MedTrust Space", "z": 1}
    assert _stable_bytes(first) == _stable_bytes(second)
    assert hashlib.sha256(_stable_bytes(first)).hexdigest() == hashlib.sha256(
        _stable_bytes(json.loads(json.dumps(second)))
    ).hexdigest()
