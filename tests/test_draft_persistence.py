# tests/test_draft_persistence.py
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from scheduler.draft_persistence import (
    discard_draft,
    has_draft,
    load_draft_or_published,
    publish_draft,
    save_draft,
)


@pytest.fixture
def config_dir(tmp_path):
    annual = tmp_path / "annual"
    annual.mkdir()
    (annual / "test.yaml").write_text("rules: []\nshifts: [A, B]\n")
    return annual


def test_load_published_when_no_draft(config_dir):
    data = load_draft_or_published(config_dir, "test.yaml")
    assert data["shifts"] == ["A", "B"]


def test_save_and_load_draft(config_dir):
    save_draft(config_dir, "test.yaml", {"rules": [], "shifts": ["X"]})
    data = load_draft_or_published(config_dir, "test.yaml")
    assert data["shifts"] == ["X"]


def test_has_draft(config_dir):
    assert not has_draft(config_dir, "test.yaml")
    save_draft(config_dir, "test.yaml", {"rules": []})
    assert has_draft(config_dir, "test.yaml")


def test_discard_draft(config_dir):
    save_draft(config_dir, "test.yaml", {"rules": []})
    discard_draft(config_dir, "test.yaml")
    assert not has_draft(config_dir, "test.yaml")
    # Falls back to published
    data = load_draft_or_published(config_dir, "test.yaml")
    assert data["shifts"] == ["A", "B"]


def test_publish_draft(config_dir):
    save_draft(config_dir, "test.yaml", {"rules": [], "shifts": ["NEW"]})
    publish_draft(config_dir, "test.yaml")
    assert not has_draft(config_dir, "test.yaml")
    # Published now has the draft content
    data = load_draft_or_published(config_dir, "test.yaml")
    assert data["shifts"] == ["NEW"]


def test_publish_no_draft_raises(config_dir):
    with pytest.raises(FileNotFoundError):
        publish_draft(config_dir, "test.yaml")


def test_load_missing_file_raises(config_dir):
    with pytest.raises(FileNotFoundError):
        load_draft_or_published(config_dir, "nonexistent.yaml")
