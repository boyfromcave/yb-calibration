"""docs/parameters.md must equal the registry rendering byte for byte."""

from __future__ import annotations

from ybcal.params.doc import render_markdown

from .conftest import REPO_ROOT


def test_parameters_md_is_current():
    committed = (REPO_ROOT / "docs" / "parameters.md").read_text()
    assert committed == render_markdown(), "run `make docs` (ybcal params doc) and commit the result"
