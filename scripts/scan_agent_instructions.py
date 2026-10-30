#!/usr/bin/env python3
"""Scan AI-agent instruction files (CLAUDE.md, AGENTS.md, skills, hooks, plugins,
MCP configs) for patterns that could install or run malware, and report only
what changed since the user last approved a baseline.

Read-only except for --update-baseline, which writes
$HOME/.claude/security/baseline.json (mode 0600). The baseline stores item
names in clear text (file paths, skill names, plugin names, MCP server names)
and SHA-256 hashes for every value and every matched line: never raw matched
lines, hook commands or MCP configs, because those can contain secrets (bot
tokens, API keys). The baseline is local only; do not publish or sync it. The
folder $HOME/.claude/security is excluded from the scan. The "persistence"
category (below) stores only file paths and SHA-256 hashes.

Usage (always run isolated):
  python3 -I scan_agent_instructions.py
      Default, for the weekly check. Prints only what is NEW since the baseline:
      findings whose key is not in the baseline (grouped HIGH / HIDDEN / MEDIUM)
      and inventory changes (ADDED / REMOVED / CHANGED per category: hooks, mcp,
      plugins, skills, autorun_files, persistence). Prints NO_NEW_FINDINGS and
      exits 0 when nothing is new; exits 1 when something is new; prints
      NO_BASELINE and exits 2 when no baseline exists yet.
  python3 -I scan_agent_instructions.py --all
      Full report of every regex hit, as before. Exit 0.
  python3 -I scan_agent_instructions.py --update-baseline
      Approve the current state: write baseline.json (finding keys + inventory
      hashes), print counts per category and BASELINE_UPDATED.

Exit codes:
  0  nothing new (or --all / --update-baseline succeeded)
  1  new findings or inventory changes
  2  no baseline, or an unreadable baseline
  3  scanner error: an unexpected exception (or Python older than 3.11);
     prints "SCAN_ERROR: <ExceptionType>: <message>" on stdout. Never 1.
     A settings file that exists but is broken (see below) also exits 3 and
     prints "SCAN_ERROR: settings.toml: <reason>".

Project folders (where your projects live) and the optional settings file:
  $HOME/.claude/weekly-checks/settings.toml   (TOML; the file may be missing)
    project_roots = ["~/code", "~/work"]
        Folders whose projects are scanned for CLAUDE.md, AGENTS.md, .mcp.json,
        project hooks and so on. "~" and "~/x" mean HOME (the fake home when
        SCAN_HOME is set), other relative paths are relative to HOME too, and
        absolute paths are used as given. Folders that do not exist are skipped
        silently. An explicit empty list means no project folders at all.
    All other keys (for example "language") belong to other parts of the skill
    and are ignored by this scanner.
  Defaults, when the file or the key is missing: those of ~/Desktop, ~/code,
  ~/projects, ~/src, ~/dev, ~/repos and ~/work that exist.
  SCAN_PROJECT_ROOTS=dir1:dir2 (separated by os.pathsep, same expansion as
  above) overrides both the file and the defaults, for tests; the settings
  file is not read in that case.
  The settings file is hashed under autorun_files, so a change that drops a
  project folder shows up as CHANGED / REMOVED in the weekly check. --all, and
  the default check when it has something to report, print a
  "Project roots: <short paths>" line (or "(none found)") right after the
  first line; a clean default check still prints only NO_NEW_FINDINGS.
  A settings file that exists but cannot be read or parsed, or whose
  project_roots is not a list of non-empty strings, is an error (exit 3), never
  a quiet fallback: a broken config must not silently disable scanning.

Persistence inventory (category "persistence"). The "persistence" regex rule
only notices text that MENTIONS persistence; this category notices whether
something was actually planted where code runs on its own. It is inventory only:
the files are hashed (SHA-256, or the NONREGULAR marker for FIFOs and devices),
never regex-scanned and never printed, because they often hold secrets (exported
API keys, registry tokens). Item name = path (~/... under HOME, absolute for
system files), value = hash; a change prints as "ADDED / REMOVED / CHANGED
<path>" under "--- persistence ---" and nothing else. A baseline written before
this category existed counts as an empty one (everything is ADDED once). Missing
or unreadable paths are skipped silently. Directories are walked at most 4
levels deep and 500 files each (more than that adds a "<dir> :: TRUNCATED" item
so it is visible). Sources:
  shell startup (HOME)   .bashrc .bash_profile .bash_login .bash_logout .profile
                         .zshrc .zprofile .zshenv .zlogin .xprofile .xinitrc
                         .xsessionrc .config/fish/config.fish .config/fish/conf.d/*
  shell startup (system) /etc/profile /etc/profile.d/* /etc/bash.bashrc
                         /etc/environment /etc/zsh/zshrc /etc/zsh/zshenv
  services, autostart    ~/.config/systemd/user/ ~/.local/share/systemd/user/
                         ~/.config/autostart/*.desktop /etc/systemd/system/
                         /etc/systemd/user/ /etc/xdg/autostart/*.desktop
                         (symlinks under /etc/systemd are not followed: the item
                         value is a hash of "L:<target>", so retargeting shows)
  cron                   /etc/crontab /etc/cron.d/* /etc/cron.{hourly,daily,
                         weekly,monthly}/* /etc/anacrontab
                         /var/spool/cron/crontabs/<user> (usually unreadable)
                         and the user's own crontab, item "crontab -l (<user>)"
  SSH (HOME)             .ssh/authorized_keys .ssh/authorized_keys2 .ssh/config
                         .ssh/rc .ssh/environment
  dynamic loader         /etc/ld.so.preload /etc/ld.so.conf /etc/ld.so.conf.d/*
  sudoers                /etc/sudoers.d/* (usually unreadable)
  git (HOME)             .gitconfig .config/git/config .config/git/attributes
  package managers (HOME) .npmrc .yarnrc .yarnrc.yml .config/pip/pip.conf
                         .pip/pip.conf .pydistutils.cfg .cargo/config.toml
                         .cargo/config
The only subprocess call in this script is `crontab -l` (see crontab_digest):
fixed argument list, no shell, no stdin, 5 s timeout, environment limited to
PATH and LANG=C. Only a hash of its output is kept.

Finding key = sha256("severity|rule|path_with_~|stripped_line_text"); line
numbers are not part of the key, so shifting lines does not create noise.

Robustness against planted files: only regular files are read (FIFOs, devices
and symlinks to them are skipped or recorded as NONREGULAR), at most MAX_BYTES
of any scanned file is read, regex rules are bounded so no line can cause
catastrophic backtracking, and lines over LONG_LINE chars skip the regex rules
but are reported as [long-line].

Testing: set SCAN_HOME=/some/fake/home to run against a fake home directory.
Every path in this script derives from HOME (SCAN_PROJECT_ROOTS may add
absolute paths, and the persistence category reads system files under
SYSTEM_ROOT). When SCAN_HOME is set, the first stdout line is
"SCAN_HOME=<path>" in every mode.
  SCAN_SYSTEM_ROOT=/some/fake/root  replaces "/" as the base of the system
      files above (etc/..., var/...), so tests never read the real /etc.
  SCAN_NO_CRONTAB=1  skips the `crontab -l` call (tests must set it).
"""
import argparse
import getpass
import glob
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone

