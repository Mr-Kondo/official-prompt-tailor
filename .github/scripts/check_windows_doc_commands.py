"""Run the documented Windows PowerShell commands (docs/feedback.md) with real Git/Python."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
NAME = "official-prompt-tailor"


def run(args, **kwargs):
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **kwargs)
    if completed.returncode:
        raise AssertionError(f"{args[0]} exited {completed.returncode}\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout


def powershell(shell, command, env, cwd, success=True):
    command += "\nif (-not $?) { exit 1 }; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                               env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if (completed.returncode == 0) != success:
        raise AssertionError(f"Documented command exited {completed.returncode}\n{command}\n{completed.stdout}\n{completed.stderr}")
    return completed


def check_feedback_commands(shell, git, env, base):
    """Run the docs/feedback.md blocks from a repo-root-like folder, with paths that contain a space and Japanese."""
    blocks = re.findall(r"```text powershell\r?\n(.*?)```", (ROOT / "docs/feedback.md").read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 3, "Expected report folder, questionnaire template and export commands"
    work = base / "feedback docs 日本語"
    shutil.copytree(ROOT / "docs/templates", work / "docs/templates")
    shutil.copytree(ROOT / ".claude/skills" / NAME / "scripts", work / ".claude/skills" / NAME / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / ".gitignore", work / ".gitignore")
    run([git, "init", "--initial-branch=main", str(work)], env=env)
    folder = work / "feedback/case-001"
    made = powershell(shell, blocks[0], env, work)
    assert (folder / "problem-report.md").is_file() and "feedback/" in made.stdout, "Report folder command failed"
    assert not run([git, "-C", str(work), "ls-files", "--", "feedback/"], env=env).strip(), "feedback/ must stay untracked"
    (folder / "problem-report.md").write_bytes(b"keep me")
    failed = powershell(shell, blocks[0], env, work, success=False)
    assert "Report already exists" in failed.stderr and (folder / "problem-report.md").read_bytes() == b"keep me"
    powershell(shell, blocks[1], env, work)
    draft = folder / "draft.json"
    document = json.loads(draft.read_text(encoding="utf-8"))
    document.update(origin="artificial_test", stage="downstream_workbook", conditions="matched")
    document["environment"] = {"execution_host": "codex", "os": "windows"}
    draft.write_bytes(json.dumps(document).encode("utf-8"))
    powershell(shell, blocks[2], env, work)
    assert json.loads((folder / "report.json").read_text(encoding="utf-8"))["evidence_status"] == "reported_not_independently_verified"
    print(f"PASS: {shell} docs/feedback.md: report folder, template, export; no overwrite; space/Japanese paths", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", choices=("powershell", "pwsh"), required=True)
    args = parser.parse_args()
    shell, git = shutil.which(args.shell), shutil.which("git")
    assert os.name == "nt" and shell and git, "Windows, Git and the requested PowerShell are required"
    print(f"Python {sys.version}; PowerShell " + run([shell, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]).strip(), flush=True)
    with tempfile.TemporaryDirectory(prefix="opt-doc-commands-") as tmp:
        base = Path(tmp)
        # Run the real python.exe directly, excluding the separate py launcher from PATH.
        env = {**os.environ, "PATH": os.pathsep.join([str(Path(sys.executable).parent), str(Path(git).parent), str(Path(os.environ["SystemRoot"]) / "System32")]),
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        check_feedback_commands(shell, git, env, base)


if __name__ == "__main__":
    main()
