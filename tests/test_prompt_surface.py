#!/usr/bin/env python3
"""Black-box tests for scripts/prompt_surface.py.

Every test runs the script as a subprocess (`python3 -I scripts/prompt_surface.py`)
with HOME pointed at a throwaway folder, so the real home is never touched.

Run with either:
    python3 -I tests/test_prompt_surface.py -v
    python3 -m pytest -q tests/test_prompt_surface.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, "..", "scripts", "prompt_surface.py"))


class PromptSurfaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = os.path.realpath(self._tmp.name)
        os.makedirs(os.path.join(self.home, "code"))
        self.env = {"HOME": self.home, "PATH": os.environ.get("PATH", "")}

    # helpers
    def write(self, rel, text="x\n"):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def run_cmd(self, *args):
        return subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True,
                              text=True, timeout=60, env=self.env)

    def plan(self, *args):
        proc = self.run_cmd("plan", "--json", *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def commit(self, *args):
        proc = self.run_cmd("commit", *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    @staticmethod
    def due_paths(plan):
        return sorted(d["path"] for d in plan["config"]["due"])

    def basic_config(self):
        self.write(".claude/CLAUDE.md", "user\n")
        self.write(".claude/skills/alpha/SKILL.md", "skill\n")
        self.write(".claude/skills/alpha/reference.md", "ref\n")
        self.write(".claude/commands/do.md", "cmd\n")
        self.write("code/proj/CLAUDE.md", "proj\n")
        self.write("code/proj/.claude/skills/x/SKILL.md", "pskill\n")
        self.write("code/proj/node_modules/pkg/CLAUDE.md", "junk\n")

    # tests
    def test_1_config_discovery(self):
        self.basic_config()
        plan = self.plan()
        paths = self.due_paths(plan)
        for want in ("~/.claude/CLAUDE.md", "~/.claude/skills/alpha/SKILL.md",
                     "~/.claude/commands/do.md", "~/code/proj/CLAUDE.md",
                     "~/code/proj/.claude/skills/x/SKILL.md"):
            self.assertIn(want, paths)
        self.assertNotIn("~/.claude/skills/alpha/reference.md", paths)
        self.assertFalse([p for p in paths if "node_modules" in p])
        self.assertEqual(plan["config"]["total_count"], 5)
        self.assertEqual(plan["project_roots"], ["~/code"])

    def test_2_symlinked_skill_dir_skipped(self):
        self.basic_config()
        target = os.path.join(self.home, "elsewhere")
        os.makedirs(target)
        with open(os.path.join(target, "SKILL.md"), "w") as fh:
            fh.write("linked\n")
        os.symlink(target, os.path.join(self.home, ".claude", "skills", "linked"))
        plan = self.plan()
        self.assertNotIn("~/.claude/skills/linked/SKILL.md", self.due_paths(plan))
        self.assertIn("~/.claude/skills/linked", plan["skipped_symlinks"])

    def test_3_first_plan_full_then_incremental(self):
        self.basic_config()
        first = self.plan()
        self.assertEqual(first["config"]["mode"], "full")
        self.assertEqual(first["config"]["reason"], "no_manifest")
        self.commit()
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "prompt-audit", "pending.json")))
        second = self.plan()
        self.assertEqual(second["config"]["mode"], "incremental")
        self.assertEqual(second["config"]["reason"], "incremental")
        self.assertEqual(second["config"]["due"], [])
        self.assertEqual(second["config"]["same_count"], 5)

    def test_4_edit_new_and_removed(self):
        self.basic_config()
        self.plan()
        self.commit()
        self.write("code/proj/CLAUDE.md", "edited\n")
        plan = self.plan()
        self.assertEqual([(d["path"], d["status"]) for d in plan["config"]["due"]],
                         [("~/code/proj/CLAUDE.md", "changed")])
        self.commit()
        self.write(".claude/agents/new.md", "agent\n")
        plan = self.plan()
        self.assertEqual([(d["path"], d["status"]) for d in plan["config"]["due"]],
                         [("~/.claude/agents/new.md", "new")])
        self.commit()
        os.unlink(os.path.join(self.home, ".claude", "commands", "do.md"))
        plan = self.plan()
        self.assertEqual(plan["config"]["due"], [])
        self.assertEqual(plan["config"]["removed"], ["~/.claude/commands/do.md"])

    def test_5_model_change_forces_full(self):
        self.basic_config()
        self.plan("--model", "a")
        self.commit()
        same = self.plan("--model", "a")
        self.assertEqual(same["config"]["mode"], "incremental")
        other = self.plan("--model", "b")
        self.assertEqual(other["config"]["mode"], "full")
        self.assertEqual(other["config"]["reason"], "model_changed")
        self.assertEqual(other["apps_mode"]["reason"], "model_changed")
        forced = self.plan("--model", "a", "--full")
        self.assertEqual(forced["config"]["reason"], "forced")

    def make_app(self):
        self.write("code/app/.git/HEAD", "ref\n")
        self.write("code/app/backend/requirements.txt", "Anthropic==1.0\n")
        self.write("code/app/backend/llm.py", "client = anthropic.Anthropic()\n")
        self.write("code/app/prompts/x.md", "be nice\n")
        self.write("code/app/feature-prompts/01_step.md", "spec for a coding agent\n")
        self.write("code/app/web/package-lock.json", '{"anthropic": 1}\n')
        self.write("code/app/backend/plain.py", "print('hi')\n")
        self.write("code/other/requirements.txt", "requests\n")
        self.write("code/other/prompt_x.py", "x = 1\n")

    def test_6_app_detection_and_fingerprint(self):
        self.make_app()
        plan = self.plan()
        self.assertEqual([a["project"] for a in plan["apps"]], ["~/code/app"])
        self.assertEqual(plan["apps_mode"]["reason"], "no_manifest")
        self.commit()
        plan = self.plan()
        self.assertEqual(plan["apps"][0]["status"], "same")
        self.assertEqual(plan["apps"][0]["files_count"], 2)
        pending = os.path.join(self.home, ".claude", "prompt-audit")
        with open(os.path.join(pending, "manifest.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        files = sorted(manifest["apps"]["~/code/app"])
        self.assertEqual(files, ["~/code/app/backend/llm.py", "~/code/app/prompts/x.md"])
        self.write("code/app/backend/llm.py", "client = anthropic.Anthropic(timeout=5)\n")
        plan = self.plan()
        self.assertEqual(plan["apps"][0]["status"], "due")
        self.assertEqual(plan["apps"][0]["changed_files"],
                         [{"path": "~/code/app/backend/llm.py", "status": "changed"}])

    def test_7_commit_group_config_leaves_app_due(self):
        self.basic_config()
        self.make_app()
        self.plan()
        self.commit("--group", "config")
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "prompt-audit", "pending.json")))
        plan = self.plan()
        self.assertEqual(plan["config"]["mode"], "incremental")
        self.assertEqual(plan["apps_mode"]["mode"], "full")
        self.assertEqual(plan["apps"][0]["status"], "due")
        self.commit("--group", "app")
        plan = self.plan()
        self.assertEqual(plan["apps"][0]["status"], "same")

    def test_8_commit_without_pending_exits_2(self):
        proc = self.run_cmd("commit")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("pending", proc.stderr)

    def test_settings_toml_project_roots(self):
        os.makedirs(os.path.join(self.home, "elsewhere"))
        self.write("elsewhere/CLAUDE.md", "x\n")
        self.write("code/CLAUDE.md", "y\n")
        self.write(".claude/weekly-checks/settings.toml", 'project_roots = ["~/elsewhere"]\n')
        plan = self.plan()
        self.assertEqual(plan["project_roots"], ["~/elsewhere"])
        self.assertEqual(self.due_paths(plan), ["~/elsewhere/CLAUDE.md"])


if __name__ == "__main__":
    unittest.main()