try:   # tomllib is 3.11+; main() reports SCAN_ERROR (exit 3) on older Pythons.
    import tomllib
except ImportError:
    tomllib = None

HOME = os.environ.get("SCAN_HOME") or os.path.expanduser("~")
HOME = os.path.abspath(HOME)
# Base of the system files in the persistence inventory (etc/..., var/...);
# tests point it at a temp dir so they never read the real /etc.
SYSTEM_ROOT = os.path.abspath(os.environ.get("SCAN_SYSTEM_ROOT") or "/")
SECURITY_DIR = f"{HOME}/.claude/security"   # holds the baseline; never scanned
BASELINE_PATH = f"{SECURITY_DIR}/baseline.json"

# Where agent instructions and auto-run code live.
ROOTS = [
    f"{HOME}/.claude/CLAUDE.md",
    f"{HOME}/.claude/settings.json",
    f"{HOME}/.claude/agents",
    f"{HOME}/.claude/skills",
    f"{HOME}/.claude/hooks",
    f"{HOME}/.claude/commands",
    f"{HOME}/.claude/output-styles",
    f"{HOME}/.claude/plugins/cache",
    f"{HOME}/.claude/plugins/marketplaces",
    f"{HOME}/.claude/mods",
    f"{HOME}/.claude/dev-mods",
    f"{HOME}/.claude/model-router",
    f"{HOME}/.claude/scheduled-tasks",
    f"{HOME}/.codex",
    f"{HOME}/.gemini",
    f"{HOME}/.config/opencode",
    f"{HOME}/.aider",
    f"{HOME}/.cursor/rules",
    f"{HOME}/.cursor/mcp.json",
]
SETTINGS_PATH = f"{HOME}/.claude/weekly-checks/settings.toml"   # optional; see docstring
# Used when there is no settings file (or no project_roots key); only those that exist count.
DEFAULT_PROJECT_DIRS = ("Desktop", "code", "projects", "src", "dev", "repos", "work")
PROJECT_ROOTS = None   # list of existing absolute folders; filled lazily by project_roots()
PROJECT_FILES = {"CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules",
                 "copilot-instructions.md", ".mcp.json", "SKILL.md"}
PROJECT_DIRS = {".claude", ".gemini", ".qoder", ".codex", ".cursor"}

SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "dist",
             "build", ".next", "state", "data", ".code-review-graph"}
TEXT_EXT = {".md", ".txt", ".json", ".sh", ".bash", ".zsh", ".py", ".js",
            ".mjs", ".cjs", ".ts", ".toml", ".yaml", ".yml", ".ps1", ".cmd",
            ".bat", ".rules", ".mdc", ""}
MAX_BYTES = 2_000_000            # most bytes read from any scanned text file
MAX_STRUCT_BYTES = 32_000_000    # JSON/TOML configs and the baseline (~/.claude.json grows)
LONG_LINE = 20_000               # longer lines skip the regex rules; see [long-line]

# Every pattern below must stay linear-ish on hostile input: no unbounded `.*` or
# `[^x]*` followed by more pattern. Planted files can hold very long lines.
HIGH = [
    ("pipe-to-shell", r"(curl|wget|iwr|Invoke-WebRequest)[^\n|]{0,200}\|\s*(sudo\s+)?(ba|z|da)?sh\b"),
    ("pipe-to-interpreter", r"(curl|wget)[^\n|]{0,200}\|\s*(sudo\s+)?(python3?|node|perl|ruby)\b"),
    ("shell-from-download", r"(ba)?sh\s+<\(\s*(curl|wget)|sh\s+-c\s+[\"']?\$\(\s*(curl|wget)"),
    ("base64-exec", r"base64\s+(-d|--decode)[^\n]{0,80}\|\s*(ba)?sh|(exec|eval)\s*\(\s*(base64|b64decode|atob)"),
    ("reverse-shell", r"/dev/tcp/|\bnc\s+(-[a-z]{0,20}e|-c)\b|\bncat\b.{0,40}-e|mkfifo\s+/tmp|socat\s+.{0,200}exec:"),
    ("credential-read", r"\.ssh/id_|\.credentials\.json|\.aws/credentials|\.netrc|Login Data|\.gnupg|keyring|\.config/gh/hosts"),
    ("persistence", r"crontab\s+-|>>\s*~?/?[^\s]{0,200}\.(bashrc|profile|zshrc|bash_profile)|authorized_keys|systemctl\s+--user\s+enable|/etc/sudoers"),
    ("exfil-endpoint", r"discord(app)?\.com/api/webhooks|pastebin\.com|transfer\.sh|ngrok\.io|requestbin|webhook\.site|pipedream\.net|interact\.sh|burpcollaborator"),
    ("hide-from-user", r"(do not|don't|never)\s+(tell|inform|mention|show)\s+(the\s+)?user|without\s+(telling|informing)\s+the\s+user|ignore\s+(all\s+)?(previous|prior)\s+instructions"),
]
MEDIUM = [
    ("download", r"\b(curl|wget)\s+[^\n]{0,30}https?://"),
    ("global-install", r"npm\s+(i|install)\s+(-g|--global)|pip3?\s+install|pipx\s+install|cargo\s+install|go\s+install|gem\s+install|brew\s+install|apt(-get)?\s+install"),
    ("npx-auto-yes", r"npx\s+(-y|--yes)\b"),
    ("make-executable", r"chmod\s+(\+x|[0-7]{0,4}7[0-7]{0,4})\s"),
    ("sudo", r"\bsudo\s+"),
    ("dynamic-exec", r"\beval\s+\"?\$\(|\bexec\s*\(\s*(requests|urllib|compile)"),
    ("disable-safety", r"--dangerously-skip-permissions|bypassPermissions|dangerouslyDisableSandbox"),
]

