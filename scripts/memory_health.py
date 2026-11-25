#!/usr/bin/env python3
"""Read-only health report for Claude Code auto-memory folders.

For every <root>/*/memory/ that contains a MEMORY.md it reports:
  1. orphans: *.md files that MEMORY.md never links to
  2. dead links: links in MEMORY.md whose target file does not exist
  3. index size: line count and bytes of MEMORY.md against two limits

Exit codes: 0 nothing flagged, 1 something flagged, 2 error.
Stdlib only. Never writes or deletes anything.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import unquote

# The part of MEMORY.md loaded at session start; verify against the current Claude Code docs.
MAX_INDEX_LINES = 200  # verify against the current Claude Code docs
MAX_INDEX_BYTES = 25 * 1024  # verify against the current Claude Code docs

INDEX = "MEMORY.md"
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]*)[^)]*\)")


def link_targets(text):
    """Local file targets of markdown links (URLs, mailto and pure anchors skipped)."""
    out = []
    for m in LINK_RE.finditer(text):
        t = m.group(1).strip().strip("<>")
        if not t or t.startswith("#") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", t):
            continue
        t = unquote(t.split("#", 1)[0].split("?", 1)[0])
        if t:
            out.append(os.path.normpath(t))
    return out


def check_folder(mem):
    with open(os.path.join(mem, INDEX), "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", errors="replace")
    lines = len(text.splitlines())
    size = len(raw)
    targets = link_targets(text)
    linked = set(targets)
    dead = sorted({t for t in targets if not os.path.isfile(os.path.join(mem, t))})
    orphans = sorted(n for n in os.listdir(mem)
                     if n.endswith(".md") and n != INDEX
                     and os.path.isfile(os.path.join(mem, n)) and n not in linked)
    flags = []
    if lines > MAX_INDEX_LINES:
        flags.append("index over %d lines" % MAX_INDEX_LINES)
    if size > MAX_INDEX_BYTES:
        flags.append("index over %d bytes" % MAX_INDEX_BYTES)
    return {"lines": lines, "bytes": size, "orphans": orphans, "dead_links": dead,
            "size_flags": flags}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="~/.claude/projects",
                    help="folder holding one sub-folder per project (default ~/.claude/projects)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    root = os.path.expanduser(args.root)
    if not os.path.isdir(root):
        print("error: root is not a directory: %s" % root, file=sys.stderr)
        return 2
    results = {}
    try:
        for name in sorted(os.listdir(root)):
            mem = os.path.join(root, name, "memory")
            if os.path.isfile(os.path.join(mem, INDEX)):
                results[name] = check_folder(mem)
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    flagged = [n for n, r in results.items()
               if r["orphans"] or r["dead_links"] or r["size_flags"]]
    if args.json:
        print(json.dumps({"limits": {"lines": MAX_INDEX_LINES, "bytes": MAX_INDEX_BYTES},
                          "projects": results, "flagged": flagged}, indent=2,
                         ensure_ascii=False))
    else:
        for name, r in results.items():
            mark = "FLAGGED" if name in flagged else "ok"
            print("%s  [%s]" % (name, mark))
            print("  index: %d lines, %d bytes%s" % (
                r["lines"], r["bytes"],
                ("  <-- " + "; ".join(r["size_flags"])) if r["size_flags"] else ""))
            print("  orphans (%d): %s" % (len(r["orphans"]), ", ".join(r["orphans"]) or "-"))
            print("  dead links (%d): %s" % (len(r["dead_links"]), ", ".join(r["dead_links"]) or "-"))
        print("SUMMARY: %d memory folders, %d flagged, %d orphans, %d dead links, "
              "%d oversized indexes" % (
                  len(results), len(flagged),
                  sum(len(r["orphans"]) for r in results.values()),
                  sum(len(r["dead_links"]) for r in results.values()),
                  sum(1 for r in results.values() if r["size_flags"])))
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
