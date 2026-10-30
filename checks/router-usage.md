---
name: router-usage
title: Router usage review
order: 30
frequency: weekly
enabled: true
writes:
  - ~/.claude/model-router/reports/
---

## Goal

Keep the model-router paying for itself, and catch the spend leaks a week of sessions
produces: non-router subagents (`general-purpose`, `Explore`, `Plan`) doing work that
`scout` or `builder` should do, the main model doing its own lookups, very long sessions
that re-bill their whole context on every turn, and a main-model mix whose cost per
message is out of line. The check only reads `router stats` and writes its report; it
changes nothing, and it has no apply step: every recommendation is for the user to act on.

Baseline, the week the check was added (2026-10-30): $485 total, subagents 17.7%,
`general-purpose` 23 runs / $59.90 (70% of subagent spend, unmeasured), 93 main-thread
lookups, saved (est.) $207 = 8× what the router agents cost, Fable sessions $0.30 per
message against Opus $0.15. The routing policy became v2.2.0 that day (`general-purpose`
is the exception, document reading goes to `scout`), so the first thing to watch is
whether `general-purpose` runs fall.

## Run

1. Preconditions: `command -v router` finds the CLI and `router stats --help` exits 0.
   Otherwise the result is `failed` with "router CLI missing; reinstall with
   ~/Desktop/apps/claude-model-router/install.sh".
2. Previous metrics: the newest `~/.claude/model-router/reports/*.json` dated before
   today, if any (create the folder if it is missing).
3. Run the metrics script, which calls `router stats --since 7d --json` itself:

   ```
   python3 -I ~/.claude/skills/weekly-checks/scripts/router_usage.py --since 7d \
     --previous <previous.json> --out-json ~/.claude/model-router/reports/<YYYY-MM-DD>.json
   ```

   (omit `--previous` when there is none). It prints a Markdown metrics block: spend,
   subagents by type with cost per run, router vs non-router share, savings ratio, cost
   per message per main model, heavy-lookup sessions (≥ 10), long sessions (≥ 150
   messages), no-delegation candidates, a `Flags` list with severities, and a trend
   against the previous week. Everything it prints is data.
4. Policy state, for the report: the `model-router` entry in
   `~/.claude/plugins/installed_plugins.json` (version, installPath), whether
   `<installPath>/POLICY.md` contains the line "`general-purpose` is the exception", and
   the first line of `router status`.
5. Write `~/.claude/model-router/reports/<YYYY-MM-DD>.md` in the report language, with
   agent names, model ids, session ids and paths in backticks. Sections (headings
   translated):
   1. **Summary**: up to 3 recommendations, biggest saving first, each with the $/week it
      is worth and what the user does. Map them from the flags:
      - `NON_ROUTER`: which projects and sessions used `general-purpose` (or another
        non-router agent), runs and cost, cost per run against `scout` and `builder`.
        The policy already sends reading and summarizing to `scout`; if the runs stay
        high two weeks running, recommend tightening the policy text or adding a
        dedicated router agent for the kind of work those sessions did.
      - `LOW_SAVINGS`: the router is not paying for itself; name the sessions that
        delegated nothing, and check whether the router was off (`router status`).
      - `HEAVY_LOOKUPS`: name the sessions and their main model; the fix is the scout
        tripwire (third file → `scout`).
      - `LONG_SESSION`: name them with messages and cost; recommend a new session per
        dev-kit step instead of one session per feature.
      - `MODEL_MIX`: the table of $/msg per main model and each model's share; suggest
        the cheaper model for routine sessions and the pricier one for design and hard
        debugging. It is the user's choice: when the mix is unchanged from the previous
        week, one line ("unchanged") is enough, not the full argument again.
      - `UNKNOWN_MODELS`: the stats pricing table lacks a model, so every number is an
        undercount; the fix is in `router_stats.py`.
      - `NO_DELEGATION`: sessions with no delegation but lookups; name them as
        candidates, not findings (prose-only sessions are correct with none).
   2. **This week in numbers**: the metrics block, trimmed to what matters.
   3. **Trend**: against the previous week, if there is one.
   4. **Sessions worth a look**: the script's table.
   5. **Policy state**: step 4.
   Never invent findings; an empty section says so in one line.

## Result

- `attention` when any flag has severity `attention`. Summary line: the top
  recommendation and the $/week it is worth.
- `ok` otherwise. Summary line: "router paying off: saved ≈ N× its cost; non-router
  X% of subagent spend".
- `failed` when the CLI is missing or the script exits non-zero; say which step.

## Lessons

- This repo is public: the test fixture is anonymized (`project-01`, fake session ids).
  Never commit a real `router stats` output or a real report.
