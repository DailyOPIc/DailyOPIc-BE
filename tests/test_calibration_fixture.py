import json
from pathlib import Path

from app.models.api import OPIcLevel


def test_calibration_dataset_has_thirty_unique_levelled_answers() -> None:
    path = Path(__file__).parent / "fixtures" / "calibration_answers.json"
    answers = json.loads(path.read_text(encoding="utf-8"))

    assert len(answers) >= 30
    assert len({item["id"] for item in answers}) == len(answers)
    assert {item["calibratedLevel"] for item in answers} == {
        level.value for level in OPIcLevel
    }
    assert all(len(item["transcript"].split()) >= 5 for item in answers)
