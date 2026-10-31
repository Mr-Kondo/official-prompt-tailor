#!/usr/bin/env python3
"""Install the official-prompt-tailor skill (macOS, Linux, Windows; Python 3.9+, stdlib only).

  python3 install.py --project <dir>                  Claude Code: <dir>/.claude/skills/official-prompt-tailor
  python3 install.py --user                           Claude Code: ~/.claude/skills/official-prompt-tailor
  python3 install.py --target codex --project <dir>   Codex: <dir>/.agents/skills/...   (--user: ~/.agents/skills)
  python3 install.py --target antigravity --user      Antigravity app/IDE: ~/.gemini/config/skills
  python3 install.py --target antigravity-cli --user  Antigravity CLI: ~/.gemini/antigravity-cli/skills
  python3 install.py --zip <dir>                      <dir>/official-prompt-tailor.zip for claude.ai / Cowork upload
  python3 install.py --plugin <dir>                   <dir>/official-prompt-tailor/ as a plugin (ChatGPT Work / Codex format)

  python3 install.py --update ...                     bring an installed copy up to date (the same arguments as the install); --force also works
  python3 install.py --uninstall ...                  remove what the same --project/--user/--target (or --plugin) arguments installed

Copies the skill directory only. It does not edit settings, hooks or permissions. Installing never deletes:
with --force or --update an existing copy (or ZIP) is renamed to <name>.bak-<timestamp> beside it.
--uninstall is the only command that deletes: it removes a copy it recognises as this skill (backups are left alone).
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

NAME = "official-prompt-tailor"
SRC = Path(__file__).resolve().parent / ".claude" / "skills" / NAME
# per product: where its documentation says skills are loaded from (project-relative, home-relative)
TARGETS = {
    "claude-code": (".claude/skills", ".claude/skills"),
    "codex": (".agents/skills", ".agents/skills"),
    "antigravity": (".agents/skills", ".gemini/config/skills"),  # app and IDE
    "antigravity-cli": (".agents/skills", ".gemini/antigravity-cli/skills"),
}
UPLOAD_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}  # claude.ai rejects others
UPLOAD_DESCRIPTION_MAX = 200  # the help article's limit; the API docs say 1024, so the stricter one is used
PLUGIN_VERSION = "0.7.0"
PLUGIN_AUTHOR = "official-prompt-tailor"  # plugin manifests require an author name; --author overrides it
SELF_CHECK = """
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import evidence
import private_feedback
reg, _ = evidence.load_registry()
print("check: %d sources, %d rules" % (len(reg["sources"]), len(reg["rules"])))
"""


def die(msg):
    print(f"install.py: {msg}", file=sys.stderr)
    sys.exit(1)


@contextmanager
def restore_on_error(dest, backup):
    # Enter only after the old destination has been moved successfully.
    try:
        yield
    except (OSError, UnicodeError, subprocess.SubprocessError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        try:
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            elif dest.exists():
                shutil.rmtree(dest)
            if backup:
                backup.rename(dest)
        except OSError as recovery_error:
            die(f"installation failed: {error}\nrecovery failed: {recovery_error}\n"
                f"destination: {dest}\nprevious copy: {backup if backup else 'none (first installation)'}")
        die(f"installation failed; rolled back\n{error}")


def self_check(skill_dir):
    # UTF-8 on both ends: a Windows console code page must not decide how the child's messages (paths may be Japanese) are read.
    r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-c", SELF_CHECK, str(Path(skill_dir) / "scripts")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr).strip()


def upload_problems():
    """What claude.ai / Cowork would reject in SKILL.md frontmatter (empty list = uploadable)."""
    text = (SRC / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return ["SKILL.md has no frontmatter on its first line"]
    keys = {l.split(":", 1)[0] for l in m.group(1).splitlines() if re.match(r"^[A-Za-z_-]+:", l)}
    out = [f"frontmatter key not accepted for upload: {k}" for k in sorted(keys - UPLOAD_KEYS)]
    desc = next((l.split(":", 1)[1].strip() for l in m.group(1).splitlines() if l.startswith("description:")), "")
    if not desc or len(desc) > UPLOAD_DESCRIPTION_MAX:
        out.append(f"description must be 1-{UPLOAD_DESCRIPTION_MAX} characters (is {len(desc)})")
    return out


def expected_files(plugin):
    """Relative paths this script installs (the skill source, plus the manifest for a plugin)."""
    skill = {p.relative_to(SRC) for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    if not plugin:
        return {p.as_posix() for p in skill}
    return {(Path("skills") / NAME / p).as_posix() for p in skill} | {".codex-plugin/plugin.json"}


def is_our_skill(skill_dir):
    md = skill_dir / "SKILL.md"
    if not md.is_file() or not (skill_dir / "scripts" / "evidence.py").is_file():
        return False
    m = re.match(r"---\n(.*?)\n---\n", md.read_text(encoding="utf-8", errors="replace"), re.S)
    return bool(m) and re.search(r"^name:\s*%s\s*$" % re.escape(NAME), m.group(1), re.M) is not None


def is_our_plugin(plugin_dir):
    try:
        manifest = json.loads((plugin_dir / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == NAME and is_our_skill(plugin_dir / "skills" / NAME)


def free_backup(parent, name):
    """<name>.bak-<timestamp> beside the copy; a second one in the same second gets -2, -3 ... instead of replacing or stopping."""
    first = parent / f"{name}.bak-{datetime.now():%Y%m%d%H%M%S}"
    path, n = first, 2
    while path.exists() or path.is_symlink():
        path, n = parent / f"{first.name}-{n}", n + 1
    return path


def copy_state(dest, plugin):
    """What stands at dest: absent | unrecognised (not this skill, or a symlink) | current (same files as the source) | outdated."""
    if not dest.exists() and not dest.is_symlink():
        return "absent"
    if dest.is_symlink() or not (is_our_plugin(dest) if plugin else is_our_skill(dest)):
        return "unrecognised"
    skill = dest / "skills" / NAME if plugin else dest
    present = {p.relative_to(skill).as_posix() for p in skill.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    wanted = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    if present != wanted or any((skill / r).read_bytes() != (SRC / r).read_bytes() for r in wanted):
        return "outdated"
    if plugin:
        try:
            if json.loads((dest / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version") != PLUGIN_VERSION:
                return "outdated"
        except (OSError, ValueError):
            return "outdated"
    return "current"


def uninstall(dest, plugin, force, dry):
    """Remove one installed copy. Refuses anything it cannot recognise, the skill source, and (without --force) unknown files."""
    tag = "[dry-run] " if dry else ""
    if not dest.exists() and not dest.is_symlink():
        die(f"not installed: {dest}")
    if dest.is_symlink():  # remove the link only; what it points to is not ours to delete
        print(f"{tag}unlink {dest} (symlink; its target is left alone)")
        if not dry:
            dest.unlink()
    else:
        real, src = dest.resolve(), SRC.resolve()
        if real == src or real in src.parents or src in real.parents:
            die(f"refusing to remove the skill source: {dest}")
        if not (is_our_plugin(dest) if plugin else is_our_skill(dest)):
            die(f"not recognised as this {'plugin' if plugin else 'skill'}, left alone: {dest}")
        known = expected_files(plugin)
        extra = sorted(r for r in (p.relative_to(dest).as_posix() for p in dest.rglob("*")
                                   if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc") if r not in known)
        if extra and not force:
            die(f"{dest} holds files this script did not install ({', '.join(extra[:5])}{', ...' if len(extra) > 5 else ''}); "
                "use --force to remove it anyway")
        print(f"{tag}remove {dest}")
        if not dry:
            shutil.rmtree(dest)
    for b in sorted((dest.parent if plugin else dest.parent.parent).glob(f"{NAME}.bak-*")):  # install.py keeps backups beside the copy
        print(f"backup left in place: {b}")
    print("dry-run only: nothing was changed" if dry else f"uninstalled: {dest}")
    return 0


def plugin_manifest(author):
    """.codex-plugin/plugin.json in the shape OpenAI's plugin validator accepts (the format ChatGPT and Codex share)."""
    return {
        "name": NAME,
        "version": PLUGIN_VERSION,
        "description": "プロンプトを利用先の公式資料に基づいて最適化し、取得・引用・適用範囲をスクリプトで検証して返すスキル。",
        "author": {"name": author},
        "skills": "./skills/",
        "interface": {
            "displayName": "Official Prompt Tailor",
            "shortDescription": "公式資料に基づくプロンプト最適化と検証",
            "longDescription": "プロンプトを利用先の公式資料に基づいて最適化し、取得・引用・適用範囲・変更箇所への結合をスクリプトで検証する。"
                               "スクリプトの実行と、公式ドメインへの HTTPS 接続が必要。読取り観測は Claude Code・Codex・Antigravity の対応ログで行い、"
                               "ログがない場合や未対応の製品では参照検証は INCOMPLETE になる。",
            "developerName": author,
            "category": "Productivity",
            "capabilities": ["Interactive", "Write"],
            "defaultPrompt": ["このプロンプトを公式資料に沿って最適化して", "Codex 向けにプロンプトを最適化して", "最適化案の根拠と検証結果を見せて"],
        },
    }