_HIGH_RE = [(name, re.compile(pat, re.I)) for name, pat in HIGH]
_MEDIUM_RE = [(name, re.compile(pat, re.I)) for name, pat in MEDIUM]

# Invisible / direction-flipping characters often used to hide injected text
# (including "Trojan Source" bidi tricks). Source note: every such character is
# written as a \u escape here, never as a literal.
#
# Always suspicious: zero-width space/joiners U+200B-D, bidi embeddings and
# overrides U+202A-E, word joiner and invisible operators U+2060-4, BOM U+FEFF
# (a BOM at the very start of a file is skipped by hidden_codepoints), soft
# hyphen U+00AD, Arabic letter mark U+061C, tag characters U+E0000-7F, any
# supplementary variation selector U+E0100-1EF, and runs of 2+ variation
# selectors (a single U+FE0F after an emoji is normal).
HIDDEN_ALWAYS = re.compile(
    "[\u200b-\u200d\u202a-\u202e\u2060-\u2064\ufeff\u00ad\u061c"
    "\U000e0000-\U000e007f\U000e0100-\U000e01ef]"
    "|[\ufe00-\ufe0f\U000e0100-\U000e01ef]{2,}")
# Hebrew/Arabic prose legitimately uses LRM/RLM (U+200E/F) and bidi isolates
# (U+2066-9): flag them only on a line without Hebrew/Arabic letters, or when
# a single line carries more than HIDDEN_BIDI_MAX of them.
HIDDEN_BIDI = re.compile("[\u200e\u200f\u2066-\u2069]")
RTL_LETTERS = re.compile("[\u0590-\u08ff]")
HIDDEN_BIDI_MAX = 20


def hidden_codepoints(line, first_line=False):
    """Sorted 'U+XXXX' strings of suspicious invisible characters on one line
    (empty list if none). first_line=True ignores a BOM at the very start."""
    if first_line and line.startswith("\ufeff"):
        line = line[1:]
    found = set()
    for m in HIDDEN_ALWAYS.finditer(line):
        found.update(m.group())
    bidi = HIDDEN_BIDI.findall(line)
    if bidi and (len(bidi) > HIDDEN_BIDI_MAX or not RTL_LETTERS.search(line)):
        found.update(bidi)
    return [f"U+{ord(c):04X}" for c in sorted(found)]


def short(path):
    """Display form of a path: HOME prefix replaced by ~."""
    if path == HOME:
        return "~"
    if path.startswith(HOME + os.sep):
        return "~" + path[len(HOME):]
    return path


def in_security_dir(path):
    path = os.path.abspath(path)
    return path == SECURITY_DIR or path.startswith(SECURITY_DIR + os.sep)


class SettingsError(Exception):
    """settings.toml exists but is unusable. run() prints it as
    'SCAN_ERROR: settings.toml: <reason>' and exits 3."""


def read_settings():
    """Parsed settings.toml as a dict ({} if the file does not exist). Raises
    SettingsError if it exists but cannot be read or parsed."""
    try:
        raw = read_capped(SETTINGS_PATH, MAX_STRUCT_BYTES)
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except OSError as e:
        raise SettingsError("cannot read: %s" % (e.strerror or type(e).__name__)) from None
    if raw is None:
        raise SettingsError("not a regular file")
    if len(raw) > MAX_STRUCT_BYTES:
        raise SettingsError("file too large")
    try:
        return tomllib.loads(raw.decode("utf-8-sig"))
    except (ValueError, RecursionError) as e:   # TOML and Unicode errors are ValueErrors
        raise SettingsError("cannot parse: %s" % _error_text(e)) from None


def expand_root(text):
    """Absolute, normalized folder for one configured entry. '~' and '~/x' are
    relative to HOME (not the real home, so SCAN_HOME tests work), other relative
    paths are relative to HOME too, absolute paths stay as they are."""
    if text == "~" or text.startswith(("~/", "~" + os.sep)):
        text = text[1:].lstrip("/" + os.sep)
    return os.path.normpath(os.path.join(HOME, text))


def project_roots():
    """The folders whose projects are scanned: SCAN_PROJECT_ROOTS if set, else
    settings.toml project_roots, else the defaults. Only existing folders are
    kept, duplicates dropped. Resolved once. Raises SettingsError."""
    global PROJECT_ROOTS
    if PROJECT_ROOTS is not None:
        return PROJECT_ROOTS
    env = os.environ.get("SCAN_PROJECT_ROOTS")
    if env:
        wanted = [p for p in env.split(os.pathsep) if p.strip()]
    else:
        value = read_settings().get("project_roots")
        if value is None:
            wanted = ["~/" + d for d in DEFAULT_PROJECT_DIRS]
        elif not (isinstance(value, list) and all(isinstance(p, str) for p in value)):
            raise SettingsError("project_roots must be a list of strings")
        elif not all(p.strip() for p in value):
            raise SettingsError("project_roots must not contain empty strings")
        else:
            wanted = value
    roots = []
    for entry in wanted:
        full = expand_root(entry)
        if os.path.isdir(full) and full not in roots:
            roots.append(full)
    PROJECT_ROOTS = roots
    return roots


def project_roots_line():
    roots = project_roots()
    shown = ", ".join(short(r) for r in roots) if roots else "(none found)"
    return "Project roots: " + one_line(shown, limit=1000)


def rel_depth(path, root):
    """How many folders deep path is below root (root itself is 0)."""
    rel = os.path.relpath(path, root)
    return 0 if rel == "." else rel.count(os.sep) + 1


