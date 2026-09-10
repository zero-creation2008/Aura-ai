"""
AURA - GitHub self-improvement pipeline.

Flow (matches the guardrail we agreed on — read this before changing it):

    propose change -> apply to workspace -> run tests
        -> FAIL: stop, log, do not touch git at all
        -> PASS: create branch -> commit -> push branch -> open PR

What this module will NEVER do, on purpose:
    - push directly to the base branch (config.GITHUB_BASE_BRANCH, default "main")
    - merge a pull request automatically
    - push a change whose tests did not pass

Merging stays a human action on GitHub itself. That boundary is not
configurable through this module's public functions.
"""
import subprocess
import time
import json

import requests

import config
from core import db


class GitHubError(Exception):
    pass


def _run_git(args: list, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
    )


def is_configured() -> bool:
    return bool(config.GITHUB_TOKEN and config.GITHUB_REPO)


def _api_headers():
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _slugify(title: str) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in title.lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:40] or "change"


def open_pull_request(title: str, body: str, head_branch: str) -> dict:
    """Opens a PR from head_branch into config.GITHUB_BASE_BRANCH. Assumes
    head_branch has already been pushed to the remote."""
    if not is_configured():
        raise GitHubError("GITHUB_TOKEN and GITHUB_REPO must be set in .env")

    url = f"{config.GITHUB_API}/repos/{config.GITHUB_REPO}/pulls"
    resp = requests.post(
        url,
        headers=_api_headers(),
        json={
            "title": title,
            "body": body,
            "head": head_branch,
            "base": config.GITHUB_BASE_BRANCH,
        },
        timeout=20,
    )
    if resp.status_code not in (200, 201):
        raise GitHubError(f"GitHub PR creation failed ({resp.status_code}): {resp.text[:500]}")
    data = resp.json()
    return {"pr_url": data.get("html_url"), "pr_number": data.get("number")}


def propose_and_push(repo_dir, title: str, description: str, changed_paths: list,
                      test_result: dict, task_id: int = None) -> dict:
    """
    Call this ONLY after the caller has already applied the file changes to
    repo_dir and run the test suite. test_result must be the dict produced
    by tools.test_runner.run_tests() (needs a "success" key).

    Never pushes on failing tests, and never touches the base branch.
    Returns the proposal record (dict) with status set to one of:
    'failed_tests', 'pushed', 'pr_open', 'error'.
    """
    branch = f"aura/{_slugify(title)}-{int(time.time())}"
    proposal_id = db.create_proposal(branch, title, description, changed_paths, task_id)

    if not test_result.get("success"):
        db.update_proposal(proposal_id, status="failed_tests",
                            error="Tests did not pass; change was not committed or pushed.")
        db.log_activity("self_improve_blocked", f"{title}: tests failed, not pushing", task_id)
        return db.get_proposal(proposal_id)

    if not is_configured():
        db.update_proposal(proposal_id, status="error",
                            error="GITHUB_TOKEN / GITHUB_REPO not configured in .env")
        db.log_activity("self_improve_error", "GitHub not configured", task_id)
        return db.get_proposal(proposal_id)

    try:
        # create + switch to a fresh branch off the current HEAD — never main directly
        r = _run_git(["checkout", "-b", branch], repo_dir)
        if r.returncode != 0:
            raise GitHubError(f"git checkout -b failed: {r.stderr}")

        r = _run_git(["add"] + changed_paths, repo_dir)
        if r.returncode != 0:
            raise GitHubError(f"git add failed: {r.stderr}")

        commit_msg = f"AURA self-improvement: {title}\n\n{description}\n\nTests: passed."
        r = _run_git(["commit", "-m", commit_msg], repo_dir)
        if r.returncode != 0:
            raise GitHubError(f"git commit failed: {r.stderr}")

        r = _run_git(["push", "-u", "origin", branch], repo_dir)
        if r.returncode != 0:
            raise GitHubError(f"git push failed: {r.stderr}")

        db.update_proposal(proposal_id, tests_passed=1, status="pushed")
        db.log_activity("self_improve_pushed", f"branch={branch}", task_id)

        pr = open_pull_request(
            title=f"[AURA] {title}",
            body=(
                f"{description}\n\n---\n"
                f"Automatically generated and pushed by AURA's self-improvement pipeline.\n"
                f"Tests passed locally before this branch was pushed.\n"
                f"**This PR requires manual review and merge — AURA does not merge its own PRs.**\n\n"
                f"Files changed: {', '.join(changed_paths)}"
            ),
            head_branch=branch,
        )
        db.update_proposal(proposal_id, status="pr_open", pr_url=pr["pr_url"])
        db.log_activity("self_improve_pr_opened", pr["pr_url"], task_id)

    except GitHubError as e:
        db.update_proposal(proposal_id, status="error", error=str(e))
        db.log_activity("self_improve_error", str(e), task_id)
    except Exception as e:
        db.update_proposal(proposal_id, status="error", error=f"unexpected error: {e}")
        db.log_activity("self_improve_error", str(e), task_id)

    return db.get_proposal(proposal_id)
