---
name: prompt-audit
title: Prompt audit of Claude Code configuration and Claude API prompts
order: 25
frequency: weekly
enabled: true
writes:
  - ~/.claude/prompt-audit/
writes_on_apply:
  - ~/.claude/CLAUDE.md
  - ~/.claude/skills/            # except weekly-checks/
  - ~/.claude/commands/
  - ~/.claude/agents/
  - ~/.claude/rules/
  - ~/.claude/output-styles/
  - instruction files of the projects under project_roots (CLAUDE.md, CLAUDE.local.md, AGENTS.md, .claude/)
apply: ["apply prompt fixes", "תקן פרומפטים"]
---

## Goal

The scheduled form of two commands:

- `/doctor prompt-audit`: audits the Claude Code configuration that loads into sessions
  (`CLAUDE.md` files, skills, commands, subagents, rules, output styles).
- `/claude-api prompt-audit`: audits a project that calls the Claude API (system
  prompts and the code that assembles them, tool descriptions, request code).

Both run one guide, `shared/prompt-audit.md` of the bundled `claude-api` skill.
`/doctor` cannot be invoked by the model, and all it adds is a scope paragraph, so this
check loads the skill directly and states the scope itself (pass A below is the
`/doctor` scope, pass B the `/claude-api` one).

What it catches: instructions written for older models (pressure language, scaffolds
that API features replaced, over-specified steps, workarounds for retired models),
facts the files have outgrown (paths, commands, model names), instruction files that
rule differently on the same point, tool descriptions that under-describe or steer,
and request settings that error or are deprecated on the target model.

How it differs from `skill-audit`: that check decides which skills should exist
(overlap, trigger collisions, merges, usage). This one reads the wording inside each
file and its fit to the current model. A contradiction between two skills or a dead
path that this run's skill-audit report already lists is mentioned by pointing to that
report, not repeated.

The report step writes only under `~/.claude/prompt-audit/`. It edits no audited file.

## Run

### 1. State and plan

`~/.claude/prompt-audit/decisions.md` records findings the user already settled (fixed,
or kept as they are). Create it with a `# Prompt audit decisions` heading if it is
missing. A finding it lists is not reported again unless the quoted text has changed.

The target model is `prompt_audit_model` in `settings.toml` when it is set, otherwise
the model this session runs on. Set the key to the model of your everyday sessions:
those are the sessions that read these files, and a scheduled run on another model
would otherwise re-audit everything each time the model differs from the last run's.

Run the planner from this skill's folder with that model id:

```
python3 -I scripts/prompt_surface.py plan --model <target model id> --json
```

It lists the files in scope and which are due: new or changed since the last finished
audit. Everything is due on the first run, when the model differs from the one the last
audit ran on (a prompt is tuned to a model, so a new model is a reason to re-audit), or
when the user asked for a full audit (add `--full`). It reads sizes and hashes only.

If nothing is due: no new report. Status `ok`, and the summary line says since when
nothing changed and how many findings are still open in the last report. Stop here.

### 2. Load the guide

Invoke the `claude-api` skill with the Skill tool, with these args:

> Run the `prompt-audit` subcommand from the Subcommands table: read
> `shared/prompt-audit.md` first and follow it in order. Scope, target model and output
> location are set by the weekly-checks check `prompt-audit` that invoked this; its
> request names every file in scope.

The skill's result names its base directory. Read `shared/prompt-audit.md` there in
full and follow its steps for each pass below. If the skill is not available (disabled,
or bundled skills are turned off), the check is `failed`: say that in one line.

Where the guide and this file differ on what may be read, written or run, this file and
"Rules for every run" in SKILL.md win. In particular: the guide's Step 7 probes that
would call a model or run a project's code are not run here; name them as the next step
for a contested finding.

### 3. Pass A: Claude Code configuration

Scope, named here because the guide audits user-level files only when the request names
them: the due files of the planner's `config` group. That group is
`~/.claude/CLAUDE.md`; `~/.claude/skills/*/SKILL.md`; everything under
`~/.claude/commands/`, `agents/`, `rules/` and `output-styles/`; and, for each project
under `project_roots`, its `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` and the same
kinds of file under its `.claude/`.

Target model: the one passed to the planner (step 1).