def iter_files():
    seen = set()

    def walk(path):
        if in_security_dir(path):
            return
        if os.path.isfile(path):
            yield path
            return
        for dp, dns, fns in os.walk(path):
            dns[:] = [d for d in dns if d not in SKIP_DIRS
                      and not in_security_dir(os.path.join(dp, d))]
            for fn in fns:
                yield os.path.join(dp, fn)

    for r in ROOTS:
        if os.path.exists(r):
            for f in walk(r):
                yield f
    # Overlapping roots may yield a file twice; scan() skips repeats by path.
    for root in project_roots():
        for dp, dns, fns in os.walk(root):
            depth = rel_depth(dp, root)
            keep = []
            for d in dns:
                if d in PROJECT_DIRS:
                    for f in walk(os.path.join(dp, d)):
                        yield f
                elif d not in SKIP_DIRS and not d.startswith(".") and depth < 4:
                    keep.append(d)
            dns[:] = keep
            for fn in fns:
                if fn in PROJECT_FILES:
                    yield os.path.join(dp, fn)


SEVERITIES = ("HIGH", "HIDDEN", "MEDIUM")
CATEGORIES = ("hooks", "mcp", "plugins", "skills", "autorun_files", "persistence")


def sha(data):
    if isinstance(data, str):
        data = data.encode("utf-8", "backslashreplace")
    return hashlib.sha256(data).hexdigest()


def finding_key(severity, rule, path, text):
    """Stable identity of a hit. No line number, so shifting lines is not 'new'."""
    return sha(f"{severity}|{rule}|{short(path)}|{text}")


NONREGULAR = "NONREGULAR"   # inventory marker for FIFOs, devices, sockets (and symlinks to them)


def open_regular(path):
    """Open path read-only (symlinks followed) if it resolves to a regular file.
    Returns a raw fd, or None if the target is not a regular file (a FIFO would
    block open(), /dev/zero never ends). OSError propagates as from os.stat/open.
    O_NONBLOCK keeps open() from hanging if a FIFO is swapped in after the stat,
    and the fstat re-check closes that same window."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        return None
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_NOCTTY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_capped(path, limit):
    """Read at most limit + 1 bytes of a regular file, never trusting its
    reported size (len(result) > limit means 'larger than limit'). Returns None
    if path is not a regular file. OSError propagates."""
    fd = open_regular(path)
    if fd is None:
        return None
    with os.fdopen(fd, "rb") as fh:
        return fh.read(limit + 1)


def scan():
    """Run the regex scan. Returns (files_scanned, results); results maps
    severity -> list of (rule, path, lineno, display_text, key)."""
    me = os.path.abspath(__file__)
    results = {"HIGH": [], "MEDIUM": [], "HIDDEN": []}
    scanned = 0
    seen = set()
    for f in iter_files():
        f = os.path.abspath(f)
        if f in seen or f == me or in_security_dir(f):
            continue
        seen.add(f)
        ext = os.path.splitext(f)[1].lower()
        if ext not in TEXT_EXT:
            continue
        try:
            data = read_capped(f, MAX_BYTES)
        except OSError:
            continue
        if data is None or len(data) > MAX_BYTES:
            continue   # not a regular file, or too big to scan
        # Same newline handling as open(f, encoding="utf-8", errors="replace").
        with io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        scanned += 1
        for i, line in enumerate(lines, 1):
            text = line.strip()   # the key hashes the full stripped line
            if len(line) > LONG_LINE:
                # Pathological line: no regex rules, but never ignore it silently.
                shown = f"{len(text)} chars, regex rules skipped: {text[:80]}"
                results["MEDIUM"].append(
                    ("long-line", f, i, shown, finding_key("MEDIUM", "long-line", f, text)))
            else:
                for name, rx in _HIGH_RE:
                    if rx.search(line):
                        results["HIGH"].append(
                            (name, f, i, text[:220], finding_key("HIGH", name, f, text)))
                for name, rx in _MEDIUM_RE:
                    if rx.search(line):
                        results["MEDIUM"].append(
                            (name, f, i, text[:220], finding_key("MEDIUM", name, f, text)))
            cps = hidden_codepoints(line, first_line=(i == 1))
            if cps:
                name = "hidden-chars " + ",".join(cps)
                # Display text stays empty (as before); the key still hashes the line.
                results["HIDDEN"].append(
                    (name, f, i, "", finding_key("HIDDEN", name, f, text)))
    return scanned, results


# Secrets that may sit inside a matched line (e.g. a bot token in a permission
# rule). Masked in everything the script prints; keys still hash the raw line.
#
# Convention: if a pattern has a capture group, group 1 is the part to keep
# (the label or URL prefix) and everything the pattern matches after it becomes
# "***"; without groups the whole match becomes "***". All patterns are anchored
# on a literal or a lookbehind and use bounded or run-limited quantifiers, so
# they stay linear on very long input (hook commands are redacted unbounded).
SECRET_PATTERNS = [
    re.compile(r"(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{30,}"),                # Telegram bot token (often after "bot")
    re.compile(r"\b(?:sk-ant-|sk-|gh[pousr]_|github_pat_|xox[abprs]-|hf_|glpat-)[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{30,}"),        # AWS / Google keys
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|bearer)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{8,}"),
    # "Authorization: Bearer <tok>" (also Basic / Token schemes or a bare value).
    re.compile(r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?(?:(?:bearer|basic|token)\s+)?)[^\s\"',;]{8,}"),
    # "Bearer <tok>" with a space separator and no Authorization label.
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    # Env-style assignments: AWS_SECRET_ACCESS_KEY=..., GITHUB_TOKEN=..., FOO_KEY="...".
    # Upper-case names only (so ordinary prose like "key=value" is left alone).
    re.compile(r"(\b[A-Z][A-Z0-9_]{0,60}(?:SECRET|TOKEN|KEY|PASSWORD|PASSWD)[A-Z0-9_]{0,60}"
               r"(?:\s*=\s*[\"']?|[\"']\s*:\s*[\"']?))[^\s\"',;]{4,}"),
    re.compile(r"(?<![A-Za-z0-9])(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}"),  # Stripe
    # URL credentials scheme://user:pass@host -> mask the password (may contain '@').
    re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]{0,20}://[^\s:/@]+:)[^\s/]+(?=@)"),
    # CLI flags: --token X, --api-key=X, --password X, --client-secret X ...
    re.compile(r"(?i)(--(?:[a-z]{1,20}-)?(?:token|api[_-]?key|password|passwd|secret)(?:=|\s+)[\"']?)[^\s\"']{4,}"),
    # Discord / Slack incoming-webhook URLs: the path after the prefix is the secret.
    re.compile(r"(?i)((?:/api/webhooks/|hooks\.slack\.com/services/))[^\s\"'<>)\]]+"),
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),   # JWT
    re.compile(r"(?<![A-Za-z0-9_-])ya29\.[A-Za-z0-9_-]{16,}"),                          # Google OAuth token
]


def redact(text):
    for pat in SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "***", text)
    return text


def print_rows(rows):
    for name, f, i, txt, _key in rows:
        print(f"[{name}] {short(f)}:{i}  {redact(txt)}")


def print_report(scanned, results):
    print(f"Scanned {scanned} text files.")
    print(project_roots_line())
    print()
    for sev in SEVERITIES:
        rows = results[sev]
        print(f"===== {sev}: {len(rows)} hits =====")
        print_rows(rows)
        print()


# ---------------------------------------------------------------------------
# Inventory: hashes of everything that can run or steer an agent automatically.
# ---------------------------------------------------------------------------

# Files looked for under every project root: pattern -> max depth (relative to
# that root) of the directory that contains the matched file.
PROJECT_PATTERNS = {
    ".claude/settings.json": 5,
    ".claude/settings.local.json": 5,
    ".gemini/settings.json": 5,
    ".qoder/settings.json": 5,
    ".mcp.json": 5,
    ".claude/model-router/*.py": 6,
    ".gemini/hooks/*": 6,
}

_LOADED = {}


def load_structured(path):
    """Parse a JSON (or .toml) file. Returns (state, data, digest) where state is
    'missing' (silently skipped), 'ok' or 'error' (digest = hash of what we saw)."""
    if path in _LOADED:
        return _LOADED[path]
    try:
        raw = read_capped(path, MAX_STRUCT_BYTES)
    except (FileNotFoundError, NotADirectoryError):
        res = ("missing", None, None)
    except OSError as e:
        res = ("error", None, sha(f"OSError:{e.errno}"))
    else:
        if raw is None:   # FIFO, device, socket: never read it, but make it visible
            res = ("error", None, sha(NONREGULAR))
        elif len(raw) > MAX_STRUCT_BYTES:
            res = ("error", None, sha(b"TOOBIG:" + raw[:MAX_STRUCT_BYTES]))
        else:
            try:
                text = raw.decode("utf-8-sig")
                if path.endswith(".toml"):
                    if tomllib is None:
                        raise RuntimeError("Python 3.11+ is required to read TOML")
                    data = tomllib.loads(text)
                else:
                    data = json.loads(text)
                res = ("ok", data, None)
            except (ValueError, RecursionError):   # JSON/TOML/Unicode errors are ValueErrors
                res = ("error", None, sha(raw))
    _LOADED[path] = res
    return res


def file_digest(path):
    """sha256 of a regular file's bytes, streamed; None if unreadable; the fixed
    marker NONREGULAR for FIFOs, devices, sockets and symlinks to them."""
    h = hashlib.sha256()
    try:
        fd = open_regular(path)
        if fd is None:
            return NONREGULAR
        with os.fdopen(fd, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


# Directories whose content changes without anyone editing the tool itself:
# bytecode caches (rewritten by running a script), git metadata (rewritten by
# every commit when a skill is its own repo) and test caches.
DIGEST_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}


def tree_files(root, max_files=None, max_depth=None, links=False):
    """All files under root (or root itself if it is not a directory, so a FIFO or
    a symlink to a device still reaches file_digest and gets the NONREGULAR
    marker), skipping DIGEST_SKIP_DIRS. Sorted, so a cut-off is deterministic.
    max_files stops after that many paths; max_depth is how many folders below
    root are entered (0 = only the files directly in root); links=True also
    yields symlinks to folders (os.walk does not follow them) so the caller can
    record them."""
    if not os.path.isdir(root):
        yield root
        return
    count = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in DIGEST_SKIP_DIRS)
        names = list(fns)
        if links:
            names += [d for d in dns if os.path.islink(os.path.join(dp, d))]
        if max_depth is not None and rel_depth(dp, root) >= max_depth:
            dns[:] = []
        for fn in sorted(names):
            if max_files is not None and count >= max_files:
                return
            count += 1
            yield os.path.join(dp, fn)


def project_find():
    """One bounded walk of each project root, depth measured from that root ->
    {pattern: sorted matching files}. Sets merge overlapping roots."""
    found = {p: set() for p in PROJECT_PATTERNS}
    descend_to = max(m - p.count("/") for p, m in PROJECT_PATTERNS.items())
    for root in project_roots():
        for dp, dns, _fns in os.walk(root):
            depth = rel_depth(dp, root)
            for pat, max_depth in PROJECT_PATTERNS.items():
                if depth + pat.count("/") > max_depth:
                    continue
                for m in glob.glob(os.path.join(glob.escape(dp), pat)):
                    if os.path.isfile(m):
                        found[pat].add(m)
            dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")
                      and depth < descend_to]
    return {p: sorted(v) for p, v in found.items()}


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)


def _command_strings(node):
    """Every string value under a key named 'command', at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "command":
                yield from _strings(v)
            else:
                yield from _command_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _command_strings(v)


