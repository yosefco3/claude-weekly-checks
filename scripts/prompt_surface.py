#!/usr/bin/env python3
"""Inventory the "prompt surface" and report which files are due for a prompt audit.

Run as `python3 -I scripts/prompt_surface.py <command>`; standard library only.
It never prints file contents: only paths, sizes, hashes and statuses.

  plan [--model ID] [--full] [--json]
      Compare the current inventory with the manifest of the last finished audit
      and say what is due. Always (re)writes pending.json with the full snapshot.
  commit [--group config|app]
      Promote pending.json into manifest.json (the audit is finished).

State lives in $HOME/.claude/prompt-audit/ (manifest.json, pending.json) and
nowhere else is written. Project roots come from
$HOME/.claude/weekly-checks/settings.toml (`project_roots = ["~/code"]`), else
whichever of ~/Desktop ~/code ~/projects ~/src ~/dev ~/repos ~/work exist.

Two groups: "config" (instruction files: CLAUDE.md, skills, commands, ...) and
"app" (projects that call an LLM API, with their prompt/model fingerprint files).
Each group stores the model it was last audited with and goes full on its own.

Exit codes: 0 ok, 2 usage or state error, 3 unexpected error.
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

try:   # tomllib is 3.11+
    import tomllib
except ImportError:   # pragma: no cover
    tomllib = None

HOME = Path.home()
STATE_DIR = HOME / ".claude" / "prompt-audit"
MANIFEST = STATE_DIR / "manifest.json"
PENDING = STATE_DIR / "pending.json"
SETTINGS_PATH = HOME / ".claude" / "weekly-checks" / "settings.toml"
DEFAULT_PROJECT_DIRS = ("Desktop", "code", "projects", "src", "dev", "repos", "work")

PRUNE = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
         ".next", "target", "vendor", "site-packages"}
MAX_DEPTH = 4
INSTRUCTION_NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
MD_DIRS = ("commands", "agents", "rules", "output-styles")
MANIFEST_GLOBS = ("requirements*.txt", "pyproject.toml", "package.json", "go.mod", "Gemfile",
                  "pom.xml", "build.gradle", "build.gradle.kts", "composer.json", "*.csproj")
NAME_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rb", ".java", ".kt", ".php",
             ".cs", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".j2", ".jinja"}
CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rb", ".java", ".kt", ".php", ".cs"}
MODEL_RE = re.compile(r"anthropic|claude-(opus|sonnet|haiku|fable|mythos|instant|[23])", re.I)
MAX_FINGERPRINT_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
BAD_NAME_PREFIXES = (".env", "settings", "credentials")


class StateError(Exception):
    """Usage or state problem; exit 2."""


# ---------------------------------------------------------------- helpers

def show(path):
    p = str(path)
    h = str(HOME)
    if p == h:
        return "~"
    if p.startswith(h + os.sep):
        return "~" + p[len(h):]
    return p


def file_record(path):
    """{bytes, sha256} or None if unreadable / not a regular file."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def read_bytes(path, limit):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit + 1)
    except OSError:
        return None


def entries(path):
    try:
        with os.scandir(path) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError:
        return []


def walk_dirs(root, max_depth, keep_hidden_claude=True):
    """Yield (dir, entries, depth) for root and its subdirectories down to max_depth,
    pruning PRUNE names and hidden dirs (except .claude). Symlinks are not followed;
    symlinked dirs are yielded in entries only (callers decide whether to report)."""
    stack = [(root, 0)]
    while stack:
        cur, depth = stack.pop()
        ents = entries(cur)
        yield cur, ents, depth
        if depth >= max_depth:
            continue
        for e in reversed(ents):
            if e.is_symlink():
                continue
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            name = e.name
            if name in PRUNE:
                continue
            if name.startswith(".") and not (keep_hidden_claude and name == ".claude"):
                continue
            stack.append((e.path, depth + 1))


def walk_files(root):
    """All regular files under root, pruned as above, symlinks not followed.
    Yields (path, is_symlink)."""
    for _cur, ents, _d in walk_dirs(root, 10 ** 6):
        for e in ents:
            if e.is_symlink():
                yield e.path, True
            else:
                try:
                    if e.is_file(follow_symlinks=False):
                        yield e.path, False
                except OSError:
                    pass


# ---------------------------------------------------------------- project roots

