#!/usr/bin/env python3
"""Turn `router stats --since 7d --json` into the metrics of the weekly
"router usage" check: where the money went, whether the router pays for itself,
and which sessions deserve a look. Read-only: it runs the `router` CLI (or reads
a saved stats file) and prints or writes the result; nothing else on disk is
touched except the --out-json file.

The output carries only project names, short session ids, model names and
numbers, all taken from the router's own stats. No prompts, no file contents.

Usage (always run isolated):
  python3 -I router_usage.py
      Run `router stats --since 7d --json` and print a terse Markdown block
      (sections: Period, Spend, Subagents by type, Savings, Main model mix,
      Flags, Sessions worth a look, and Trend when --previous is given).
      Exit 0 on success; exit 2 with a one-line stderr message when the
      `router` CLI is missing, fails, or its output is not JSON.
  python3 -I router_usage.py --stats FILE
      Read the stats from FILE (the saved output of `router stats --json`)
      instead of running the CLI. Same exit codes; an unreadable or non-JSON
      FILE is exit 2 too.
  python3 -I router_usage.py --since 30d
      Window passed to `router stats --since D --json` (default 7d). Ignored
      with --stats.
  python3 -I router_usage.py --previous PREV.json
      PREV.json is a metrics file written earlier by --out-json. Adds the
      "trend" key (JSON) and the "## Trend" section (Markdown): new minus
      previous, null where either side is missing.
  python3 -I router_usage.py --out-json FILE
      Also write the metrics JSON to FILE.
  python3 -I router_usage.py --json
      Print the metrics JSON to stdout instead of Markdown.

Metrics JSON keys: period, spend, subagent_types, router_cost, non_router,
savings, by_main_model (with cost_per_msg_ratio), lookups, long_sessions,
no_delegation_candidates, unknown_models, flags, status ("attention" when any
flag has severity "attention", else "ok"), trend (only with --previous).

Every threshold that decides a flag is in the THRESHOLDS dict below; edit it
there and nowhere else. Python 3, standard library only.
"""
import argparse
import json
import shutil
import subprocess
import sys

# The single place where flag thresholds live.
THRESHOLDS = {
    "non_router_share_pct": 25,       # NON_ROUTER: share of subagent cost above this %
    "non_router_cost": 15,            # NON_ROUTER: or non-router subagent cost above this $
    "low_savings_ratio": 2,           # LOW_SAVINGS: saved_inline / router_cost below this
    "heavy_lookups": 10,              # HEAVY_LOOKUPS: a session with this many lookups or more
    "long_session_messages": 150,     # LONG_SESSION: a session with this many main messages or more
    "no_delegation_min_messages": 40,  # NO_DELEGATION: no delegations, at least this many messages ...
    "no_delegation_min_lookups": 3,   # ... and at least this many lookups
    "model_mix_cost_ratio": 1.5,      # MODEL_MIX: priciest/cheapest main model $ per message
    "model_mix_share_pct": 30,        # MODEL_MIX: ... and the priciest model has this % of main cost or more
}

MAX_SESSION_ROWS = 15    # rows of the "Sessions worth a look" table; the rest is summarised
CLI_TIMEOUT_SECONDS = 120


class InputError(Exception):
    """Something the caller must fix (missing CLI, bad JSON); exit 2, no traceback."""


# ----------------------------------------------------------------------------
# input
# ----------------------------------------------------------------------------

