"""Run the documented Windows commands with real Git/Python and an isolated home."""
import argparse
import base64
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", choices=("powershell", "pwsh"), required=True)
    args = parser.parse_args()
    shell, git = shutil.which(args.shell), shutil.which("git")
    assert os.name == "nt" and shell and git, "Windows, Git and the requested PowerShell are required"
    print(f"Python {sys.version}; PowerShell " + run([shell, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]).strip(), flush=True)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = [("README.md", b) for b in re.findall(r"```powershell\r?\n(.*?)```", readme, re.S)]
    blocks += [("docs/install.md", b) for b in re.findall(r"```powershell\r?\n(.*?)```", (ROOT / "docs/install.md").read_text(encoding="utf-8"), re.S)]
    assert len(blocks) == 5, "Expected four product commands and one project command"
    clone = next(b for b in re.findall(r"```sh\r?\n(.*?)```", readme, re.S) if "git clone --branch main" in b)
    targets = {"claude-code": ".claude/skills", "codex": ".agents/skills",
               "antigravity": ".gemini/config/skills", "antigravity-cli": ".gemini/antigravity-cli/skills"}
    with tempfile.TemporaryDirectory(prefix="opt-doc-commands-") as tmp:
        base = Path(tmp)
        seed, origin = base / "seed", base / "origin.git"
        seed.mkdir()
        # Run the real python.exe directly, excluding the separate py launcher from PATH.
        env = {**os.environ, "PATH": os.pathsep.join([str(Path(sys.executable).parent), str(Path(git).parent), str(Path(os.environ["SystemRoot"]) / "System32")]),
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        shutil.copy2(ROOT / "install.py", seed / "install.py")
        shutil.copytree(ROOT / ".claude/skills" / NAME, seed / ".claude/skills" / NAME,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        run([git, "init", "--initial-branch=main", str(seed)], env=env)
        run([git, "-C", str(seed), "add", "."], env=env)
        run([git, "-C", str(seed), "-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-m", "fixture"], env=env)
        run([git, "clone", "--bare", str(seed), str(origin)], env=env)
        for index, (document, command) in enumerate(blocks):
            home = base / f"home {index} 日本語"
            home.mkdir()
            project = home / "project 日本語"
            project.mkdir()
            env.update(USERPROFILE=str(home), HOME=str(home))
            powershell(shell, 'if (Get-Command py -ErrorAction SilentlyContinue) { throw "py must be absent in this test" }; if ($HOME -ne $env:USERPROFILE) { throw "HOME must be isolated" }', env, base)
            powershell(shell, clone.replace("https://github.com/Mr-Kondo/official-prompt-tailor.git", origin.as_posix()), env, base)
            repo = home / NAME
            # Only documented path placeholders change; executable commands are read verbatim.
            command = command.replace("C:/path/to/official-prompt-tailor", repo.as_posix()).replace("C:/path/to/my-project", project.as_posix())
            target = re.search(r"--target ([a-z-]+)", command).group(1)
            dest = (project / ".agents/skills" if "--project" in command else home / targets[target]) / NAME
            powershell(shell, command, env, base)
            expected = (repo / ".claude/skills" / NAME / "references/recommendations-example.md").read_bytes()
            installed = dest / "references/recommendations-example.md"
            assert installed.read_bytes() == expected, "Initial installation did not match the source"
            installed.write_bytes(expected + b"\nold copy\n")
            powershell(shell, command, env, base)
            backups = list(dest.parent.parent.glob(NAME + ".bak-*"))
            assert installed.read_bytes() == expected and len(backups) == 1, "Update/backup failed"
            powershell(shell, command, env, base)
            assert list(dest.parent.parent.glob(NAME + ".bak-*")) == backups, "Repeated update changed backups"
            installed.write_bytes(expected + b"\nkeep on Git failure\n")
            run([git, "-C", str(repo), "remote", "set-url", "origin", str(base / "missing.git")], env=env)
            failed = powershell(shell, command, env, base, success=False)
            assert "Git update failed" in failed.stderr
            assert installed.read_bytes() == expected + b"\nkeep on Git failure\n", "Installer ran after Git failure"
            assert list(dest.parent.parent.glob(NAME + ".bak-*")) == backups
            run([git, "-C", str(repo), "remote", "set-url", "origin", str(origin)], env=env)
            shutil.rmtree(dest)
            dest.mkdir()
            marker = dest / "unrecognised.txt"
            marker.write_bytes(b"leave alone")
            failed = powershell(shell, command, env, base, success=False)
            assert "Skill update failed" in failed.stderr and marker.read_bytes() == b"leave alone"
            assert list(dest.parent.parent.glob(NAME + ".bak-*")) == backups
            print(f"PASS: {args.shell} {document} {target}: install, update, repeat, Git failure, installer failure; no py; space/Japanese paths", flush=True)


if __name__ == "__main__":
    main()
