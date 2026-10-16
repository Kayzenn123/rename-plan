#!/usr/bin/env python3
"""Tests for rename-plan. Run: python3 -m unittest discover -s test"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rename_plan import (apply_rules, build_plan, execute_plan, slugify,
                         split_stem_ext, format_counter, main)

BIN = os.path.join(ROOT, "rename_plan.py")


def run_cli(*args, stdin_data=None, cwd=None):
    return subprocess.run(
        [sys.executable, BIN, *args], input=stdin_data,
        capture_output=True, text=True, cwd=cwd or ROOT)


class TestCore(unittest.TestCase):
    def test_replace(self):
        self.assertEqual(
            apply_rules("IMG_001.jpg", [("replace", ("IMG_", "photo_"))]),
            "photo_001.jpg")

    def test_regex(self):
        import re
        self.assertEqual(
            apply_rules("report-2024.pdf", [("regex", (re.compile(r"\d+"), "2025"))]),
            "report-2025.pdf")

    def test_slug(self):
        self.assertEqual(
            apply_rules("Laporan FINAL v2 (revisi).pdf", [("slug", None)]),
            "laporan-final-v2-revisi.pdf")

    def test_slug_unicode(self):
        self.assertEqual(slugify("Café Münchén!"), "cafe-munchen")

    def test_numbering_token_in_suffix(self):
        rules = [("replace", ("IMG_", "")), ("suffix", ["-{n}"][0])]
        self.assertEqual(apply_rules("IMG_A.jpg", rules, "007"), "A-007.jpg")

    def test_numbering_default_before_ext(self):
        self.assertEqual(apply_rules("a.jpg", [], "03"), "a03.jpg")

    def test_number_format_validation(self):
        with self.assertRaises(ValueError):
            format_counter("abc", 1)

    def test_stem_ext_edge_cases(self):
        self.assertEqual(split_stem_ext(".gitignore"), (".gitignore", ""))
        self.assertEqual(split_stem_ext("archive.tar.gz"), ("archive.tar", ".gz"))
        self.assertEqual(split_stem_ext("noext"), ("noext", ""))


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for n in ["b.txt", "a.txt"]:
            open(os.path.join(self.tmp.name, n), "w").close()

    def tearDown(self):
        self.tmp.cleanup()

    def entries(self):
        return [(n, os.path.join(self.tmp.name, n))
                for n in sorted(os.listdir(self.tmp.name))]

    def test_collision_two_sources_same_target(self):
        plan = build_plan(self.entries(),
                          [("replace", ("a", "x")), ("replace", ("b", "x"))])
        self.assertTrue(all(p["status"] == "collision" for p in plan
                            if p["status"] != "unchanged") or
                        any(p["status"] == "collision" for p in plan))
        self.assertIn("collision", {p["status"] for p in plan})

    def test_collision_with_existing_file(self):
        open(os.path.join(self.tmp.name, "target.txt"), "w").close()
        plan = build_plan([("a.txt", os.path.join(self.tmp.name, "a.txt"))],
                          [("replace", ("a", "target"))])
        self.assertEqual(plan[0]["status"], "collision")

    def test_unchanged(self):
        plan = build_plan(self.entries(), [("replace", ("zzz", "q"))])
        self.assertTrue(all(p["status"] == "unchanged" for p in plan))

    def test_swap_two_phase(self):
        plan = build_plan(
            [(n, os.path.join(self.tmp.name, n)) for n in ["a.txt", "b.txt"]],
            [("replace", ("a", "TMPX")), ("replace", ("b", "a")),
             ("replace", ("TMPX", "b"))])
        errs = execute_plan(plan)
        self.assertEqual(errs, [])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "b.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "a.txt")))

    def test_execute_aborts_on_collision(self):
        plan = build_plan(self.entries(),
                          [("replace", ("a", "x")), ("replace", ("b", "x"))])
        errs = execute_plan(plan)
        self.assertTrue(errs)
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "a.txt")))


class TestCLI(unittest.TestCase):
    def test_help_and_version(self):
        r = run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--apply", r.stdout)
        r = run_cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("1.0.0", r.stdout)

    def test_usage_error_no_rules(self):
        r = run_cli("/tmp")
        self.assertEqual(r.returncode, 2)

    def test_usage_error_bad_rule(self):
        r = run_cli("--replace", "nocolon", "/tmp")
        self.assertEqual(r.returncode, 2)

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "IMG_1.jpg"), "w").close()
            r = run_cli("--replace", "IMG_:photo_", d)
            self.assertEqual(r.returncode, 0)
            self.assertIn("photo_1.jpg", r.stdout)
            self.assertTrue(os.path.exists(os.path.join(d, "IMG_1.jpg")))

    def test_apply_renames(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "IMG_1.jpg"), "w").close()
            r = run_cli("--replace", "IMG_:photo_", "--apply", d)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(os.path.exists(os.path.join(d, "photo_1.jpg")))
            self.assertFalse(os.path.exists(os.path.join(d, "IMG_1.jpg")))

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "x.txt"), "w").close()
            r = run_cli("--upper", "--json", d)
            self.assertEqual(r.returncode, 0)
            data = json.loads(r.stdout)
            self.assertFalse(data["applied"])
            self.assertEqual(data["plan"][0]["new"], "X.txt")

    def test_stdin_paths(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a b.txt")
            open(p, "w").close()
            r = run_cli("--slug", stdin_data=p + "\n")
            self.assertEqual(r.returncode, 0)
            self.assertIn("a-b.txt", r.stdout)

    def test_no_color(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "a.txt"), "w").close()
            r = run_cli("--upper", "--no-color", d)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("\033[", r.stdout)

    def test_e2e_table_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ["IMG_20261016_093211.jpg", "IMG_20261016_093310.jpg"]:
                open(os.path.join(d, n), "w").close()
            r = run_cli("--replace", "IMG_20261016_:bali-", "--number", "%03d", d)
            self.assertEqual(r.returncode, 0)
            self.assertIn("bali-093211001.jpg", r.stdout)
            self.assertIn("bali-093310002.jpg", r.stdout)
            self.assertIn("dry-run", r.stderr)
            r2 = run_cli("--replace", "IMG_20261016_:bali-", "--number", "%03d",
                         "--apply", d)
            self.assertEqual(r2.returncode, 0)
            self.assertTrue(os.path.exists(os.path.join(d, "bali-093211001.jpg")))

    def test_missing_dir_exit_1(self):
        r = run_cli("--upper", "/no/such/dir-xyz")
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", r.stderr)


if __name__ == "__main__":
    unittest.main()
