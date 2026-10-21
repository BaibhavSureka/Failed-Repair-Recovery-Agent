"""Isolated demo API fixtures; no external services or production database."""

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app  # noqa: E402


@pytest.fixture
def demo_clock():
    """A mutable clock keeps due-date checks deterministic without sleeping."""
    return [datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)]


@pytest.fixture
def application(tmp_path, demo_clock):
    return create_app(
        db_path=str(tmp_path / "recovery-test.sqlite3"),
        demo_date="2026-10-21",
        clock=lambda: demo_clock[0],
    )


@pytest.fixture
def client(application):
    with TestClient(application) as test_client:
        yield test_client
