import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture():
    def load(name: str):
        path = FIXTURES / name
        with open(path, encoding="utf-8") as f:
            return json.load(f) if path.suffix == ".json" else f.read()

    return load