def expand_root(text):
    if text == "~" or text.startswith(("~/", "~" + os.sep)):
        text = text[1:].lstrip("/" + os.sep)
    return os.path.normpath(os.path.join(str(HOME), text))


def project_roots():
    value = None
    if SETTINGS_PATH.exists():
        if tomllib is None:
            raise StateError("settings.toml: this Python has no tomllib (3.11+ needed)")
        raw = read_bytes(SETTINGS_PATH, 1024 * 1024)
        if raw is None or len(raw) > 1024 * 1024:
            raise StateError("settings.toml: cannot read or too large")
        try:
            value = tomllib.loads(raw.decode("utf-8-sig")).get("project_roots")
        except (ValueError, RecursionError) as e:
            raise StateError("settings.toml: cannot parse: %s" % type(e).__name__)
    if value is None:
        wanted = ["~/" + d for d in DEFAULT_PROJECT_DIRS]
    elif not (isinstance(value, list) and all(isinstance(p, str) for p in value)):
        raise StateError("settings.toml: project_roots must be a list of strings")
    elif not all(p.strip() for p in value):
        raise StateError("settings.toml: project_roots must not contain empty strings")
    else:
        wanted = value
    roots = []
    for entry in wanted:
        full = expand_root(entry)
        if os.path.isdir(full) and full not in roots:
            roots.append(full)
    return roots


# ---------------------------------------------------------------- config group

def md_files_under(base, skipped):
    """Every *.md under base (recursive, pruned). Symlinked ones are reported."""
    out = []
    if not os.path.isdir(base) or os.path.islink(base):
        return out
    for path, is_link in walk_files(base):
        if path.endswith(".md"):
            (skipped.append(path) if is_link else out.append(path))
    return out


def claude_dir_files(cdir, skipped):
    """Instruction files inside one .claude directory (skills/*/SKILL.md one level)."""
    out = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        p = os.path.join(cdir, name)
        if os.path.islink(p):
            skipped.append(p)
        elif os.path.isfile(p):
            out.append(p)
    skills = os.path.join(cdir, "skills")
    if os.path.isdir(skills) and not os.path.islink(skills):
        for e in entries(skills):
            if e.is_symlink():
                skipped.append(e.path)
                continue
            if not e.is_dir(follow_symlinks=False):
                continue
            p = os.path.join(e.path, "SKILL.md")
            if os.path.islink(p):
                skipped.append(p)
            elif os.path.isfile(p):
                out.append(p)
    for sub in MD_DIRS:
        out.extend(md_files_under(os.path.join(cdir, sub), skipped))
    return out


def collect_config(roots, skipped):
    paths = []
    cdir = str(HOME / ".claude")
    p = os.path.join(cdir, "CLAUDE.md")
    if os.path.islink(p):
        skipped.append(p)
    elif os.path.isfile(p):
        paths.append(p)
    skills = os.path.join(cdir, "skills")
    if os.path.isdir(skills):
        for e in entries(skills):
            if e.is_symlink():
                skipped.append(e.path)
            elif e.is_dir(follow_symlinks=False):
                s = os.path.join(e.path, "SKILL.md")
                if os.path.islink(s):
                    skipped.append(s)
                elif os.path.isfile(s):
                    paths.append(s)
    for sub in MD_DIRS:
        paths.extend(md_files_under(os.path.join(cdir, sub), skipped))
    for root in roots:
        for cur, ents, _d in walk_dirs(root, MAX_DEPTH):
            for e in ents:
                if e.name in INSTRUCTION_NAMES:
                    if e.is_symlink():
                        skipped.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        paths.append(e.path)
                elif e.name == ".claude":
                    if e.is_symlink():
                        skipped.append(e.path)
                    elif e.is_dir(follow_symlinks=False):
                        paths.extend(claude_dir_files(e.path, skipped))
    config = {}
    for path in sorted(set(paths)):
        rec = file_record(path)
        if rec:
            config[show(path)] = rec
    return config


# ---------------------------------------------------------------- app group

def is_manifest_name(name):
    return any(fnmatch.fnmatch(name, g) for g in MANIFEST_GLOBS)


def is_lock_name(name):
    n = name.lower()
    return n.endswith((".lock", "-lock.json", "-lock.yaml", "-lock.yml")) or n in (
        "package-lock.json", "npm-shrinkwrap.json", "composer.lock", "go.sum")


