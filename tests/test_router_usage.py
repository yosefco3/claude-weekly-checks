#!/usr/bin/env python3
"""Black-box tests for scripts/router_usage.py.

Every test runs the script as a subprocess (`python3 -I scripts/router_usage.py`)
against the anonymized fixture tests/fixtures/router_stats_sample.json or a tiny
input written to a throwaway folder. The real `router` CLI is never called: the
missing-CLI case points PATH at an empty folder, and the CLI cases put a stub
`router` there.

Run with either:
    python3 -I tests/test_router_usage.py -v
    python3 -m pytest -q tests/test_router_usage.py
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, "..", "scripts", "router_usage.py"))
SAMPLE = os.path.join(HERE, "fixtures", "router_stats_sample.json")

MINIMAL = {
    "since": "2026-01-01T00:00:00+00:00",
    "generated_at": "2026-01-08T00:00:00+00:00",
    "sessions": [],
    "totals": {"sessions": 0, "main_cost": 0, "subagent_cost": 0, "total_cost": 0,
               "subagent_share_pct": 0, "delegations": {}, "delegation_count": 0,
               "lookups": 0, "saved_repriced": 0, "saved_inline": 0, "saved_inline_pct": 0,
               "subagents_by_type": {}},
    "unknown_models": {},
    "note": "",
}


def run_script(*args, env=None, timeout=60):
    return subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def load_sample():
    with open(SAMPLE, encoding="utf-8") as fh:
        return json.load(fh)


class RouterUsageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = self._tmp.name

    def path(self, name):
        return os.path.join(self.tmp, name)

    def metrics(self, *extra):
        proc = run_script("--stats", SAMPLE, "--json", *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def stub_router(self, body):
        """A fake `router` executable in its own folder; returns that folder."""
        folder = self.path("bin")
        os.makedirs(folder, exist_ok=True)
        exe = os.path.join(folder, "router")
        with open(exe, "w", encoding="utf-8") as fh:
            fh.write("#!%s\n%s\n" % (sys.executable, body))
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
        return folder

    # a. the numbers of the real week, via the anonymized fixture ------------------

    def test_a_sample_metrics(self):
        m = self.metrics()
        nr = m["non_router"]
        self.assertEqual(nr["types"], ["general-purpose"])
        self.assertEqual(nr["runs"], 23)
        self.assertAlmostEqual(nr["cost"], 59.90, delta=0.01)
        self.assertAlmostEqual(nr["share_pct"], 68.9, delta=0.2)
        self.assertAlmostEqual(m["router_cost"], 27.09, delta=0.05)
        self.assertAlmostEqual(m["savings"]["ratio_inline"], 7.99, delta=0.05)

        by_model = m["by_main_model"]
        self.assertIn("claude-opus-5-5", by_model)
        self.assertIn("claude-fable-5-1", by_model)
        self.assertGreater(by_model["claude-fable-5-1"]["cost_per_msg"],
                           by_model["claude-opus-5-5"]["cost_per_msg"])

        self.assertEqual(m["lookups"]["total"], 93)
        sessions = load_sample()["sessions"]
        looky = next(s for s in sessions if s["lookups"] == 24)
        self.assertIn((looky["project"], looky["session"]),
                      [(h["project"], h["session"]) for h in m["lookups"]["heavy"]])
        long374 = next(s for s in sessions if s["main_messages"] == 374)
        self.assertIn((long374["project"], long374["session"]),
                      [(s["project"], s["session"]) for s in m["long_sessions"]])

        codes = [f["code"] for f in m["flags"]]
        for code in ("NON_ROUTER", "HEAVY_LOOKUPS", "LONG_SESSION"):
            self.assertIn(code, codes)
        self.assertEqual(m["status"], "attention")
        self.assertNotIn("trend", m)

    # b. --out-json ----------------------------------------------------------------

    def test_b_out_json_matches_stdout(self):
        out = self.path("metrics.json")
        proc = run_script("--stats", SAMPLE, "--json", "--out-json", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), json.loads(proc.stdout))

    # c. --previous ----------------------------------------------------------------

    def test_c_previous_same_week_has_zero_deltas(self):
        prev = self.path("prev.json")
        first = run_script("--stats", SAMPLE, "--out-json", prev)
        self.assertEqual(first.returncode, 0, first.stderr)
        trend = self.metrics("--previous", prev)["trend"]
        self.assertTrue(trend["previous_generated_at"])
        deltas = trend["deltas"]
        numbers = [v for k, v in deltas.items() if k != "cost_per_msg"] + list(deltas["cost_per_msg"].values())
        self.assertGreaterEqual(len(numbers), 6)
        for value in numbers:
            self.assertIsInstance(value, (int, float))
            self.assertEqual(value, 0)
        self.assertIn("claude-fable-5-1", deltas["cost_per_msg"])

    def test_c2_previous_with_missing_parts_gives_null_deltas(self):
        prev = self.path("old.json")
        with open(prev, "w", encoding="utf-8") as fh:
            json.dump({"spend": {"total": 100.0}}, fh)
        deltas = self.metrics("--previous", prev)["trend"]["deltas"]
        self.assertIsNone(self.metrics("--previous", prev)["trend"]["previous_generated_at"])
        self.assertAlmostEqual(deltas["spend_total"], 504.874019 - 100.0, places=4)
        self.assertIsNone(deltas["lookups_total"])
        self.assertIsNone(deltas["savings_ratio_inline"])
        self.assertTrue(all(v is None for v in deltas["cost_per_msg"].values()))

    # d. Markdown ------------------------------------------------------------------

    def test_d_markdown(self):
        proc = run_script("--stats", SAMPLE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("## Flags", proc.stdout)
        self.assertIn("general-purpose", proc.stdout)
        headings = [ln for ln in proc.stdout.splitlines() if ln.startswith("## ")]
        self.assertEqual(headings, ["## Period", "## Spend", "## Subagents by type", "## Savings",
                                    "## Main model mix", "## Flags", "## Sessions worth a look"])
        self.assertIn("ATTENTION NON_ROUTER:", proc.stdout)
        self.assertLess(len(proc.stdout.splitlines()), 60)

    def test_d2_markdown_with_previous_has_trend(self):
        prev = self.path("prev.json")
        run_script("--stats", SAMPLE, "--out-json", prev)
        proc = run_script("--stats", SAMPLE, "--previous", prev)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines().count("## Trend"), 1)

    # e. empty week ----------------------------------------------------------------

    def test_e_minimal_input(self):
        src = self.path("minimal.json")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump(MINIMAL, fh)
        proc = run_script("--stats", src, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        m = json.loads(proc.stdout)
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["flags"], [])
        self.assertIsNone(m["savings"]["ratio_inline"])
        self.assertIsNone(m["by_main_model"]["cost_per_msg_ratio"])
        md = run_script("--stats", src)
        self.assertEqual(md.returncode, 0, md.stderr)
        self.assertIn("## Sessions worth a look", md.stdout)

    # f. missing CLI / bad input ---------------------------------------------------

    def test_f_missing_cli(self):
        empty = self.path("empty")
        os.makedirs(empty)
        env = dict(os.environ, PATH=empty)
        proc = run_script(env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("router", proc.stderr)
        self.assertEqual(len(proc.stderr.strip().splitlines()), 1)
        self.assertEqual(proc.stdout, "")

    def test_f2_cli_output_not_json(self):
        folder = self.stub_router("print('definitely not json')")
        proc = run_script("--json", env=dict(os.environ, PATH=folder))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("router", proc.stderr)
        self.assertIn("JSON", proc.stderr)

    def test_f3_cli_is_called_with_since(self):
        record = self.path("args.txt")
        folder = self.stub_router(
            "import sys\n"
            "open(%r, 'w').write(' '.join(sys.argv[1:]))\n"
            "sys.stdout.write(open(%r).read())" % (record, SAMPLE))
        proc = run_script("--since", "30d", "--json", env=dict(os.environ, PATH=folder))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["lookups"]["total"], 93)
        with open(record, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "stats --since 30d --json")

    def test_f4_stats_file_not_json_or_missing(self):
        bad = self.path("bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{nope")
        for src in (bad, self.path("does-not-exist.json")):
            proc = run_script("--stats", src)
            self.assertEqual(proc.returncode, 2, src)
            self.assertEqual(len(proc.stderr.strip().splitlines()), 1)

    # the fixture itself -----------------------------------------------------------

    def test_g_fixture_is_anonymized(self):
        with open(SAMPLE, encoding="utf-8") as fh:
            text = fh.read()
        # Spelled in pieces so this public file does not itself hold the real names.
        for needle in ("Desk" + "top", "yo" + "sef", "/ho" + "me/"):
            self.assertNotIn(needle, text)
        for sess in load_sample()["sessions"]:
            self.assertRegex(sess["project"], r"^project-\d\d$")
            self.assertEqual(sess["project_dir"], "-home-user-" + sess["project"])
            self.assertEqual(sess["session"], sess["session_id"][:8])


if __name__ == "__main__":
    unittest.main()
