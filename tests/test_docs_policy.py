"""docs/policy.md must document every policy key (WP-10)."""

from __future__ import annotations

import dataclasses
import re

from ybcal.config import Policy

from .conftest import REPO_ROOT


def _documented_keys() -> set[str]:
    """Keys that head a row of one of the policy tables: ``| `key` | default | …``."""
    text = (REPO_ROOT / "docs" / "policy.md").read_text()
    return set(re.findall(r"^\| `([a-z0-9_]+)` \|", text, flags=re.MULTILINE))


def test_every_policy_field_is_documented():
    fields = {f.name for f in dataclasses.fields(Policy)}
    missing = sorted(fields - _documented_keys())
    assert not missing, f"docs/policy.md has no table row for: {', '.join(missing)}"


def test_policy_doc_has_no_unknown_keys():
    fields = {f.name for f in dataclasses.fields(Policy)}
    unknown = sorted(_documented_keys() - fields)
    assert not unknown, f"docs/policy.md documents keys that are not Policy fields: {', '.join(unknown)}"
