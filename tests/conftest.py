from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from bcb.spec import Spec, load_spec

REPO = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO / "trajectories" / "vaping-v0" / "spec.yaml"


@pytest.fixture(scope="session")
def spec() -> Spec:
    return load_spec(SPEC_PATH)


def smoke_config(tmp: Path, **overrides) -> Path:
    """A mock-only run config whose outputs all land under `tmp`."""
    raw = yaml.safe_load((REPO / "configs" / "run.smoke.yaml").read_text())
    raw.update({
        "spec": str(SPEC_PATH),
        "conversation": str(tmp / "conversation.draft.yaml"),
        "examples": str(tmp / "examples.yaml"),
        "prompts": {k: str(REPO / v) for k, v in raw["prompts"].items()},
        "output_dir": str(tmp / "runs"),
    })
    raw.update(overrides)
    path = tmp / "run.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


@pytest.fixture()
def drafted(tmp_path: Path) -> Path:
    """A tmp dir holding a mock-drafted conversation and its config."""
    from bcb.cli import main

    cfg = smoke_config(tmp_path)
    assert main(["draft", "--config", str(cfg)]) == 0
    return tmp_path


def copy_repo_file(rel: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / rel, dest)
    return dest
