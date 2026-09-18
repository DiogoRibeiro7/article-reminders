import runpy
from collections.abc import Callable, Sequence
from typing import Any, cast

_module = runpy.run_path("scripts/sync_cran_portfolio_metadata.py")
parse_state = cast(Callable[[str], dict[str, str]], _module["parse_state"])
normalized_values = cast(Callable[[str], dict[str, str]], _module["normalized_values"])
VIEWS = cast(Sequence[Any], _module["VIEWS"])


def test_parse_state_stops_at_next_section() -> None:
    body = """## Portfolio state

- **CRAN target:** Yes, after rename
- **Maturity:** Release Candidate
- **Current status:** CRAN Hardening
- **Priority:** P0
- **Version:** 0.1.0
- **R CMD check:** 0 errors / 0 warnings / 0 notes
- **Next action:** Freeze the exact candidate.

## Audit notes

- **Priority:** should not be parsed
"""

    assert parse_state(body) == {
        "CRAN target": "Yes, after rename",
        "Maturity": "Release Candidate",
        "Current status": "CRAN Hardening",
        "Priority": "P0",
        "Version": "0.1.0",
        "R CMD check": "0 errors / 0 warnings / 0 notes",
        "Next action": "Freeze the exact candidate.",
    }


def test_normalized_values_handles_audit_variants() -> None:
    body = """## Portfolio state

- **CRAN target:** Yes, long-term
- **Maturity:** Release Candidate technically; demonstration package functionally
- **Current status:** Blocked
- **Priority:** p1
- **Version:** 0.2.0
- **R CMD check:** No successful hosted validation.
- **Next action:** Fix the estimator.
"""

    assert normalized_values(body) == {
        "CRAN": "Long-term",
        "Maturity": "Release Candidate",
        "Lifecycle": "Blocked",
        "Priority": "P1",
        "Version": "0.2.0",
        "R CMD Check": "No successful hosted validation.",
        "Next Action": "Fix the estimator.",
    }


def test_normalized_values_maps_no_target() -> None:
    body = """## Portfolio state

- **CRAN target:** No, not in current scope
- **Maturity:** Alpha
- **Current status:** Do Not Submit
- **Priority:** P3
"""
    assert normalized_values(body) == {
        "CRAN": "No",
        "Maturity": "Alpha",
        "Lifecycle": "Do Not Submit",
        "Priority": "P3",
    }


def test_view_names_are_unique() -> None:
    names = [view.name for view in VIEWS]
    assert len(names) == len(set(names))
    assert {
        "CRAN Pipeline",
        "Release Queue",
        "Blocked — Fix First",
        "P0 / P1",
        "Paused — Revive?",
        "Maturity Map",
        "CRAN Decisions",
    } == set(names)
