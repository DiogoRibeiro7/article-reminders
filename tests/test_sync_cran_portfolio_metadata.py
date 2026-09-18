import runpy
from collections.abc import Callable, Sequence
from typing import Any, cast

_module = runpy.run_path("scripts/sync_cran_portfolio_metadata.py")
parse_state = cast(Callable[[str], dict[str, str]], _module["parse_state"])
normalized_values = cast(Callable[[str], dict[str, str]], _module["normalized_values"])
VIEWS = cast(Sequence[Any], _module["VIEWS"])
audit_integrity_errors = cast(
    Callable[[dict[str, Any]], list[str]],
    _module["audit_integrity_errors"],
)
validate_audit_integrity = cast(
    Callable[[list[dict[str, Any]]], None],
    _module["validate_audit_integrity"],
)


def test_parse_state_stops_at_next_section() -> None:
    body = """## Portfolio state

- **CRAN target:** Yes, after rename
- **Maturity:** Release Candidate
- **Current status:** CRAN Hardening
- **Priority:** P0
- **Primary blocker:** Metadata / Release
- **Last reviewed:** 2026-09-18
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
        "Primary blocker": "Metadata / Release",
        "Last reviewed": "2026-09-18",
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
- **Primary blocker:** Methodology
- **Last reviewed:** 2026-09-18
- **Version:** 0.2.0
- **R CMD check:** No successful hosted validation.
- **Next action:** Fix the estimator.
"""

    assert normalized_values(body) == {
        "CRAN": "Long-term",
        "Maturity": "Release Candidate",
        "Lifecycle": "Blocked",
        "Priority": "P1",
        "Primary Blocker": "Methodology",
        "Last Reviewed": "2026-09-18",
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
- **Primary blocker:** Naming / Scope
- **Last reviewed:** 2026-09-18
"""
    assert normalized_values(body) == {
        "CRAN": "No",
        "Maturity": "Alpha",
        "Lifecycle": "Do Not Submit",
        "Priority": "P3",
        "Primary Blocker": "Naming / Scope",
        "Last Reviewed": "2026-09-18",
    }


def test_view_names_are_unique() -> None:
    names = [view.name for view in VIEWS]
    assert len(names) == len(set(names))
    assert {
        "Portfolio Summary",
        "CRAN Pipeline",
        "Release Queue",
        "Blocked — Fix First",
        "P0 / P1",
        "Paused — Revive?",
        "Maturity Map",
        "CRAN Decisions",
    } == set(names)


def test_audit_integrity_accepts_complete_tracker() -> None:
    issue = {
        "title": "[CRAN] example",
        "url": "https://example.invalid/issue/1",
        "body": """## Portfolio state

- **CRAN target:** Yes
- **Maturity:** Beta
- **Current status:** Blocked
- **Priority:** P1
- **Primary blocker:** Methodology
- **Last reviewed:** 2026-09-18
- **Version:** 0.1.0
- **R CMD check:** No successful hosted validation.
- **Next action:** Fix the blocker.
""",
    }
    assert audit_integrity_errors(issue) == []
    validate_audit_integrity([issue])


def test_audit_integrity_rejects_inventory_template() -> None:
    issue = {
        "title": "[CRAN] stale",
        "url": "https://example.invalid/issue/2",
        "body": """## Portfolio state
- **CRAN target:** To be reviewed
- **Maturity:** To be reviewed
- **Current status:** Inventory
- **Priority:** To be assigned
- **Primary blocker:** Dormant / Revive
- **Next action:** Review current package and release state
""",
    }

    errors = audit_integrity_errors(issue)

    assert any("missing Portfolio state keys" in error for error in errors)
    assert any("inventory placeholder" in error for error in errors)
    assert any("does not normalize all Project fields" in error for error in errors)


def test_audit_integrity_fails_before_partial_sync() -> None:
    good = {
        "title": "[CRAN] good",
        "url": "https://example.invalid/issue/3",
        "body": """## Portfolio state
- **CRAN target:** Yes
- **Maturity:** Alpha
- **Current status:** Paused
- **Priority:** P3
- **Primary blocker:** Dormant / Revive
- **Last reviewed:** 2026-09-18
- **Version:** 0.0.0.9000
- **R CMD check:** Not yet run.
- **Next action:** Decide whether to revive.
""",
    }
    bad = {
        "title": "[CRAN] bad",
        "url": "https://example.invalid/issue/4",
        "body": """## Portfolio state
- **CRAN target:** Yes
- **Maturity:** Beta
- **Current status:** Blocked
- **Priority:** P1
""",
    }

    try:
        validate_audit_integrity([good, bad])
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected incomplete audit metadata to fail")

    assert "[CRAN] bad" in message
    assert "Version" in message
    assert "R CMD check" in message
    assert "Next action" in message


def test_normalized_values_rejects_unknown_primary_blocker() -> None:
    body = """## Portfolio state

- **CRAN target:** Yes
- **Maturity:** Beta
- **Current status:** Blocked
- **Priority:** P1
- **Primary blocker:** Something Else
- **Version:** 0.1.0
- **R CMD check:** Not green.
- **Next action:** Fix it.
"""
    values = normalized_values(body)
    assert "Primary Blocker" not in values


def test_normalized_values_rejects_invalid_review_date() -> None:
    body = """## Portfolio state

- **CRAN target:** Yes
- **Maturity:** Beta
- **Current status:** Blocked
- **Priority:** P1
- **Primary blocker:** Methodology
- **Last reviewed:** 18/09/2026
- **Version:** 0.1.0
- **R CMD check:** Not green.
- **Next action:** Fix it.
"""
    values = normalized_values(body)
    assert "Last Reviewed" not in values
