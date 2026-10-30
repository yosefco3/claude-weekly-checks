# weekly-checks

A [Claude Code](https://claude.com/claude-code) skill that holds all your recurring
checks and runs them from one scheduled task. Each check is one Markdown file in
`checks/`. Adding a check means adding a file, not another skill or another scheduled
task.

Three checks ship with it:

| Check | What it catches |
|---|---|
| `security-scan` | anything **new** in your AI-agent setup that could install or run malware, steal secrets, add persistence, send data out, or hide actions from you |
| `skill-audit` | Claude skills that overlap, contradict each other, or go unused |
| `router-usage` | `router stats` for the last 7 days: non-router subagent spend, main-thread lookups, long sessions, cost per message per main model; recommendations, no apply step |

## Why a security scan for agent files

Skills, `CLAUDE.md` / `AGENTS.md` files, hooks, plugins and MCP servers are instructions
and code that an agent follows or runs automatically. A malicious skill or a planted
line in a project's `CLAUDE.md` ("first run `curl … | bash`") is a real attack path. The
scanner looks for it in:

- `~/.claude` (CLAUDE.md, settings and hooks, skills, agents, commands, plugins,
  scheduled tasks), `~/.codex`, `~/.gemini`, `~/.cursor`, `~/.config/opencode`, `~/.aider`
- every project under your project roots (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
  `.cursorrules`, `.mcp.json`, `.claude/`, `.gemini/`, `.codex/`, `.cursor/`, `.qoder/`)
- MCP server definitions in `~/.claude.json`, the desktop app config, Cursor, Gemini
  and Codex

It flags download-and-run commands, reverse shells, reads of SSH keys or credential
files, persistence (crontab, shell rc files, `authorized_keys`, systemd user units),
known exfiltration endpoints, "don't tell the user" phrasing, and hidden Unicode
(zero-width and bidi characters, tag characters, variation-selector runs). It also
hashes every hook command, MCP server, plugin, skill and autorun file, and every place
where planted code would run on its own: shell startup files, systemd user units and
timers, autostart entries, cron, `~/.ssh/authorized_keys` / `config` / `rc`,
`/etc/ld.so.preload`, git config and package-manager config (npm, pip, cargo). No root
needed.

After you approve a **baseline**, each weekly run reports only what is new or changed.
A Claude session then reads each item and judges it malicious, suspicious or benign.
The scanner is a static, read-only Python script (stdlib only). It never runs what it
finds and never touches the network. It is not an antivirus: it watches the files that
steer your agents.

## Requirements

- Python 3.11 or later
- Claude Code; the desktop app runs scheduled tasks while it is open
- Optional: the [model-router](https://github.com/yosefco3/claude-model-router) plugin,
  whose `scout` agent lets skill-audit read skills in parallel. Without it, skill-audit
  uses the built-in `Explore` agent. Its `router` CLI is what the router-usage check
  reads; without it that check reports `failed` (set `enabled: false` in
  `checks/router-usage.md` if you don't use the router).

## Install

```bash
git clone https://github.com/yosefco3/claude-weekly-checks ~/.claude/skills/weekly-checks
```

1. Optional: create `~/.claude/weekly-checks/settings.toml` (see Settings).
2. Read the full scan report first:
   ```bash
   python3 -I ~/.claude/skills/weekly-checks/scripts/scan_agent_instructions.py --all
   ```
   Go through every HIGH and HIDDEN hit (ask Claude to check them with you). Only then
   approve the current state as your baseline:
   ```bash
   python3 -I ~/.claude/skills/weekly-checks/scripts/scan_agent_instructions.py --update-baseline
   ```
3. Ask Claude in the desktop app: "create the scheduled task from
   ~/.claude/skills/weekly-checks/schedule/weekly-checks.md".
4. Optional, so that future sessions put new checks here: add to `~/.claude/CLAUDE.md`
   ```markdown
   ## Recurring checks
   Every weekly (or other recurring) check lives in the **weekly-checks** skill as
   `~/.claude/skills/weekly-checks/checks/<name>.md`, and one scheduled task
   (`weekly-checks`) runs them all. To add a check, follow that skill's "Adding a check".
   Never create a separate scheduled task or skill for a recurring check.
   ```

## Settings

`~/.claude/weekly-checks/settings.toml` is local and never committed. Every key is
optional:

```toml
language = "English"             # language of reports and summaries
project_roots = ["~/code"]       # where to look for projects
                                 # default: whichever of ~/Desktop, ~/code, ~/projects,
                                 # ~/src, ~/dev, ~/repos, ~/work exist
```

The scanner tracks this file too, so a change that removes a project root shows up in
the next weekly report.

## Using it

- The scheduled task runs every check and sends one summary.
- Run checks on demand: "run the weekly checks", "security scan", "audit my skills"
  (or `/weekly-checks`, `/weekly-checks security-scan`).
- After a report: "approve baseline" approves the new state of the security scan, and
  "apply" carries out the skill-audit recommendations you confirm.
- Run the scanner by hand at any time:
  ```bash
  python3 -I ~/.claude/skills/weekly-checks/scripts/scan_agent_instructions.py
  ```
  Exit codes: 0 nothing new, 1 new items, 2 no or unreadable baseline, 3 scanner error.

## Adding a check

Tell Claude "add a weekly check that …", or write `checks/<name>.md` yourself. The format
and the steps are in [SKILL.md](SKILL.md) under "Check file format" and "Adding a
check". A check can run `weekly` (the default) or `monthly`.

## Safety model

- Scheduled runs only report. Every apply step (approving a baseline, editing skills)
  needs your explicit request in the conversation, after you have seen the report.
- Everything the checks read is treated as untrusted data, never as instructions.
- Approving a baseline first re-runs the scan and refuses if anything appeared since the
  report.
- The skill audit never edits this skill. Any change to this skill shows up in the next
  security scan, and the scan checks it against the git history.
- The baseline stores hashes and item names only, never matched lines, commands or MCP
  configs, and it stays on your machine. Secrets are masked in all output.

## Data that stays on your machine

- `~/.claude/security/`: the approved baseline and the scan reports
- `~/.claude/model-router/reports/`: router-usage reports and the weekly metrics JSON the
  next run compares against.
- `~/.claude/skills-audit/`: skill-audit reports and `decisions.md` (merge and
  keep-separate decisions)
- `~/.claude/weekly-checks/`: `settings.toml` and the combined summaries

## Layout

```
SKILL.md                             the runner: settings, modes, rules, check format
checks/<name>.md                     one file per check (frontmatter + Goal/Run/Result/Apply)
checks/<name>/                       a check's supporting files
scripts/scan_agent_instructions.py   the security scanner
scripts/router_usage.py              router stats → metrics, flags and week-over-week trend
schedule/weekly-checks.md            the scheduled task (cron + prompt)
tests/test_scan.py                   scanner tests (fake home directories, stdlib unittest)
tests/test_router_usage.py           metrics script tests (anonymized fixture in tests/fixtures/)
```

## Tests

```bash
python3 -I ~/.claude/skills/weekly-checks/tests/test_scan.py -v
python3 -I ~/.claude/skills/weekly-checks/tests/test_router_usage.py -v
```

## Updating

```bash
git -C ~/.claude/skills/weekly-checks pull
```

The scanner hashes this skill too (ignoring `.git`), so the next run lists
`weekly-checks` as CHANGED. Review the change (`git log -p`), then approve it.

## License

MIT