Not in scope: plugin skills, claude.ai account skills, the archive, and a skill's
reference files and scripts (open one only to verify a finding in the `SKILL.md` that
points to it). Never read settings files, hook definitions, `.mcp.json` or
`~/.claude.json`: they are not prompt text and can hold secrets. A file that is not due
may be opened to verify a conflict with a due file; it is not audited itself. For the
complete built-in scope, plugins included, the user can run `/doctor prompt-audit` in a
session.

### 4. Pass B: projects that call the Claude API

For each project the planner marks `due` in `apps` (it has an `anthropic` dependency and
its prompt or request files changed), run the guide with that project's directory as
the scope. Its instruction files belong to pass A. The target model is resolved per
project as the guide's Step 0 says. A project that also uses another provider gets that
recorded in the assumptions; never propose switching a file to the Anthropic SDK. Run
none of the project's code, tests or evals.

### 5. Split the reading when the plan is large

With more than about eight due files, or more than one due project, send the reading to
read-only subagents (`model-router:scout` with `model: sonnet`,
`run_in_background: true`; the `Explore` agent if that plugin is not installed), all
briefs in one message: one per batch of files (about 60 KB of text each) or per project,
using [prompt-audit/subagent-brief.md](prompt-audit/subagent-brief.md). When they report
back, open the quoted lines yourself and verify every medium- or high-confidence
finding before it goes in the report. The judgment is yours, not the subagents'.

### 6. Report

Drop findings that `decisions.md` settles. Then write, in the report language (quoted
evidence stays in its own language, paths and commands as they are):

- `~/.claude/prompt-audit/<YYYY-MM-DD>.md`: the assumptions (scope per pass, target
  model per pass, what was skipped), the two or three findings that matter most in
  prose, then the numbered findings ordered by confidence, each in the guide's Step 5
  shape (location `file:line`, quoted evidence, pattern, why it no longer fits,
  confidence, action), then counts per group.
- `~/.claude/prompt-audit/<YYYY-MM-DD>.diff`: the proposed edits, one hunk per finding,
  each headed with its finding number. High and medium confidence only.

Open findings carry forward: a finding from the previous report that `decisions.md`
does not settle and whose file is not due this run is copied, with its hunk, into a
section "Open from earlier audits", numbered after the new ones.

A finding in a file of this skill (`weekly-checks/`) is reported and never gets a hunk.
A clean surface is a valid result: say so in one line and invent nothing.

### 7. Record the audit

A pass is finished when every due path of its group was audited: compare the planner's
due list with the inventories the subagents returned (or with what you read yourself),
and re-send or read whatever is missing. A file nobody read must not be recorded, or it
stays unaudited until it next changes.

When a pass finished, record it so the next run skips unchanged files:

```
python3 -I scripts/prompt_surface.py commit --group config     # pass A done
python3 -I scripts/prompt_surface.py commit --group app        # pass B done
```

A pass that did not finish is not committed; the next run picks it up again.

## Result

- `attention` if this run found at least one new finding of medium or high confidence.
  The summary line gives the count and the top finding; the apply reply is the first
  phrase in `apply:` in the report language, followed by the finding numbers.
- `ok` if nothing was due, or nothing new at medium or high confidence turned up. Low-
  confidence flags and open findings are given as counts.
- `failed` if the `claude-api` skill is unavailable, the planner exits non-zero, or a
  pass could not finish. Say which.

## Apply

Only when the user asks in this conversation and names the finding numbers
("apply prompt fixes 1, 3" / "תקן פרומפטים 1, 3"). Without numbers, list the open
findings and ask which. "keep 2, 5" settles findings without editing.

1. Read the newest report and its diff. For each chosen finding show the current text
   and the replacement.
2. A conflict between two instruction files, or a stale-fact edit, is confirmed one by
   one even when its number was named: quote both sides, or the fact and what
   contradicts it, and wait for a yes.
3. An edit under `~/.claude/` affects every project: say so before making it.
4. If the file changed since the report was written, do not apply the hunk: re-audit
   that file.
5. Apply with the Edit tool, one finding at a time. Never edit `weekly-checks/`, plugin
   files, settings, hooks or scripts. Code of a project that calls the Claude API is
   not edited from here: give the user the hunk to apply in that project's own session,
   where its tests run.
6. After a removal, search the surrounding files for references to the removed text
   (guide Step 6) and list what you find.
7. Record each finding in `decisions.md`: date, file, pattern, the quoted text (one
   line), and the decision (`fixed`, or `keep` with the user's reason).
8. Edited skills change their hashes, so the next security scan lists them as CHANGED.
   Say so in the closing message.
