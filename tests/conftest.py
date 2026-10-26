import json
from pathlib import Path

import pytest

from finguard.scenarios import load_transfer_scenario

SCENARIO_PATH = Path(__file__).parent / "scenarios" / "transfer-basic.json"


@pytest.fixture
def scenario():
    return load_transfer_scenario(SCENARIO_PATH)


@pytest.fixture
def scenario_data():
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
