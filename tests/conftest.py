import json
import shutil
from pathlib import Path

import pytest

from retail_intel import db
from retail_intel.pipeline import run_pipeline
from retail_intel.synth.generate import generate


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    """Generate the messy data once, run the whole pipeline, hand back connection + ground truth."""
    root = tmp_path_factory.mktemp("world")
    src, truth_dir, db_path = root / "source", root / "truth", root / "w.db"
    generate(src, truth_dir)
    result = run_pipeline(src, db_path, overrides_path=None)
    assert result["status"] == "ok", result.get("blockers")
    conn = db.connect(db_path)
    truth = json.loads((truth_dir / "ground_truth.json").read_text())
    yield dict(conn=conn, truth=truth, result=result, src=src, truth_dir=truth_dir, db_path=db_path, root=root)
    conn.close()


@pytest.fixture()
def fresh_copy(world, tmp_path):
    """A private copy of the source files that a test may damage."""
    dst = tmp_path / "source"
    shutil.copytree(world["src"], dst)
    return dst
