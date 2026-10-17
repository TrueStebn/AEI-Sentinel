import csv
import io

import pytest

from src.config import Settings
from src.database import initialize_database
from src.data_loader import HEADERS


@pytest.fixture
def settings(tmp_path):
    value = Settings(database_path=tmp_path / "test.db", dataset_path=tmp_path)
    initialize_database(value)
    return value


def encode_rows(table, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS[table])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def decode_rows(bundle, table):
    return list(csv.DictReader(io.StringIO(bundle[f"{table}.csv"].decode("utf-8"))))


@pytest.fixture
def bundle():
    rows = {
        "wards": [dict(zip(HEADERS["wards"], ["ED", "Paivystys", "Emergency department", "out", "hospital"]))],
        "antibiotics": [dict(zip(HEADERS["antibiotics"], ["ampicillin", "AMP", "ampisilliini", "penicillins", "all"]))],
        "patients": [dict(zip(HEADERS["patients"], ["P1", "F", "1980", "Centre"]))],
        "cultures": [dict(zip(HEADERS["cultures"], ["C1", "P1", "45", "2026-09-27", "2026", "39", "ED", "Paivystys", "Emergency department", "out", "hospital", "", "urine", "virtsa", "positive", "1"]))],
        "isolates": [dict(zip(HEADERS["isolates"], ["I1", "C1", "1", "Escherichia coli", ">=10^5", "0", "0", "0", "0"]))],
        "susceptibility": [dict(zip(HEADERS["susceptibility"], ["I1", "C1", "Escherichia coli", "ampicillin", "AMP", "ampisilliini", "R", "EUCAST 2026"]))],
    }
    return {f"{table}.csv": encode_rows(table, values) for table, values in rows.items()}
