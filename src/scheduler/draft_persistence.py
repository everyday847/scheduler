# src/scheduler/draft_persistence.py
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def get_drafts_dir(config_dir: Path) -> Path:
    drafts = config_dir / ".drafts"
    drafts.mkdir(exist_ok=True)
    return drafts


def draft_path_for(config_dir: Path, filename: str) -> Path:
    return get_drafts_dir(config_dir) / f"{Path(filename).stem}.draft.yaml"


def load_draft_or_published(config_dir: Path, filename: str) -> dict[str, Any]:
    draft = draft_path_for(config_dir, filename)
    if draft.exists():
        data = yaml.safe_load(draft.read_text())
    else:
        published = config_dir / filename
        if not published.exists():
            raise FileNotFoundError(f"Config not found: {filename}")
        data = yaml.safe_load(published.read_text())
    return data


def save_draft(config_dir: Path, filename: str, data: dict[str, Any]) -> Path:
    draft = draft_path_for(config_dir, filename)
    draft.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return draft


def publish_draft(config_dir: Path, filename: str) -> None:
    draft = draft_path_for(config_dir, filename)
    published = config_dir / filename
    if not draft.exists():
        raise FileNotFoundError(f"No draft to publish for {filename}")
    published.write_text(draft.read_text())
    draft.unlink()


def discard_draft(config_dir: Path, filename: str) -> None:
    draft = draft_path_for(config_dir, filename)
    if draft.exists():
        draft.unlink()


def has_draft(config_dir: Path, filename: str) -> bool:
    return draft_path_for(config_dir, filename).exists()
