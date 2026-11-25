#!/usr/bin/env python3
"""Black-box tests for scripts/memory_health.py (fixtures in a throwaway folder).

Run with: python3 -I tests/test_memory_health.py -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.normpath(os.path.join(HERE, "..", "scripts", "memory_health.py"))


def run(root):
    return subprocess.run([sys.executable, "-I", SCRIPT, "--root", root],
                          capture_output=True, text=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class MemoryHealth(unittest.TestCase):
    def test_orphan_and_dead_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "projects")
            mem = os.path.join(root, "x", "memory")
            write(os.path.join(mem, "MEMORY.md"),
                  "- [A](a.md) - ok\n- [Gone](gone.md) - missing\n- [Web](https://e.com/z.md)\n")
            write(os.path.join(mem, "a.md"), "a\n")
            write(os.path.join(mem, "orphan.md"), "o\n")
            r = run(root)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("orphans (1): orphan.md", r.stdout)
            self.assertIn("dead links (1): gone.md", r.stdout)
            self.assertNotIn("a.md,", r.stdout)

    def test_clean_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = os.path.join(tmp, "p", "memory")
            write(os.path.join(mem, "MEMORY.md"), "- [A](a.md)\n")
            write(os.path.join(mem, "a.md"), "a\n")
            self.assertEqual(run(tmp).returncode, 0)

    def test_oversized_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "p", "memory", "MEMORY.md"), "line\n" * 250)
            r = run(tmp)
            self.assertEqual(r.returncode, 1)
            self.assertIn("index over 200 lines", r.stdout)

    def test_bad_root_exit_2(self):
        self.assertEqual(run("/nonexistent/xyz").returncode, 2)


if __name__ == "__main__":
    unittest.main()
