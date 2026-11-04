"""Regression tests for the flow-map skill.

Run from the repo root (stdlib only):
    python3 -B -m unittest discover -s tests/flow_map -v
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixture_project")
SCRIPTS = os.path.join(HERE, "..", "..", "skills", "flow-map", "scripts")
sys.path.insert(0, SCRIPTS)
sys.dont_write_bytecode = True

import extract_checks as ec  # noqa: E402
import flow_map as fm  # noqa: E402


def graph(entry, depth=1):
    return fm.build_graph(ec.analyze(ec.Project(FIXTURE), entry, depth))


def fn(g, qualname):
    return next(f for f in g["functions"] if f["qualname"] == qualname)


def nodes(g, qualname):
    key = fn(g, qualname)["key"]
    return [n for n in g["nodes"] if n["fn"] == key]


def node(g, qualname, code):
    hits = [n for n in nodes(g, qualname) if n["code"] == code]
    assert hits, f"no node {code!r} in {qualname}: {[n['code'] for n in nodes(g, qualname)]}"
    return hits[0]


def outs(g, nid):
    return [e for e in g["edges"] if e["source"] == nid]


def ins(g, nid):
    return [e for e in g["edges"] if e["target"] == nid]


class TestReach(unittest.TestCase):
    def test_called_in_a_test_is_direct(self):
        self.assertEqual(fn(graph("pkg/svc.py:aug"), "aug")["test_reach"], "direct")

    def test_docstring_mention_is_not_a_test(self):
        g = graph("pkg/svc.py:finals")
        self.assertEqual(fn(g, "finals")["test_reach"], "none")
        self.assertEqual(fn(g, "cleanup")["test_reach"], "none")

    def test_mocked_only_is_reported_as_mocked(self):
        g = graph("pkg/svc.py:late_raise")
        self.assertEqual(fn(g, "parse")["test_reach"], "mocked")
        self.assertEqual(fn(g, "store")["test_reach"], "direct")
        self.assertEqual(fn(graph("pkg/svc.py:gen"), "gen")["test_reach"], "mocked")

    def test_route_hit_over_http_is_direct(self):
        self.assertEqual(fn(graph("pkg/api.py:answer"), "answer")["test_reach"], "direct")
        self.assertEqual(fn(graph("pkg/api.py:delete_document"), "delete_document")["test_reach"], "direct")


class TestDrawing(unittest.TestCase):
    def test_augmented_assignment_keeps_operator(self):
        g = graph("pkg/svc.py:aug")
        node(g, "aug", "total += x")
        node(g, "aug", "total -= helpers.clean(x)")

    def test_finally_runs_after_return(self):
        g = graph("pkg/svc.py:finals")
        cleanup = node(g, "finals", "helpers.cleanup()")
        ret = node(g, "finals", "return helpers.clean(x)")
        self.assertIn(ret["id"], [e["source"] for e in ins(g, cleanup["id"])])
        after = [nodeid for nodeid in (e["target"] for e in outs(g, cleanup["id"]))]
        self.assertTrue(after, "finally must lead somewhere")

    def test_exit_calls_end_the_flow(self):
        g = graph("pkg/svc.py:exits")
        for code in ("sys.exit('bad')", "exit(2)"):
            n = node(g, "exits", code)
            self.assertEqual(n["kind"], "exit")
            self.assertEqual(outs(g, n["id"]), [])
        node(g, "exits", "print('ok')")

    def test_generators_show_yields(self):
        g = graph("pkg/svc.py:gen")
        self.assertEqual(node(g, "gen", "yield i")["kind"], "yield")
        self.assertEqual(node(g, "gen", "yield from items")["kind"], "yield")
        end = [n for n in nodes(g, "gen") if n["kind"] == "exit"]
        self.assertIn("generator", end[0]["label"])

    def test_exception_edge_names_the_whole_try_range(self):
        g = graph("pkg/svc.py:late_raise")
        exc = [e for e in g["edges"] if e["style"] == "exc"]
        self.assertEqual(len(exc), 1)
        self.assertIn("lines 48–50", exc[0]["label"])

    def test_raise_inside_try_goes_to_matching_handler(self):
        g = graph("pkg/svc.py:caught_raise")
        r = node(g, "caught_raise", "raise ValueError('bad x')")
        handler = node(g, "caught_raise", "except ValueError")
        self.assertIn(handler["id"], [e["target"] for e in outs(g, r["id"])])


class TestNotes(unittest.TestCase):
    def test_only_real_duplicate_conditions_are_flagged(self):
        dup = [n for n in graph("pkg/svc.py:dupes")["notes"] if n["type"] == "duplicate_condition"]
        self.assertEqual(len(dup), 1)
        self.assertIn("a is None", dup[0]["text"])


class TestLabels(unittest.TestCase):
    def test_trivial_nodes_need_no_agent_label(self):
        g = graph("pkg/svc.py:logs")
        self.assertTrue(node(g, "logs", "t0 = time.perf_counter()")["label"].startswith("Note the time"))
        self.assertEqual(node(g, "logs", "logger.info(f'Handling request for {x}')")["label"],
                         "Log info: Handling request for …")
        self.assertTrue(node(g, "logs", "total = 0")["plain"])

    def test_render_warning_is_compact_and_skips_plain_nodes(self):
        g = graph("pkg/svc.py:late_raise")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "graph.json"), "w") as fh:
                json.dump(g, fh)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                fm.render(d, os.path.join(d, "out.html"))
            warn = [line for line in buf.getvalue().splitlines() if line.startswith("unlabelled")]
            self.assertTrue(warn)
            self.assertLess(len(warn[0]), 200)
            self.assertTrue(os.path.exists(os.path.join(d, "out.html")))

    def test_label_depth_hides_deep_nodes_from_summary(self):
        g = graph("pkg/svc.py:late_raise")
        g["label_depth"] = 0
        text = fm.summary(g, "/tmp/x")
        self.assertNotIn("return x", text)  # body of helpers.clean, depth 1
        self.assertIn("helpers.clean", text)


class TestResolution(unittest.TestCase):
    def test_inherited_and_attribute_methods(self):
        g = graph("pkg/svc.py:Service.run")
        self.assertEqual(node(g, "Service.run", "self.save(items)")["target"], "pkg.base:Base.save")
        self.assertEqual(node(g, "Service.run", "self.client.fetch(items)")["target"], "pkg.helpers:Client.fetch")

    def test_instances_of_project_classes(self):
        self.assertEqual(node(graph("pkg/svc.py:main"), "main", "s.run([1])")["target"], "pkg.svc:Service.run")
        self.assertEqual(node(graph("pkg/svc.py:use"), "use", "return svc.run([1])")["target"], "pkg.svc:Service.run")

    def test_nested_functions(self):
        g = graph("pkg/svc.py:factory")
        self.assertEqual(node(g, "factory", "return inner(3)")["target"], "pkg.svc:factory.inner")
        graph("pkg/svc.py:factory.inner")  # usable as an entry point

    def test_only_auth_dependencies_are_auth_checks(self):
        g = graph("pkg/api.py:answer")
        checks = [n["code"] for n in nodes(g, "answer") if n["kind"] == "check"]
        self.assertEqual(checks, ["Depends(verify_token)"])


class TestProject(unittest.TestCase):
    def test_unrelated_files_are_not_parsed(self):
        p = ec.Project(FIXTURE)
        ec.analyze(p, "pkg/svc.py:aug", 1)
        self.assertFalse(p.modules["pkg.unused"]._loaded)

    def test_entries_include_middleware_and_warn_on_parse_errors(self):
        out = subprocess.run([sys.executable, "-B", os.path.join(SCRIPTS, "flow_map.py"), "entries", FIXTURE],
                             capture_output=True, text=True, check=True).stdout
        self.assertIn("pkg/api.py:add_header", out)
        self.assertIn("pkg/api.py:answer", out)
        self.assertIn("pkg/broken.py", out)
        self.assertIn("could not be parsed", out)


if __name__ == "__main__":
    unittest.main()
