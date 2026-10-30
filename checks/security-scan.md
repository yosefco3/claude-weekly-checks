---
name: security-scan
title: Security scan of agent instruction files
order: 10
frequency: weekly
enabled: true
writes:
  - ~/.claude/security/reports/
writes_on_apply:
  - ~/.claude/security/baseline.json
apply: ["approve baseline", "אשר בסיס"]
---

## Goal

Catch anything NEW in the user's AI-agent setup that tells an agent to download,
install or run malware, steal credentials or secrets, add persistence, send data out,
or hide actions from the user. Covered: CLAUDE.md / AGENTS.md / GEMINI.md, skills,
hooks, plugins, MCP servers and scheduled tasks for Claude, Codex, Cursor, Gemini and
opencode, in `~` and in the project roots from `settings.toml` (see SKILL.md). It also
inventories the places where planted code would run on its own (shell startup files,
user services and autostart entries, cron, SSH files, the loader preload list, git and
package-manager config), so a change there shows up even if no instruction mentions it.

The scanner compares the current state with a baseline the user approved. The baseline
(`~/.claude/security/baseline.json`, local only) stores item names (paths, skill and
MCP server names) and hashes, never matched lines, commands or configs.

Scanner: `~/.claude/skills/weekly-checks/scripts/scan_agent_instructions.py`
(`--all` prints every hit; the default prints only what is new; `--update-baseline`
approves the current state). Exit codes: 0 nothing new, 1 new items, 2 no or unreadable
baseline, 3 scanner error (including a broken `settings.toml`).

## Run

1. Run `python3 -I ~/.claude/skills/weekly-checks/scripts/scan_agent_instructions.py`.
   - Output starting with `SCAN_HOME=`: the scan ran against another folder because that
     variable is set. Status `attention`, verdict suspicious, and stop: the result says
     nothing about the real setup.
   - `NO_NEW_FINDINGS` (exit 0): status `ok`, summary "no new findings". Done.
   - `NO_BASELINE` or `BASELINE_UNREADABLE` (exit 2): status `attention`. The approved
     baseline is missing or damaged, which is suspicious if the user did not delete it.
     Tell the user to read the full report (`--all`) before approving a new baseline.
     Done.
   - `SCAN_ERROR` (exit 3), a missing script, or any other crash: status `failed`, with
     the error line. A missing or broken script can mean tampering, so say so. Done.
   - Exit 1 means new findings and/or inventory changes: continue.
2. For every NEW finding and every inventory change (ADDED and CHANGED; REMOVED only
   when it removes a safety measure):
   - Open the file at the reported path and line with the Read tool (read-only) and read
     enough context to judge it.
   - For a new or changed hook, MCP server, plugin or autorun file, read the command or
     script and say in plain words what it does. Does it access the network, download
     anything, or run code from the internet? Does it read `~/.ssh`, credentials, tokens
     or browser data? Does it write outside its own project (shell rc files, crontab,
     systemd, `~/.claude` settings)?
   - Verdict: malicious / suspicious / benign, with a one-line
     reason. These are usually benign: package installs inside a project's own venv,
     official installers from the tool's own vendor, docs that warn about prompt
     injection, and hidden Unicode that is only emoji joiners. Skills listed as CHANGED
     right after a skill-audit apply step are expected, but still say what changed.
   - An item under `--- persistence ---` is a place where code runs on its own: shell
     startup files, user services, timers and autostart entries, cron, SSH
     `authorized_keys` / `config` / `rc`, the dynamic-loader preload list, git config and
     package-manager config. This is what a malicious instruction would actually plant.
     The baseline holds only a hash, so you see the current file, not a diff: say so,
     and read the whole file (masking secrets). Look for downloads piped to a shell,
     `base64` / `eval`, PATH or `LD_PRELOAD` entries that point into home, `/tmp` or
     hidden folders, aliases or functions that wrap `sudo`, `ssh` or `git`, unknown
     `authorized_keys` entries, `ExecStart` or `Exec` lines that run hidden or temporary
     files, git aliases starting with `!`, `core.hooksPath` / `core.fsmonitor` /
     `core.sshCommand`, and package registry or index URLs that are not the official
     ones. A change the user made on purpose (a new alias, a tool's installer adding a
     PATH line) is benign; if you can't tell, call it suspicious and quote the lines.
   - `weekly-checks` itself (`CHANGED weekly-checks` under `--- skills ---`) is the
     security control, so never call a change to it benign by default. Run
     `git -C ~/.claude/skills/weekly-checks status --short` and
     `git -C ~/.claude/skills/weekly-checks log -p --since="<baseline date>"`.
     Uncommitted changes, or commits the user did not ask for, are suspicious. Committed
     changes are benign only if each commit message matches a change the user asked for;
     list the commits in the report. If the folder is not a git clone, say so and judge
     the change from the files themselves.
3. Write a report in the report language to `~/.claude/security/reports/<YYYY-MM-DD>.md`. Start with one
   summary line, then list each item with its severity, `path:line` in backticks, what it
   is, the verdict and the reason, malicious and suspicious items first. End with the
   apply command and a note that it should be run only after the user has reviewed the
   items.

## Result

- `attention` if anything is new. The summary line counts the items and names any
  malicious or suspicious ones, and the apply reply is the first phrase in `apply:` in
  the report language.
- `ok` and `failed` as in step 1.

## Apply

Only after the user has seen the report and asks for it in this conversation (a phrase
from `apply:`). Approving writes the state as it is right now, not the state in the report, so:

1. If any item was judged malicious or suspicious, first confirm that the user has dealt
   with it.
2. Run the default check again and compare its items with the ones in the report. If
   anything is new or different, list it, judge it as in step 2 of Run, and ask the user
   again. Do not approve until the two lists match.
3. Then run:

```bash
python3 -I ~/.claude/skills/weekly-checks/scripts/scan_agent_instructions.py --update-baseline
```

4. Run the default check once more and confirm that it prints `NO_NEW_FINDINGS`.