def _canon(value):
    return json.dumps(value, sort_keys=True, default=str)


def _tree_digest(root):
    """Hash of every file (relative path + content) under root, in sorted order,
    so edits, additions, removals and renames all change it."""
    h = hashlib.sha256()
    entries = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in DIGEST_SKIP_DIRS]
        for d in dns:
            full = os.path.join(dp, d)
            if os.path.islink(full):   # os.walk does not follow it; record the target
                entries.append((os.path.relpath(full, root), "L:" + os.readlink(full)))
        for fn in fns:
            full = os.path.join(dp, fn)
            entries.append((os.path.relpath(full, root), file_digest(full) or "UNREADABLE"))
    for rel, digest in sorted(entries):
        h.update(rel.encode("utf-8", "backslashreplace") + b"\0")
        h.update(digest.encode("ascii", "backslashreplace") + b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Persistence inventory: places where code runs on its own (shell startup,
# services, cron, SSH, loader, sudoers, git and package-manager config). Files
# are hashed only: never regex-scanned, read into a report or printed, because
# they often hold secrets (exported API keys, registry tokens).
# ---------------------------------------------------------------------------

PERSIST_MAX_FILES = 500   # most paths taken from any one directory walk
PERSIST_MAX_DEPTH = 4     # folders entered below a walked directory

# (relative path, kind[, name suffix]). Kinds:
#   file   one file
#   flat   the files directly inside the folder (only names ending in the suffix, if given)
#   tree   the folder walked to PERSIST_MAX_DEPTH; symlinks to files are followed,
#          symlinks to folders are recorded by target
#   links  like tree, but every symlink is recorded by its target ("L:<target>"
#          is hashed) instead of followed: /etc/systemd is full of *.wants links
#          into /usr/lib, which would otherwise change with every package update
PERSIST_HOME = (
    # shell startup
    (".bashrc", "file"), (".bash_profile", "file"), (".bash_login", "file"),
    (".bash_logout", "file"), (".profile", "file"), (".zshrc", "file"),
    (".zprofile", "file"), (".zshenv", "file"), (".zlogin", "file"),
    (".xprofile", "file"), (".xinitrc", "file"), (".xsessionrc", "file"),
    (".config/fish/config.fish", "file"), (".config/fish/conf.d", "flat"),
    # services and autostart
    (".config/systemd/user", "tree"), (".local/share/systemd/user", "tree"),
    (".config/autostart", "flat", ".desktop"),
    # SSH
    (".ssh/authorized_keys", "file"), (".ssh/authorized_keys2", "file"),
    (".ssh/config", "file"), (".ssh/rc", "file"), (".ssh/environment", "file"),
    # git
    (".gitconfig", "file"), (".config/git/config", "file"),
    (".config/git/attributes", "file"),
    # package managers
    (".npmrc", "file"), (".yarnrc", "file"), (".yarnrc.yml", "file"),
    (".config/pip/pip.conf", "file"), (".pip/pip.conf", "file"),
    (".pydistutils.cfg", "file"), (".cargo/config.toml", "file"),
    (".cargo/config", "file"),
)
PERSIST_SYSTEM = (   # relative to SYSTEM_ROOT
    # shell startup
    ("etc/profile", "file"), ("etc/profile.d", "flat"), ("etc/bash.bashrc", "file"),
    ("etc/environment", "file"), ("etc/zsh/zshrc", "file"), ("etc/zsh/zshenv", "file"),
    # services and autostart
    ("etc/systemd/system", "links"), ("etc/systemd/user", "links"),
    ("etc/xdg/autostart", "flat", ".desktop"),
    # cron (the per-user spool file is added in persistence_sources)
    ("etc/crontab", "file"), ("etc/cron.d", "flat"), ("etc/cron.hourly", "flat"),
    ("etc/cron.daily", "flat"), ("etc/cron.weekly", "flat"),
    ("etc/cron.monthly", "flat"), ("etc/anacrontab", "file"),
    # dynamic loader
    ("etc/ld.so.preload", "file"), ("etc/ld.so.conf", "file"), ("etc/ld.so.conf.d", "flat"),
    # sudoers (mostly unreadable without root: skipped silently)
    ("etc/sudoers.d", "flat"),
)


def persistence_user():
    """Name of the current user, or None if it cannot be determined."""
    try:
        return getpass.getuser() or None
    except (KeyError, OSError, ImportError):   # no passwd entry for this uid
        return None


def persistence_sources():
    """[(absolute path, kind, suffix)]: HOME ones under HOME, system ones under
    SYSTEM_ROOT."""
    out = []
    for base, table in ((HOME, PERSIST_HOME), (SYSTEM_ROOT, PERSIST_SYSTEM)):
        for rel, kind, *rest in table:
            out.append((os.path.join(base, rel), kind, rest[0] if rest else None))
    user = persistence_user()
    if user and "/" not in user and user not in (".", ".."):
        out.append((os.path.join(SYSTEM_ROOT, "var/spool/cron/crontabs", user), "file", None))
    return out


SYSTEM_BIN_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def crontab_digest():
    """sha256 of the current user's own crontab (`crontab -l`), or None when there
    is no crontab program. This is the ONLY place the script runs another program.
    It is needed because per-user crontabs live in a root-owned spool folder that
    an ordinary user cannot read, so asking crontab is the only way to see them.
    The call is a fixed argument list (no shell), has no stdin, a 5 s timeout and
    an environment of just PATH and LANG=C, and only a hash of the output is kept
    (a crontab can hold secrets). Digest: sha256 of stdout on success; of the fixed
    string NO_CRONTAB when the user has no crontab; of CRONTAB_ERROR on any other
    failure or timeout."""
    # Only system bin folders: a `crontab` planted earlier on the user's PATH (for
    # example in ~/.local/bin) must never be what this security check runs.
    crontab = shutil.which("crontab", path=SYSTEM_BIN_PATH)
    if not crontab:
        return None
    try:
        proc = subprocess.run(
            [crontab, "-l"], shell=False, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=5, env={"PATH": SYSTEM_BIN_PATH, "LANG": "C"})
    except (OSError, subprocess.SubprocessError, ValueError):   # includes TimeoutExpired
        return sha("CRONTAB_ERROR")
    if proc.returncode == 0:
        return sha(proc.stdout)
    err = proc.stderr or b""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    if "no crontab" in err.lower():
        return sha("NO_CRONTAB")
    return sha("CRONTAB_ERROR")


def _persist_value(path, all_links):
    """Hash for one walked path: sha256 of "L:<target>" for a symlink recorded by
    target (every symlink if all_links, else only symlinks to folders), otherwise
    file_digest(). None means skip (unreadable or dangling)."""
    try:
        if os.path.islink(path) and (all_links or os.path.isdir(path)):
            return sha("L:" + os.readlink(path))
    except OSError:
        return None
    return file_digest(path)


def persistence_items():
    """-> {path or name: hash} for the persistence category. Missing and
    unreadable paths are skipped without a trace. Nothing read here is returned
    or printed except the path and a hash."""
    items = {}
    for path, kind, suffix in persistence_sources():
        if kind == "file":
            digest = file_digest(path)   # None: missing or unreadable
            if digest is not None:
                items[short(path)] = digest
            continue
        if not os.path.isdir(path):
            continue
        depth = 0 if kind == "flat" else PERSIST_MAX_DEPTH
        seen = 0
        # One more than the cap, so hitting the cap is noticed rather than silent.
        for fp in tree_files(path, max_files=PERSIST_MAX_FILES + 1,
                             max_depth=depth, links=(kind != "flat")):
            seen += 1
            if seen > PERSIST_MAX_FILES:
                items[f"{short(path)} :: TRUNCATED"] = sha("TRUNCATED")
                break
            if suffix and not fp.endswith(suffix):
                continue
            digest = _persist_value(fp, all_links=(kind == "links"))
            if digest is not None:
                items[short(fp)] = digest
    if not os.environ.get("SCAN_NO_CRONTAB"):
        digest = crontab_digest()
        if digest is not None:
            items[f"crontab -l ({persistence_user() or '?'})"] = digest
    return items


def collect_inventory():
    """-> {category: {item_name: sha256_hex}}. Item names are for display and may
    contain hook commands; use storable_inventory() before writing to disk."""
    inv = {c: {} for c in CATEGORIES}
    proj = project_find()

    def parse_error(cat, path, digest):
        inv[cat][f"{short(path)} :: PARSE_ERROR"] = digest

    # ---- hooks ----
    hook_files = [f"{HOME}/.claude/settings.json", f"{HOME}/.claude/settings.local.json"]
    for pat in (".claude/settings.json", ".claude/settings.local.json",
                ".gemini/settings.json", ".qoder/settings.json"):
        hook_files += proj[pat]
    hook_files += [f"{HOME}/.codex/hooks.json", f"{HOME}/.cursor/hooks.json",
                   f"{HOME}/.gemini/settings.json", f"{HOME}/.qoder/settings.json"]
    hook_files += sorted(glob.glob(f"{HOME}/.claude/plugins/cache/*/*/*/hooks/hooks.json"))
    mod_hook_files = sorted(glob.glob(f"{HOME}/.claude/mods/*/hooks/hooks.json"))
    for path in dict.fromkeys(hook_files + mod_hook_files):
        state, data, digest = load_structured(path)
        if state == "missing":
            continue
        if state == "error":
            parse_error("hooks", path, digest)
            continue
        if not isinstance(data, dict):
            continue
        cmds = list(_command_strings(data.get("hooks")))
        if path in mod_hook_files:
            mods = data.get("modules")
            if isinstance(mods, list):
                cmds += [m if isinstance(m, str) else _canon(m) for m in mods]
        for cmd in cmds:
            inv["hooks"][f"{short(path)} :: {cmd}"] = sha(cmd)

    # ---- mcp ----
    def add_servers(path, servers, scope=""):
        if isinstance(servers, dict):
            for name, cfg in servers.items():
                inv["mcp"][f"{short(path)} :: {scope}{name}"] = sha(_canon(cfg))
        elif servers is not None:
            inv["mcp"][f"{short(path)} :: {scope}mcpServers"] = sha(_canon(servers))

    mcp_files = [f"{HOME}/.config/Claude/claude_desktop_config.json",
                 f"{HOME}/.cursor/mcp.json", f"{HOME}/.gemini/settings.json"]
    mcp_files += proj[".mcp.json"] + proj[".gemini/settings.json"]
    claude_json = f"{HOME}/.claude.json"
    state, data, digest = load_structured(claude_json)
    if state == "error":
        parse_error("mcp", claude_json, digest)
    elif state == "ok" and isinstance(data, dict):
        add_servers(claude_json, data.get("mcpServers"))
        projects = data.get("projects")
        if isinstance(projects, dict):
            for pkey, pval in projects.items():
                if isinstance(pval, dict):
                    add_servers(claude_json, pval.get("mcpServers"),
                                scope=f"projects[{short(pkey)}] :: ")
    for path in dict.fromkeys(mcp_files):
        state, data, digest = load_structured(path)
        if state == "error":
            parse_error("mcp", path, digest)
        elif state == "ok" and isinstance(data, dict):
            add_servers(path, data.get("mcpServers"))
    codex_toml = f"{HOME}/.codex/config.toml"
    state, data, digest = load_structured(codex_toml)
    if state == "error":
        parse_error("mcp", codex_toml, digest)
    elif state == "ok" and isinstance(data, dict):
        add_servers(codex_toml, data.get("mcp_servers"))

    # ---- plugins ----
    installed = f"{HOME}/.claude/plugins/installed_plugins.json"
    state, data, digest = load_structured(installed)
    if state == "error":
        parse_error("plugins", installed, digest)
    elif state == "ok" and isinstance(data, dict) and isinstance(data.get("plugins"), dict):
        for name, entry in data["plugins"].items():
            entries = entry if isinstance(entry, list) else [entry]
            parts = []
            for e in entries:
                if isinstance(e, dict):
                    parts.append(f"{e.get('version', '')}|{e.get('gitCommitSha', '')}")
                else:
                    parts.append(str(e))
            inv["plugins"][name] = sha(";".join(parts))
    settings = f"{HOME}/.claude/settings.json"
    state, data, digest = load_structured(settings)
    if state == "error":
        parse_error("plugins", settings, digest)
    elif state == "ok" and isinstance(data, dict) and isinstance(data.get("enabledPlugins"), dict):
        for name, value in data["enabledPlugins"].items():
            inv["plugins"][f"enabled :: {name}"] = sha(str(value))

    # ---- skills ----
    skills_root = f"{HOME}/.claude/skills"
    if os.path.isdir(skills_root):
        for name in sorted(os.listdir(skills_root)):
            full = os.path.join(skills_root, name)
            if os.path.isdir(full):
                inv["skills"][name] = _tree_digest(full)

    # ---- autorun_files ----
    roots = [f"{HOME}/.claude/hooks", f"{HOME}/.cursor/hooks", f"{HOME}/.config/opencode/plugins"]
    # cross-model profiles are sourced as shell code by its runner; hashed only, never
    # regex-scanned or printed, because they hold API keys.
    roots += [f"{HOME}/.config/cross-model/profiles"]
    roots += glob.glob(f"{HOME}/.claude/plugins/cache/*/*/*/hooks")
    roots += glob.glob(f"{HOME}/.claude/mods/*/hooks")
    roots += glob.glob(f"{HOME}/.claude/scheduled-tasks/*/SKILL.md")
    roots += proj[".claude/model-router/*.py"] + proj[".gemini/hooks/*"]
    # The settings file decides which project folders are scanned: hashed, so a
    # change that drops a folder shows up in the weekly check.
    roots += [SETTINGS_PATH]
    for root in sorted(set(roots)):
        if not os.path.exists(root):
            continue
        for fp in tree_files(root):
            inv["autorun_files"][short(fp)] = file_digest(fp) or sha("UNREADABLE")

    # ---- persistence ---- (hash-only; see persistence_items)
    inv["persistence"].update(persistence_items())

    return inv


def storable_inventory(inv):
    """Copy of the inventory that is safe to write to disk. Hook item names embed
    the raw command (which may hold secrets), so they are replaced by
    '<path> :: sha256:<hash prefix>'. Returns (stored, display) where display
    maps stored name -> readable name for the current state."""
    stored, display = {}, {}
    for cat, items in inv.items():
        stored[cat], display[cat] = {}, {}
        for name, value in items.items():
            key = name
            if cat == "hooks":
                path, _, cmd = name.partition(" :: ")
                if cmd != "PARSE_ERROR":
                    key = f"{path} :: sha256:{value[:16]}"
            stored[cat][key] = value
            display[cat][key] = name
    return stored, display


# ---------------------------------------------------------------------------
# Baseline handling and CLI
# ---------------------------------------------------------------------------

def one_line(text, limit=300):
    text = redact(text.replace("\r", "\\r").replace("\n", "\\n"))
    return text if len(text) <= limit else text[:limit] + "..."


def write_baseline(doc):
    os.makedirs(SECURITY_DIR, mode=0o700, exist_ok=True)
    tmp = BASELINE_PATH + ".tmp"
    try:
        os.unlink(tmp)   # a stale (or planted) temp file or symlink; never write through it
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, BASELINE_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cmd_update_baseline():
    scanned, results = scan()
    keys = sorted({row[4] for sev in SEVERITIES for row in results[sev]})
    stored, _display = storable_inventory(collect_inventory())
    write_baseline({
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "findings": keys,
        "inventory": stored,
    })
    print(f"Scanned {scanned} text files.")
    print(f"findings: {len(keys)}")
    for cat in CATEGORIES:
        print(f"{cat}: {len(stored[cat])}")
    print(f"Baseline written: {short(BASELINE_PATH)}")
    print("BASELINE_UPDATED")
    return 0


def cmd_check():
    try:
        raw = read_capped(BASELINE_PATH, MAX_STRUCT_BYTES)
        if raw is None:
            print("BASELINE_UNREADABLE: not a regular file; run with --update-baseline")
            return 2
        if len(raw) > MAX_STRUCT_BYTES:
            print("BASELINE_UNREADABLE: file too large; run with --update-baseline")
            return 2
        base = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        print("NO_BASELINE: run with --update-baseline")
        return 2
    except (OSError, ValueError, RecursionError) as e:
        print(f"BASELINE_UNREADABLE: {type(e).__name__}; run with --update-baseline")
        return 2
    if not (isinstance(base, dict) and isinstance(base.get("findings"), list)
            and isinstance(base.get("inventory"), dict)):
        print("BASELINE_UNREADABLE: unexpected format; run with --update-baseline")
        return 2

    scanned, results = scan()
    known = set(base["findings"])
    new = {sev: [r for r in results[sev] if r[4] not in known] for sev in SEVERITIES}

    stored, display = storable_inventory(collect_inventory())
    changes = {}
    for cat in CATEGORIES:
        old = base["inventory"].get(cat) or {}
        cur = stored[cat]
        lines = [f"ADDED {one_line(display[cat][k])}" for k in sorted(cur.keys() - old.keys())]
        lines += [f"REMOVED {one_line(k)}" for k in sorted(old.keys() - cur.keys())]
        lines += [f"CHANGED {one_line(display[cat][k])}"
                  for k in sorted(cur.keys() & old.keys()) if cur[k] != old[k]]
        if lines:
            changes[cat] = lines

    if not any(new.values()) and not changes:
        print("NO_NEW_FINDINGS")
        return 0

    print(f"Scanned {scanned} text files. Baseline created {base.get('created', '?')}.")
    print(project_roots_line())
    print()
    for sev in SEVERITIES:
        if new[sev]:
            print(f"===== NEW {sev}: {len(new[sev])} hits =====")
            print_rows(new[sev])
            print()
    if changes:
        print("===== INVENTORY CHANGES =====")
        for cat, lines in changes.items():
            print(f"--- {cat} ---")
            for line in lines:
                print(line)
        print()
    return 1


def main(argv=None):
    if sys.version_info < (3, 11) or tomllib is None:
        raise RuntimeError("Python 3.11+ is required (found %s)" % sys.version.split()[0])
    try:   # a planted file name that is not valid UTF-8 must not crash printing
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(
        description="Scan AI-agent instruction/config files for malware-install patterns. "
                    "Default: report only what is new since the approved baseline "
                    "(exit 0 = nothing new, 1 = new items, 2 = no/unreadable baseline, "
                    "3 = scanner error).")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true",
                      help="print the full report of every regex hit")
    mode.add_argument("--update-baseline", action="store_true",
                      help="approve the current state and write baseline.json "
                           "(item names in clear text, hashes for all values)")
    args = ap.parse_args(argv)
    if os.environ.get("SCAN_HOME"):
        print(f"SCAN_HOME={HOME}")
    project_roots()   # a broken settings.toml fails here, before any report output
    if args.all:
        print_report(*scan())
        return 0
    if args.update_baseline:
        return cmd_update_baseline()
    return cmd_check()


def _discard_stdout():
    """After a closed pipe (`... --all | head -3`), send the rest of stdout to
    /dev/null so the interpreter's exit-time flush stays quiet."""
    os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


def _error_text(exc):
    text = " ".join(str(exc)[:2000].split())
    try:
        text = redact(text)
    except Exception:
        pass
    return text[:300]


def run():
    """main() with exit-code discipline: any unexpected exception becomes
    'SCAN_ERROR: <Type>: <message>' and exit 3. Exit 1 is reserved for 'new
    findings', so a crash must never look like one."""
    try:
        code = main()
        sys.stdout.flush()
        return code
    except BrokenPipeError:   # e.g. `... --all | head -3`
        _discard_stdout()
        return 0
    except (Exception, KeyboardInterrupt) as exc:   # SystemExit (argparse, exit 2) passes through
        try:
            if isinstance(exc, SettingsError):
                print(f"SCAN_ERROR: settings.toml: {exc}")
            else:
                print(f"SCAN_ERROR: {type(exc).__name__}: {_error_text(exc)}")
            sys.stdout.flush()
        except BrokenPipeError:
            _discard_stdout()
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(run())