def make_plugin(out_dir, author, force, dry, update=False):
    out_dir = Path(out_dir).resolve()
    if not out_dir.is_dir():
        die(f"not a directory: {out_dir}")
    if SRC.resolve() == out_dir or SRC.resolve() in out_dir.parents:
        die("the output directory is inside the skill source")
    dest = out_dir / NAME  # the folder name must equal the manifest name
    if dest.resolve() == SRC.resolve():
        die("source and destination are the same directory")
    tag = "[dry-run] " if dry else ""
    backup = None
    state = copy_state(dest, True)
    if update and not force:
        if state == "unrecognised":
            die(f"not recognised as this plugin, left alone: {dest} (--force replaces anything; the old one is kept)")
        if state == "current":
            print(f"up to date: {dest}")
            return 0
    if state != "absent":
        if not (force or update):
            die(f"already exists: {dest} (use --update or --force; the old copy is kept)")
        backup = free_backup(out_dir, NAME)
        print(f"{tag}move {dest} -> {backup}")
        if not dry:
            dest.rename(backup)
    print(f"{tag}plugin {NAME} {PLUGIN_VERSION} -> {dest}")
    if dry:
        print("dry-run only: nothing was changed")
        return 0
    skill = dest / "skills" / NAME
    with restore_on_error(dest, backup):
        shutil.copytree(SRC, skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (dest / ".codex-plugin").mkdir()
        (dest / ".codex-plugin" / "plugin.json").write_bytes(
            (json.dumps(plugin_manifest(author or PLUGIN_AUTHOR), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        ok, msg = self_check(skill)
        if not ok:
            raise OSError("plugin self-check failed\n" + msg)
    print(msg)
    print(f"created: {dest}" + (f" (previous copy kept at {backup})" if backup else ""))
    print("next: how a plugin folder is added to ChatGPT Work is not verified here; see docs/products.md.")
    print("  The skill inside needs script execution and network egress to the domains listed in README.md.")
    return 0


ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # the earliest time a ZIP can hold; the files' own times are not used


def write_zip(target, files):
    """The same files give the same bytes on any machine at any time: sorted by name, one fixed time, mode and creator, no compression.
    So the ZIP built from a release tag can be compared with the one published for that tag (docs/maintenance.md).
    shortcut: stored, not deflated, because a deflate stream depends on the zlib build; deflate if the size ever matters."""
    named = sorted((f"{NAME}/{p.relative_to(SRC).as_posix()}", p) for p in files)  # the skill folder is the ZIP root, as the upload requires
    with zipfile.ZipFile(target, "w") as z:
        for arcname, p in named:
            info = zipfile.ZipInfo(arcname, ZIP_TIME)
            info.create_system, info.external_attr = 3, 0o644 << 16
            z.writestr(info, p.read_bytes())


def make_zip(out_dir, force, dry):
    out_dir = Path(out_dir).resolve()
    if not out_dir.is_dir():
        die(f"not a directory: {out_dir}")
    problems = upload_problems()
    if problems:
        die("the skill would be rejected on upload:\n  " + "\n  ".join(problems))
    target = out_dir / f"{NAME}.zip"
    if target.exists() and not force:
        die(f"already exists: {target} (use --force; the old file is kept)")
    files = [p for p in sorted(SRC.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    print(("[dry-run] " if dry else "") + f"zip {len(files)} files -> {target}")
    if dry:
        print("dry-run only: nothing was changed")
        return 0
    backup = None
    if target.exists():
        backup = free_backup(out_dir, f"{NAME}.zip")
        target.rename(backup)
        print(f"previous ZIP kept at {backup}")
    with restore_on_error(target, backup):
        write_zip(target, files)
        with zipfile.ZipFile(target) as z, tempfile.TemporaryDirectory() as tmp:
            if f"{NAME}/SKILL.md" not in z.namelist():
                raise OSError("ZIP self-check failed: SKILL.md is not under the skill folder")
            z.extractall(tmp)
            ok, msg = self_check(Path(tmp) / NAME)
        if not ok:
            raise OSError("ZIP self-check failed\n" + msg)
    print(msg)
    print(f"created: {target}")
    print(f"sha256: {hashlib.sha256(target.read_bytes()).hexdigest()}")
    print("next (Claude / Cowork): Customize > Skills > + > Upload a skill, then choose this ZIP.")
    print("  Needs code execution enabled and network egress to the domains listed in README.md.")
    print("  ChatGPT Work has no documented folder for standalone skills: build a plugin with --plugin instead.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install the official-prompt-tailor skill.")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--project", metavar="DIR", help="install into the project at DIR (it must exist)")
    where.add_argument("--user", action="store_true", help="install for the current user (every project)")
    where.add_argument("--zip", metavar="DIR", help="write DIR/official-prompt-tailor.zip for claude.ai / Cowork upload")
    where.add_argument("--plugin", metavar="DIR", help="write DIR/official-prompt-tailor/ as a plugin (ChatGPT Work / Codex format)")
    ap.add_argument("--author", help="with --plugin: author name in the manifest (default: %s)" % PLUGIN_AUTHOR)
    ap.add_argument("--target", choices=sorted(TARGETS), help="product to install for (default: claude-code); not used with --zip")
    ap.add_argument("--force", action="store_true", help="replace an existing copy (the old one is kept as a backup); with --uninstall, remove even if unknown files are inside")
    ap.add_argument("--update", action="store_true", help="bring an installed copy up to date: replace it when it is this skill and differs from the source "
                    "(the old one is kept as a backup), do nothing when it is current, install when there is none; unlike --force it leaves alone what it does not recognise")
    ap.add_argument("--uninstall", action="store_true", help="remove the copy the same arguments would install (the only command that deletes)")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen and change nothing")
    ap.add_argument("--gitignore", action="store_true", help="with --project: add 'outputs/' to DIR/.gitignore")
    a = ap.parse_args(argv)

    if sys.version_info < (3, 9):
        die("Python 3.9 or newer is required")
    if not (SRC / "SKILL.md").is_file() or not (SRC / "scripts" / "evidence.py").is_file():
        die(f"skill sources not found under {SRC}")
    if a.uninstall and (a.zip or a.gitignore or a.author):
        die("--uninstall does not combine with --zip (delete the ZIP yourself), --gitignore or --author")
    if a.uninstall and a.update:
        die("--update and --uninstall do not combine")
    if a.zip:
        if a.target or a.gitignore or a.author:
            die("--target, --gitignore and --author do not apply to --zip")
        return make_zip(a.zip, a.force or a.update, a.dry_run)  # a ZIP is a build output: updating it is making it again
    if a.plugin:
        if a.target or a.gitignore:
            die("--target and --gitignore do not apply to --plugin")
        if a.uninstall:
            if not Path(a.plugin).is_dir():
                die(f"not a directory: {a.plugin}")
            return uninstall(Path(a.plugin).resolve() / NAME, True, a.force, a.dry_run)
        return make_plugin(a.plugin, a.author, a.force, a.dry_run, a.update)
    if a.author:
        die("--author only works with --plugin")
    if a.gitignore and not a.project:
        die("--gitignore only works with --project")
    project_rel, user_rel = TARGETS[a.target or "claude-code"]
    project = None
    if a.project:
        project = Path(a.project).resolve()
        if not project.is_dir():
            die(f"not a directory: {a.project}")
        base = project / project_rel
    else:
        base = Path.home() / user_rel
    dest = base / NAME
    if dest.resolve() == SRC.resolve():
        die("source and destination are the same directory")
    if a.uninstall:
        return uninstall(dest, False, a.force, a.dry_run)

    def say(action):
        print(("[dry-run] " if a.dry_run else "") + action)

    backup = None
    state = copy_state(dest, False)
    if a.update and not a.force:
        if state == "unrecognised":
            die(f"not recognised as this skill, left alone: {dest} (--force replaces anything; the old one is kept)")
    current = a.update and not a.force and state == "current"
    if a.update and state == "absent":
        print(f"not installed yet, installing: {dest}")
    if current:
        print(f"up to date: {dest}")
    elif state != "absent":
        if not (a.force or a.update):
            die(f"already installed: {dest} (use --update, or --force to replace anything; the old copy is kept)")
        # kept beside the skills folder, not inside it: a backup there would register as a second skill
        backup = free_backup(base.parent, NAME)
        say(f"move {dest} -> {backup}")
        if not a.dry_run:
            dest.rename(backup)
    if not current:
        say(f"copy {SRC} -> {dest}")
    if not a.dry_run and not current:
        with restore_on_error(dest, backup):
            base.mkdir(parents=True, exist_ok=True)
            shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            ok, msg = self_check(dest)
            if not ok:
                raise OSError("installed copy failed its self-check\n" + msg)
        print(msg)

    if a.gitignore:
        f = project / ".gitignore"
        text = f.read_text(encoding="utf-8") if f.is_file() else ""
        if "outputs/" in text.splitlines():
            print("gitignore: outputs/ already listed")
        else:
            say(f"append 'outputs/' to {f}")
            if not a.dry_run:
                f.write_bytes((text + ("" if not text or text.endswith("\n") else "\n") + "outputs/\n").encode("utf-8"))

    if a.dry_run:
        print("dry-run only: nothing was changed")
        return 0
    if current:
        return 0
    print(("updated: " if backup else "installed: ") + f"{dest}" + (f" (previous copy kept at {backup})" if backup else ""))
    print("next: start a new session of the product (running sessions may not list new skills), then invoke the skill")
    print(f"note: runs write to outputs/{NAME}/ in the workspace where the skill is used")
    return 0


if __name__ == "__main__":
    sys.exit(main())