def is_config_like(path, project_dir):
    """True for files that belong to group config (never fingerprinted)."""
    name = os.path.basename(path)
    if name in INSTRUCTION_NAMES:
        return True
    parts = Path(os.path.relpath(path, project_dir)).parts
    if ".claude" in parts:
        i = len(parts) - 1 - parts[::-1].index(".claude")
        rest = parts[i + 1:]
        if name == "SKILL.md" and len(rest) == 3 and rest[0] == "skills":
            return True
        if name.endswith(".md") and len(rest) >= 2 and rest[0] in MD_DIRS:
            return True
    return False


def find_projects(roots):
    projects = {}
    for root in roots:
        for cur, ents, _d in walk_dirs(root, MAX_DEPTH, keep_hidden_claude=False):
            for e in ents:
                if e.is_symlink() or not is_manifest_name(e.name):
                    continue
                try:
                    if not e.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                data = read_bytes(e.path, MAX_MANIFEST_BYTES)
                if data is None or b"anthropic" not in data.lower():
                    continue
                projects[nearest_git_dir(cur, root)] = True
    return sorted(projects)


def nearest_git_dir(start, root):
    cur = start
    while True:
        if os.path.lexists(os.path.join(cur, ".git")):
            return cur
        if cur == root or os.path.dirname(cur) == cur:
            return start
        cur = os.path.dirname(cur)


def fingerprint_files(project_dir, skipped):
    out = {}
    for path, is_link in walk_files(project_dir):
        name = os.path.basename(path)
        low = name.lower()
        ext = os.path.splitext(low)[1]
        if low.startswith(BAD_NAME_PREFIXES) or low == ".mcp.json" or is_lock_name(name):
            continue
        if ext not in NAME_EXTS or is_config_like(path, project_dir):
            continue
        rel = os.path.relpath(path, project_dir).lower()
        # A file named like a prompt, or one inside a directory called exactly
        # prompt/prompts. A looser "prompt anywhere in the path" also catches
        # spec folders written for coding agents (feature-prompts/ and the like),
        # which are not text the application sends to a model.
        parts = rel.split(os.sep)
        by_name = "prompt" in parts[-1] or any(p in ("prompt", "prompts") for p in parts[:-1])
        by_code = ext in CODE_EXTS
        if not (by_name or by_code):
            continue
        if is_link:
            if by_name or by_code:
                skipped.append(path)
            continue
        try:
            if os.path.getsize(path) > MAX_FINGERPRINT_BYTES:
                continue
        except OSError:
            continue
        if not by_name:
            data = read_bytes(path, MAX_FINGERPRINT_BYTES)
            if data is None or not MODEL_RE.search(data.decode("utf-8", "replace")):
                continue
        rec = file_record(path)
        if rec:
            out[show(path)] = rec
    return dict(sorted(out.items()))


def collect_apps(roots, skipped):
    return {show(p): fingerprint_files(p, skipped) for p in find_projects(roots)}


# ---------------------------------------------------------------- state files

def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise StateError("cannot read %s: %s" % (path.name, type(e).__name__))
    if not isinstance(data, dict):
        raise StateError("%s is not a JSON object" % path.name)
    return data


