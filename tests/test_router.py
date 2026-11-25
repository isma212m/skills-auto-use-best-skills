"""Tests bout en bout du routeur (stdlib). Lancer : python -m unittest discover -s tests"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROUTER = os.path.join(os.path.dirname(__file__), "..", "scripts", "skill_router.py")


def mk_skill(root, name, desc):
    d = os.path.join(root, ".claude", "skills", name)
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\nname: %s\ndescription: %s\n---\nBODY-SECRET-%s\n" % (name, desc, name))


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        mk_skill(self.tmp, "pdf-tools", "Extract, merge and split PDF files. Use for any pdf document task.")
        mk_skill(self.tmp, "chart-maker", "Create charts, graphs and data visualization from csv data.")
        mk_skill(self.tmp, "graphify", "Turn a codebase into a knowledge graph of architecture and dependencies.")
        mk_skill(self.tmp, SELF := "skills-auto-use-best-skills", "router itself")

    def run_hook(self, kind, **payload):
        payload.setdefault("cwd", self.tmp)
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home)
        r = subprocess.run([sys.executable, ROUTER, "hook", kind], input=json.dumps(payload), capture_output=True,
                           text=True, env=env, encoding="utf-8")
        return r.stdout

    def cmd(self, *a):
        env = dict(os.environ, HOME=self.home, USERPROFILE=self.home)
        return subprocess.run([sys.executable, ROUTER, *a], capture_output=True, text=True, env=env, cwd=self.tmp,
                              encoding="utf-8").stdout

    def state(self):
        with open(os.path.join(self.tmp, ".claude", "active-skills-state.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_index_has_no_body_and_excludes_self(self):
        self.run_hook("sessionstart", source="startup")
        raw = open(os.path.join(self.tmp, ".claude", "skills-index.json"), encoding="utf-8").read()
        self.assertNotIn("BODY-SECRET", raw)
        self.assertNotIn('"name":"skills-auto-use-best-skills"', raw)
        self.assertIn("pdf-tools", raw)

    def test_routes_and_deltas(self):
        self.run_hook("sessionstart", source="startup")
        out = self.run_hook("prompt", prompt="merge these two pdf files into one pdf document please")
        self.assertIn("use=pdf-tools", out)
        self.assertIn("sig=[skills: pdf-tools]", out)
        self.assertLess(len(out), 600)
        again = self.run_hook("prompt", prompt="now split the pdf document into pages")
        self.assertEqual(again.strip(), "")  # déjà chargé dans cette epoch : rien à réinjecter

    def test_continuation_is_silent_and_keeps_skill(self):
        self.run_hook("sessionstart", source="startup")
        self.run_hook("prompt", prompt="make a chart of this csv data with a graph")
        self.assertEqual(self.run_hook("prompt", prompt="ok continue").strip(), "")
        self.assertIn("chart-maker", self.state()["active_skills"])

    def test_graphify_union(self):
        os.makedirs(os.path.join(self.tmp, "graphify-out"))
        out = self.run_hook("sessionstart", source="startup")
        self.assertIn("graphify", out)
        out = self.run_hook("prompt", prompt="make a chart of this csv data with a graph")
        self.assertIn("chart-maker", out)
        self.assertTrue(self.state()["graphify_active"])
        self.assertIn("graphify", self.state()["active_skills"])

    def test_resume_and_compaction_reinject(self):
        self.run_hook("sessionstart", source="startup")
        self.run_hook("prompt", prompt="merge these two pdf files into one pdf document please")
        e0 = self.state()["context_epoch"]
        self.run_hook("precompact")
        out = self.run_hook("sessionstart", source="compact")
        self.assertIn("use=pdf-tools", out)
        self.assertEqual(self.state()["context_epoch"], e0 + 1)  # un seul incrément
        out = self.run_hook("sessionstart", source="resume")
        self.assertIn("use=pdf-tools", out)

    def test_pin_unpin_reset_and_no_recursion(self):
        self.assertIn("ok", self.cmd("pin", "pdf-tools"))
        self.assertEqual(self.state()["active_skills"]["pdf-tools"]["status"], "pinned")
        self.assertIn("pinned=pdf-tools", self.run_hook("sessionstart", source="startup"))
        self.cmd("unpin", "pdf-tools")
        self.assertEqual(self.state()["active_skills"]["pdf-tools"]["status"], "auto")
        self.assertEqual(self.run_hook("prompt", prompt="/skills-auto-use-best-skills diag").strip(), "")
        self.cmd("reset")
        self.assertEqual(self.state()["active_skills"], {})
        self.assertIn("indexed=3", self.cmd("diag"))


if __name__ == "__main__":
    unittest.main()
