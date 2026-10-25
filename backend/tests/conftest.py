"""Shared pytest configuration: make the backend package importable and load gold fixtures."""
from __future__ import annotations

import json
import os
import sys

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

GOLD_DIR = os.path.join(BACKEND_ROOT, "data", "gold")


def load_gold(name: str) -> dict:
    with open(os.path.join(GOLD_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="session")
def gold():
    return load_gold


@pytest.fixture(autouse=True)
def temp_evidence_store(monkeypatch, tmp_path):
    """Keep test runs from writing into the real evidence log."""
    from app import config

    monkeypatch.setattr(config, "EVIDENCE_PATH", str(tmp_path / "evidence.jsonl"))