def write_json_atomic(path, data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(STATE_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- plan

def group_mode(manifest, group_key, model_key, args):
    if manifest is None or manifest.get(group_key) is None:
        return "full", "no_manifest"
    if args.full:
        return "full", "forced"
    if args.model is not None and manifest.get(model_key) != args.model:
        return "full", "model_changed"
    return "incremental", "incremental"


def diff_files(current, old):
    """[(path, status)] for every current file plus removed ones."""
    rows = []
    for path, rec in current.items():
        if path not in old:
            rows.append((path, "new"))
        elif old[path].get("sha256") != rec["sha256"]:
            rows.append((path, "changed"))
        else:
            rows.append((path, "same"))
    removed = sorted(p for p in old if p not in current)
    return rows, removed


def cmd_plan(args):
    manifest = read_json(MANIFEST)
    roots = project_roots()
    skipped = []
    config = collect_config(roots, skipped)
    apps = collect_apps(roots, skipped)
    skipped = sorted({show(p) for p in skipped})

    c_mode, c_reason = group_mode(manifest, "config", "config_model", args)
    a_mode, a_reason = group_mode(manifest, "apps", "app_model", args)

    old_config = (manifest or {}).get("config") or {}
    rows, removed = diff_files(config, old_config)
    due = []
    same = 0
    for path, status in rows:
        if c_mode == "full" or status != "same":
            due.append({"path": path, "bytes": config[path]["bytes"], "status": status})
        else:
            same += 1
    config_out = {"mode": c_mode, "reason": c_reason, "due": due,
                  "due_bytes": sum(d["bytes"] for d in due), "same_count": same,
                  "total_count": len(config), "removed": removed}

    old_apps = (manifest or {}).get("apps") or {}
    apps_out = []
    for project, files in apps.items():
        file_rows, gone = diff_files(files, old_apps.get(project, {}))
        changed = [{"path": p, "status": s} for p, s in file_rows if s != "same"]
        changed += [{"path": p, "status": "removed"} for p in gone]
        is_due = a_mode == "full" or project not in old_apps or bool(changed)
        apps_out.append({"project": project, "status": "due" if is_due else "same",
                         "changed_files": changed, "files_count": len(files)})
    removed_projects = sorted(p for p in old_apps if p not in apps)

    write_json_atomic(PENDING, {"timestamp": now_iso(), "model": args.model,
                                "config": config, "apps": apps})

    previous = None
    if manifest is not None:
        previous = {"finished_at": manifest.get("finished_at"),
                    "config_model": manifest.get("config_model"),
                    "app_model": manifest.get("app_model")}
    result = {"previous": previous, "model": args.model, "config": config_out,
              "apps_mode": {"mode": a_mode, "reason": a_reason}, "apps": apps_out,
              "removed_projects": removed_projects,
              "skipped_symlinks": skipped, "project_roots": [show(r) for r in roots]}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)
    return 0


def print_summary(r):
    c = r["config"]
    prev = r["previous"]
    print("Prompt surface plan (model: %s)" % (r["model"] or "none"))
    print("Last finished audit: %s" % (prev["finished_at"] if prev else "never"))
    print("Project roots: %s" % (", ".join(r["project_roots"]) or "(none found)"))
    print("Config: %s (%s): %d due of %d files, %d bytes due, %d same, %d removed"
          % (c["mode"], c["reason"], len(c["due"]), c["total_count"], c["due_bytes"],
             c["same_count"], len(c["removed"])))
    for d in c["due"]:
        print("  due  %-8s %8d  %s" % (d["status"], d["bytes"], d["path"]))
    for p in c["removed"]:
        print("  removed  %s" % p)
    am = r["apps_mode"]
    due_apps = [a for a in r["apps"] if a["status"] == "due"]
    print("Apps: %s (%s): %d due of %d projects" % (am["mode"], am["reason"], len(due_apps), len(r["apps"])))
    for a in r["apps"]:
        print("  %-4s %s (%d fingerprint files, %d changed)"
              % (a["status"], a["project"], a["files_count"], len(a["changed_files"])))
    for p in r["removed_projects"]:
        print("  removed project  %s" % p)
    print("Skipped symlinks: %d" % len(r["skipped_symlinks"]))
    for p in r["skipped_symlinks"]:
        print("  %s" % p)


# ---------------------------------------------------------------- commit

def cmd_commit(args):
    pending = read_json(PENDING)
    if pending is None:
        print("error: no pending.json; run `plan` first", file=sys.stderr)
        return 2
    manifest = read_json(MANIFEST) or {"config": None, "apps": None,
                                       "config_model": None, "app_model": None}
    groups = ["config", "app"] if args.group is None else [args.group]
    if "config" in groups:
        manifest["config"] = pending.get("config", {})
        manifest["config_model"] = pending.get("model")
    if "app" in groups:
        manifest["apps"] = pending.get("apps", {})
        manifest["app_model"] = pending.get("model")
    manifest["finished_at"] = now_iso()
    write_json_atomic(MANIFEST, manifest)
    if args.group is None:
        try:
            PENDING.unlink()
        except FileNotFoundError:
            pass
    print("committed: %s" % ", ".join(groups))
    return 0


# ---------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(prog="prompt_surface.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="inventory and list what is due")
    p.add_argument("--model", default=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--json", action="store_true")
    c = sub.add_parser("commit", help="promote pending.json into manifest.json")
    c.add_argument("--group", choices=("config", "app"), default=None)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)   # argparse exits 2 on usage errors
    try:
        return cmd_plan(args) if args.cmd == "plan" else cmd_commit(args)
    except StateError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    except Exception as e:   # no traceback wall
        print("error: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
