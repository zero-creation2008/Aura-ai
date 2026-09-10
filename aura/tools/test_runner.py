"""
AURA - test runner.
No Docker sandbox available in Termux, so this runs commands as a
subprocess with a timeout and working-directory confined to the
workspace. This is NOT full isolation — it relies on the safety module's
path/action boundaries elsewhere, not on OS-level sandboxing.
"""
import subprocess

import config

# Only these command prefixes are ever allowed to run automatically.
# Anything else is treated as "run_shell" (a DANGEROUS_ACTION) and needs
# explicit approval.
SAFE_TEST_COMMANDS = [
    ["python3", "-m", "pytest"],
    ["python3", "-m", "unittest"],
    ["npm", "test"],
    ["npm", "run", "build"],
    ["node", "--check"],
]


def detect_project_type() -> str:
    files = {p.name for p in config.WORKSPACE_DIR.iterdir()} if config.WORKSPACE_DIR.exists() else set()
    if "package.json" in files:
        return "node"
    if any(f.endswith(".py") for f in files) or "requirements.txt" in files:
        return "python"
    return "unknown"


def run_command(cmd: list, timeout: int = 60) -> dict:
    """
    Runs a whitelisted command inside the workspace dir. Raises ValueError
    if the command isn't in SAFE_TEST_COMMANDS (caller should route
    anything else through the approval flow as 'run_shell').
    """
    if not any(cmd[:len(safe)] == safe for safe in SAFE_TEST_COMMANDS):
        raise ValueError(
            f"Command {cmd} is not pre-approved for auto-run. "
            f"Route it through the approval flow as a 'run_shell' action."
        )
    try:
        result = subprocess.run(
            cmd,
            cwd=str(config.WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "cmd": cmd,
            "returncode": result.returncode,
            "stdout": result.stdout[-8000:],
            "stderr": result.stderr[-8000:],
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "returncode": None, "stdout": "", "stderr": "timed out", "success": False}
    except FileNotFoundError as e:
        return {"cmd": cmd, "returncode": None, "stdout": "", "stderr": str(e), "success": False}


def run_tests() -> dict:
    ptype = detect_project_type()
    if ptype == "python":
        return run_command(["python3", "-m", "pytest"])
    if ptype == "node":
        return run_command(["npm", "test"])
    return {"cmd": None, "returncode": None, "stdout": "",
            "stderr": "no recognized test setup (no package.json or .py files found)",
            "success": False}


def run_arbitrary_shell(cmd: list, timeout: int = 60) -> dict:
    """
    Explicitly for use ONLY after approval has been granted for a
    'run_shell' action — bypasses the whitelist. The orchestrator is
    responsible for enforcing that approval happened before calling this.
    """
    try:
        result = subprocess.run(
            cmd, cwd=str(config.WORKSPACE_DIR), capture_output=True,
            text=True, timeout=timeout,
        )
        return {
            "cmd": cmd, "returncode": result.returncode,
            "stdout": result.stdout[-8000:], "stderr": result.stderr[-8000:],
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"cmd": cmd, "returncode": None, "stdout": "", "stderr": "timed out", "success": False}