def load_json_file(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise InputError("router_usage: cannot read %s %s: %s" % (what, path, exc.strerror or exc))
    except ValueError:
        raise InputError("router_usage: %s %s is not JSON" % (what, path))
    if not isinstance(data, dict):
        raise InputError("router_usage: %s %s is not a JSON object" % (what, path))
    return data


def run_router_cli(since):
    exe = shutil.which("router")
    if not exe:
        raise InputError("router_usage: the `router` CLI was not found on PATH")
    try:
        proc = subprocess.run([exe, "stats", "--since", since, "--json"],
                              capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InputError("router_usage: could not run `router stats`: %s" % type(exc).__name__)
    if proc.returncode != 0:
        raise InputError("router_usage: `router stats` exited %d" % proc.returncode)
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise InputError("router_usage: output of `router stats` is not JSON")
    if not isinstance(data, dict):
        raise InputError("router_usage: output of `router stats` is not a JSON object")
    return data


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def num(value):
    """A number from the input, or 0.0 when it is missing or not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def rnd(value, digits=6):
    return None if value is None else round(value, digits)


def ratio(numerator, denominator):
    return None if not denominator else numerator / denominator


def as_dict(value):
    return value if isinstance(value, dict) else {}


def as_list(value):
    return value if isinstance(value, list) else []


def dig(data, *keys):
    """data[k1][k2]... or None when any step is missing."""
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def models_by_use(models):
    """Model names of a {model: messages} dict, most used first."""
    models = as_dict(models)
    return sorted(models, key=lambda m: (-num(models[m]), m))


# ----------------------------------------------------------------------------
# metrics
# ----------------------------------------------------------------------------

def collect_types(totals, sessions):
    """{type: {model, models, runs, messages, cost, router}} from totals, or
    summed over the sessions when totals does not carry it."""
    by_type = as_dict(totals.get("subagents_by_type"))
    if by_type:
        return {name: as_dict(info) for name, info in by_type.items()}
    merged = {}
    for sess in sessions:
        for name, info in as_dict(sess.get("subagents")).items():
            info = as_dict(info)
            acc = merged.setdefault(name, {"model": info.get("model"), "models": {}, "runs": 0,
                                           "messages": 0, "cost": 0.0, "router": bool(info.get("router"))})
            for model, count in as_dict(info.get("models")).items():
                acc["models"][model] = acc["models"].get(model, 0) + num(count)
            acc["runs"] += num(info.get("runs"))
            acc["messages"] += num(info.get("messages"))
            acc["cost"] += num(info.get("cost"))
    return merged


def pick(totals, key, fallback):
    return num(totals[key]) if key in totals else fallback


def compute_metrics(stats, previous=None):
    sessions = [as_dict(s) for s in as_list(stats.get("sessions"))]
    totals = as_dict(stats.get("totals"))

    # --- spend
    main = pick(totals, "main_cost", sum(num(s.get("main_cost")) for s in sessions))
    subagents = pick(totals, "subagent_cost", sum(num(s.get("subagent_cost")) for s in sessions))
    total = pick(totals, "total_cost", main + subagents)
    share = pick(totals, "subagent_share_pct", 100.0 * subagents / total if total else 0.0)
    spend = {"total": rnd(total), "main": rnd(main), "subagents": rnd(subagents),
             "subagent_share_pct": rnd(share, 4)}

    # --- subagents by type
    types = {}
    for name, info in sorted(collect_types(totals, sessions).items(),
                             key=lambda kv: (-num(kv[1].get("cost")), kv[0])):
        runs = num(info.get("runs"))
        cost = num(info.get("cost"))
        types[name] = {"runs": int(runs), "cost": rnd(cost),
                       "cost_per_run": rnd(ratio(cost, runs)),
                       "router": bool(info.get("router")),
                       "models": as_dict(info.get("models")) or
                                 ({info["model"]: info.get("messages", 0)} if info.get("model") else {})}
    router_cost = sum(t["cost"] for t in types.values() if t["router"])
    nr_names = sorted(n for n, t in types.items() if not t["router"])
    nr_cost = sum(types[n]["cost"] for n in nr_names)
    nr_runs = sum(types[n]["runs"] for n in nr_names)
    non_router = {"cost": rnd(nr_cost), "runs": nr_runs,
                  "share_pct": rnd(100.0 * nr_cost / subagents if subagents else 0.0, 4),
                  "types": nr_names}

    # --- savings
    saved_inline = pick(totals, "saved_inline", sum(num(s.get("saved_inline")) for s in sessions))
    saved_repriced = pick(totals, "saved_repriced", sum(num(s.get("saved_repriced")) for s in sessions))
    savings = {"saved_inline": rnd(saved_inline), "saved_repriced": rnd(saved_repriced),
               "ratio_inline": rnd(ratio(saved_inline, router_cost), 4) if router_cost else None,
               "ratio_floor": rnd(ratio(saved_repriced, router_cost), 4) if router_cost else None}

    # --- main model mix
    acc = {}
    for sess in sessions:
        model = sess.get("main_model") or "unknown"
        row = acc.setdefault(model, {"sessions": 0, "messages": 0, "main_cost": 0.0})
        row["sessions"] += 1
        row["messages"] += int(num(sess.get("main_messages")))
        row["main_cost"] += num(sess.get("main_cost"))
    all_main = sum(r["main_cost"] for r in acc.values())
    by_model = {}
    for model, row in sorted(acc.items(), key=lambda kv: (-kv[1]["main_cost"], kv[0])):
        by_model[model] = {"sessions": row["sessions"], "messages": row["messages"],
                           "main_cost": rnd(row["main_cost"]),
                           "cost_per_msg": rnd(ratio(row["main_cost"], row["messages"])),
                           "share_pct": rnd(100.0 * row["main_cost"] / all_main if all_main else 0.0, 4)}
    priced = [(m, r["cost_per_msg"]) for m, r in by_model.items() if r["cost_per_msg"] is not None]
    cpm_ratio = None
    priciest = cheapest = None
    if len(priced) >= 2:
        priciest = max(priced, key=lambda mc: mc[1])
        cheapest = min(priced, key=lambda mc: mc[1])
        cpm_ratio = rnd(ratio(priciest[1], cheapest[1]), 4)
    by_model["cost_per_msg_ratio"] = cpm_ratio

    # --- sessions worth a look
    heavy, long_sessions, no_delegation = [], [], []
    lookups_total = pick(totals, "lookups", sum(num(s.get("lookups")) for s in sessions))
    for sess in sessions:
        head = {"project": sess.get("project"), "session": sess.get("session"),
                "main_model": sess.get("main_model")}
        msgs = int(num(sess.get("main_messages")))
        looks = int(num(sess.get("lookups")))
        if looks >= THRESHOLDS["heavy_lookups"]:
            heavy.append(dict(head, lookups=looks, main_cost=rnd(num(sess.get("main_cost")))))
        if msgs >= THRESHOLDS["long_session_messages"]:
            long_sessions.append(dict(head, main_messages=msgs, total_cost=rnd(num(sess.get("total_cost")))))
        if (num(sess.get("delegation_count")) == 0 and msgs >= THRESHOLDS["no_delegation_min_messages"]
                and looks >= THRESHOLDS["no_delegation_min_lookups"]):
            no_delegation.append(dict(head, main_messages=msgs, lookups=looks,
                                      main_cost=rnd(num(sess.get("main_cost")))))
    lookups = {"total": int(lookups_total), "heavy": heavy}
    unknown = as_dict(stats.get("unknown_models"))

    metrics = {
        "period": {"since": stats.get("since"), "generated_at": stats.get("generated_at"),
                   "sessions": int(pick(totals, "sessions", len(sessions)))},
        "spend": spend,
        "subagent_types": types,
        "router_cost": rnd(router_cost),
        "non_router": non_router,
        "savings": savings,
        "by_main_model": by_model,
        "lookups": lookups,
        "long_sessions": long_sessions,
        "no_delegation_candidates": no_delegation,
        "unknown_models": unknown,
    }
    metrics["flags"] = compute_flags(metrics, priciest, cheapest)
    metrics["status"] = ("attention" if any(f["severity"] == "attention" for f in metrics["flags"])
                         else "ok")
    if previous is not None:
        metrics["trend"] = compute_trend(metrics, previous)
    return metrics


def flag(code, severity, message, value, threshold):
    return {"code": code, "severity": severity, "message": message,
            "value": value, "threshold": threshold}


def compute_flags(m, priciest, cheapest):
    th = THRESHOLDS
    flags = []
    nr = m["non_router"]
    if nr["share_pct"] > th["non_router_share_pct"] or nr["cost"] > th["non_router_cost"]:
        by_share = nr["share_pct"] > th["non_router_share_pct"]
        flags.append(flag(
            "NON_ROUTER", "attention",
            "non-router subagents (%s) cost $%.2f over %d runs, %.0f%% of subagent spend"
            % (", ".join(nr["types"]), nr["cost"], nr["runs"], nr["share_pct"]),
            nr["share_pct"] if by_share else nr["cost"],
            th["non_router_share_pct"] if by_share else th["non_router_cost"]))
    sv = m["savings"]
    if sv["ratio_inline"] is not None and sv["ratio_inline"] < th["low_savings_ratio"]:
        flags.append(flag(
            "LOW_SAVINGS", "attention",
            "router saved only %.1fx what its agents cost (inline estimate)" % sv["ratio_inline"],
            sv["ratio_inline"], th["low_savings_ratio"]))
    heavy = m["lookups"]["heavy"]
    if heavy:
        top = max(h["lookups"] for h in heavy)
        flags.append(flag(
            "HEAVY_LOOKUPS", "attention",
            "%d session(s) with %d+ main-thread lookups (max %d)"
            % (len(heavy), th["heavy_lookups"], top), top, th["heavy_lookups"]))
    longs = m["long_sessions"]
    if longs:
        top = max(s["main_messages"] for s in longs)
        flags.append(flag(
            "LONG_SESSION", "attention",
            "%d session(s) with %d+ main messages (max %d)"
            % (len(longs), th["long_session_messages"], top), top, th["long_session_messages"]))
    if m["unknown_models"]:
        flags.append(flag(
            "UNKNOWN_MODELS", "attention",
            "models without a price: %s" % ", ".join(sorted(m["unknown_models"])),
            len(m["unknown_models"]), 0))
    cpm_ratio = m["by_main_model"]["cost_per_msg_ratio"]
    if (cpm_ratio is not None and cpm_ratio >= th["model_mix_cost_ratio"]
            and m["by_main_model"][priciest[0]]["share_pct"] >= th["model_mix_share_pct"]):
        flags.append(flag(
            "MODEL_MIX", "info",
            "%s costs %.1fx per message vs %s and carries %.0f%% of main spend"
            % (priciest[0], cpm_ratio, cheapest[0], m["by_main_model"][priciest[0]]["share_pct"]),
            cpm_ratio, th["model_mix_cost_ratio"]))
    nd = m["no_delegation_candidates"]
    if nd:
        flags.append(flag(
            "NO_DELEGATION", "info",
            "%d session(s) with %d+ messages and %d+ lookups but no delegation"
            % (len(nd), th["no_delegation_min_messages"], th["no_delegation_min_lookups"]),
            len(nd), 0))
    return flags


def delta(new, old):
    if (isinstance(new, bool) or isinstance(old, bool)
            or not isinstance(new, (int, float)) or not isinstance(old, (int, float))):
        return None
    return round(new - old, 6)


def compute_trend(m, prev):
    prev = as_dict(prev)
    new_cpm = {k: v.get("cost_per_msg") for k, v in m["by_main_model"].items() if isinstance(v, dict)}
    old_cpm = {k: dig(v, "cost_per_msg") for k, v in as_dict(prev.get("by_main_model")).items()
               if isinstance(v, dict)}
    models = list(new_cpm) + [k for k in old_cpm if k not in new_cpm]
    return {
        "previous_generated_at": dig(prev, "period", "generated_at"),
        "deltas": {
            "spend_total": delta(m["spend"]["total"], dig(prev, "spend", "total")),
            "non_router_share_pct": delta(m["non_router"]["share_pct"], dig(prev, "non_router", "share_pct")),
            "savings_ratio_inline": delta(m["savings"]["ratio_inline"], dig(prev, "savings", "ratio_inline")),
            "lookups_total": delta(m["lookups"]["total"], dig(prev, "lookups", "total")),
            "cost_per_msg": {k: delta(new_cpm.get(k), old_cpm.get(k)) for k in models},
        },
    }


# ----------------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------------

def usd(value):
    return "n/a" if value is None else "$%.2f" % value


def times(value):
    return "n/a" if value is None else "%.1fx" % value


def pct(value):
    return "n/a" if value is None else "%.0f%%" % value


def signed(value, fmt):
    """A delta with an explicit sign; zero prints as plain zero."""
    if value is None:
        return "n/a"
    if value == 0:
        return fmt % 0
    return ("-" if value < 0 else "+") + fmt % abs(value)


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def session_rows(m, sessions):
    """One row per session that is heavy-lookup, long or without delegation."""
    reasons = {}
    order = []
    def note(item, text):
        key = (item["project"], item["session"])
        if key not in reasons:
            reasons[key] = []
            order.append(key)
        reasons[key].append(text)
    for h in m["lookups"]["heavy"]:
        note(h, "lookups %d" % h["lookups"])
    for s in m["long_sessions"]:
        note(s, "long (%d msgs)" % s["main_messages"])
    for s in m["no_delegation_candidates"]:
        note(s, "no delegation")
    detail = {(s.get("project"), s.get("session")): s for s in sessions}
    rows = []
    for key in sorted(order, key=lambda k: -num(detail.get(k, {}).get("total_cost"))):
        sess = detail.get(key, {})
        rows.append([key[0], key[1], sess.get("main_model", "-"), int(num(sess.get("main_messages"))),
                     int(num(sess.get("lookups"))), usd(num(sess.get("main_cost"))),
                     "; ".join(reasons[key])])
    return rows


def render_markdown(m, sessions):
    out = []
    p = m["period"]
    out += ["## Period", "%s to %s, %d sessions" % (p["since"], p["generated_at"], p["sessions"]), ""]
    s = m["spend"]
    out += ["## Spend", "total %s, main %s, subagents %s (%s of total)"
            % (usd(s["total"]), usd(s["main"]), usd(s["subagents"]), pct(s["subagent_share_pct"])), ""]

    out += ["## Subagents by type"]
    rows = [[name, ", ".join(models_by_use(t["models"])) or "-", t["runs"], usd(t["cost"]),
             usd(t["cost_per_run"]), "yes" if t["router"] else "no"]
            for name, t in m["subagent_types"].items()]
    out += table(["type", "model(s)", "runs", "cost", "cost/run", "router"], rows) if rows else ["none"]
    nr = m["non_router"]
    out += ["non-router: %s over %d runs, %s of subagent cost%s"
            % (usd(nr["cost"]), nr["runs"], pct(nr["share_pct"]),
               " (" + ", ".join(nr["types"]) + ")" if nr["types"] else ""), ""]

    sv = m["savings"]
    out += ["## Savings",
            "router agents cost %s; saved (inline) %s = %s; saved (repriced floor) %s = %s"
            % (usd(m["router_cost"]), usd(sv["saved_inline"]), times(sv["ratio_inline"]),
               usd(sv["saved_repriced"]), times(sv["ratio_floor"])), ""]

    out += ["## Main model mix"]
    mix = [[name, r["sessions"], r["messages"], usd(r["main_cost"]), usd(r["cost_per_msg"]),
            pct(r["share_pct"])]
           for name, r in m["by_main_model"].items() if isinstance(r, dict)]
    out += table(["model", "sessions", "msgs", "main $", "$/msg", "share"], mix) if mix else ["none"]
    out += ["$/msg ratio, priciest to cheapest: %s; main-thread lookups: %d"
            % (times(m["by_main_model"]["cost_per_msg_ratio"]), m["lookups"]["total"]), ""]

    out += ["## Flags"]
    out += ["%s %s: %s" % (f["severity"].upper(), f["code"], f["message"]) for f in m["flags"]] or ["none"]
    out += ["status: %s" % m["status"], ""]

    out += ["## Sessions worth a look"]
    rows = session_rows(m, sessions)
    if rows:
        out += table(["project", "session", "model", "msgs", "lookups", "main $", "reason"],
                     rows[:MAX_SESSION_ROWS])
        if len(rows) > MAX_SESSION_ROWS:
            out += ["(+%d more; see --json)" % (len(rows) - MAX_SESSION_ROWS)]
    else:
        out += ["none"]

    if "trend" in m:
        t = m["trend"]
        d = t["deltas"]
        out += ["", "## Trend", "vs previous run %s" % (t["previous_generated_at"] or "(date unknown)"),
                "spend %s, non-router share %s pts, saved ratio %s, lookups %s"
                % (signed(d["spend_total"], "$%.2f"), signed(d["non_router_share_pct"], "%.0f"),
                   signed(d["savings_ratio_inline"], "%.1fx"), signed(d["lookups_total"], "%.0f"))]
        cpm = ["%s %s" % (k, signed(v, "$%.2f")) for k, v in d["cost_per_msg"].items()]
        if cpm:
            out += ["$/msg: " + ", ".join(cpm)]
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Metrics for the weekly router-usage check, from `router stats --json`. "
                    "Exit 0 = metrics produced, 2 = `router` CLI missing or its output is not JSON.")
    ap.add_argument("--stats", metavar="PATH",
                    help="read this stats JSON file instead of running the router CLI")
    ap.add_argument("--since", default="7d", metavar="D",
                    help="window passed to `router stats --since D --json` (default 7d)")
    ap.add_argument("--previous", metavar="PATH",
                    help="metrics JSON written earlier by --out-json; adds a trend section")
    ap.add_argument("--out-json", metavar="PATH", help="also write the metrics JSON here")
    ap.add_argument("--json", action="store_true",
                    help="print the metrics JSON to stdout instead of Markdown")
    args = ap.parse_args(argv)
    try:
        stats = load_json_file(args.stats, "stats file") if args.stats else run_router_cli(args.since)
        previous = load_json_file(args.previous, "previous metrics") if args.previous else None
        metrics = compute_metrics(stats, previous)
        text = json.dumps(metrics, indent=2) + "\n"
        if args.out_json:
            try:
                with open(args.out_json, "w", encoding="utf-8") as fh:
                    fh.write(text)
            except OSError as exc:
                raise InputError("router_usage: cannot write %s: %s" % (args.out_json, exc.strerror or exc))
    except InputError as exc:
        print(exc, file=sys.stderr)
        return 2
    sessions = [as_dict(s) for s in as_list(stats.get("sessions"))]
    sys.stdout.write(text if args.json else render_markdown(metrics, sessions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
