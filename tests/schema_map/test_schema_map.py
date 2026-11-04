import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "schema-map" / "scripts" / "schema_map.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("schema_map", SCRIPT)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)


class SchemaMapTests(unittest.TestCase):
    def test_extract_ignores_seed_strings_and_finds_both_fk_forms(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "schema.sql"
            path.write_text("""-- CREATE TABLE fake (id INT);
CREATE TABLE parent (id INT PRIMARY KEY);
CREATE TABLE child (
  id INT,
  parent_id INT REFERENCES parent(id),
  CONSTRAINT fk FOREIGN KEY (id) REFERENCES parent(id)
);
INSERT INTO child VALUES ('CREATE TABLE bogus (x INT);');
""")
            tables, edges, warnings = sm.parse_schema(path)
            self.assertEqual([t["name"] for t in tables], ["parent", "child"])
            self.assertEqual(len(edges), 2)
            self.assertEqual({tuple(e["columns"]) for e in edges}, {("parent_id",), ("id",)})
            self.assertEqual(warnings, [])

    def test_structural_alter_and_unsupported_table_are_visible(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "schema.sql"
            path.write_text("CREATE TABLE t (id INT);\nALTER TABLE t ADD COLUMN value INT;\nCREATE VIEW v AS SELECT id FROM t;\nCREATE TABLE newer LIKE t;\n")
            tables, _, warnings = sm.parse_schema(path)
            self.assertEqual(len(tables), 1)
            self.assertEqual(len(warnings), 3)

    def test_preserves_enum_values_comments_and_key_rules(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "schema.sql"
            path.write_text("""-- A full comment that must not be cut off after the first sentence.
-- It explains how status values are used by the worker.
CREATE TABLE jobs (
  id INT PRIMARY KEY,
  status ENUM('PENDING','DONE') NOT NULL DEFAULT 'PENDING',
  parent_id INT REFERENCES parents(id) ON DELETE CASCADE,
  UNIQUE KEY uq_status (status)
);
CREATE TABLE parents (id INT PRIMARY KEY);
""")
            tables, edges, warnings = sm.parse_schema(path)
            jobs = tables[0]
            status = next(c for c in jobs["columns"] if c["name"] == "status")
            self.assertEqual(status["type"], "ENUM('PENDING','DONE')")
            self.assertEqual(status["default"], "'PENDING'")
            self.assertTrue(status["not_null"])
            self.assertIn("worker.", jobs["comment"])
            self.assertEqual({k["kind"] for k in jobs["constraints"]}, {"PRIMARY KEY", "UNIQUE"})
            self.assertEqual(edges[0]["on_delete"], "CASCADE")
            self.assertEqual(warnings, [])

    def test_invalid_evidence_prevents_report_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "schema.sql").write_text("CREATE TABLE t (id INT);")
            work = root / "work"
            sm.extract(type("Args", (), {"repo": str(root), "schema": str(root / "schema.sql"), "out": str(work)}))
            ann = json.loads((work / "annotations.json").read_text())
            ann["purposes"]["t"] = {"text": "Claim", "evidence": ["missing.py:99"]}
            (work / "annotations.json").write_text(json.dumps(ann))
            output = root / "map.html"
            with self.assertRaisesRegex(ValueError, "invalid evidence"):
                sm.preview(type("Args", (), {"work": str(work), "output": str(output)}))
            self.assertFalse(output.exists())

    def test_preview_is_fragment_and_export_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "schema.sql").write_text("CREATE TABLE parent (id INT PRIMARY KEY);\nCREATE TABLE child (id INT PRIMARY KEY, parent_id INT NOT NULL, FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE CASCADE);\n")
            work = root / "work"
            sm.extract(type("Args", (), {"repo": str(root), "schema": str(root / "schema.sql"), "out": str(work)}))
            ann = json.loads((work / "annotations.json").read_text())
            ann["categories"] = {"parent": "Core", "child": "Work"}
            ann["pipeline"] = [{"title": "Create child", "summary": "Store a child linked to its parent.", "tables": ["child", "parent"], "evidence": ["schema.sql:2"]}]
            (work / "annotations.json").write_text(json.dumps(ann))
            preview = root / "preview.fragment.html"
            sm.preview(type("Args", (), {"work": str(work), "output": str(preview)}))
            preview_text = preview.read_text()
            self.assertFalse(preview_text.startswith("<!doctype"))
            self.assertIn('role="tablist"', preview_text)
            self.assertIn("Export HTML", preview_text)
            payload = json.loads(re.search(r'<script id="sw-data" type="application/json">(.*?)</script>', preview_text, re.S).group(1))
            self.assertEqual(payload["edges"][0]["cardinality"], "N → 1")
            self.assertEqual(payload["edges"][0]["on_delete"], "CASCADE")
            self.assertEqual(payload["group_labels"], ["Core", "Work"])
            self.assertFalse((root / "docs" / "schema_explorer.html").exists())

            output, markdown = root / "map.html", root / "SCHEMA.md"
            sm.export(type("Args", (), {"work": str(work), "output": str(output), "markdown": str(markdown)}))
            self.assertTrue(output.read_text().startswith("<!doctype html>"))
            exported_payload = json.loads(re.search(r'<script id="sw-data" type="application/json">(.*?)</script>', output.read_text(), re.S).group(1))
            self.assertIsNone(exported_payload["work_dir"])
            self.assertIn("flowchart LR", markdown.read_text())

    def test_query_analysis_flags_scope_and_estimates_bounded_latency(self):
        schema = {
            "tables": [
                {"name": "users", "columns": [{"name": "id"}, {"name": "account_id"}], "constraints": [{"kind": "PRIMARY KEY", "columns": ["id"]}]},
                {"name": "emails", "columns": [{"name": "id"}, {"name": "user_id"}, {"name": "account_id"}], "constraints": [{"kind": "PRIMARY KEY", "columns": ["id"]}, {"kind": "INDEX", "columns": ["user_id"]}]},
            ]
        }
        result = sm.analyze_query(
            "SELECT e.id FROM emails e JOIN users u ON u.id = e.user_id WHERE e.user_id = 42",
            schema,
            stats={"tables": {"emails": {"rows": 1_000_000}, "users": {"rows": 100_000}}},
        )
        self.assertEqual(result["confidence"], "low")
        self.assertLess(result["latency"]["low_ms"], result["latency"]["high_ms"])
        self.assertTrue(any("account_id" in item["title"] for item in result["recommendations"]))
        emails = next(item for item in result["accesses"] if item["table"] == "emails")
        self.assertEqual(emails["access"], "index lookup")
        self.assertLess(emails["estimated_scan_rows"], emails["table_rows"])

    def test_explain_analyze_uses_observed_time(self):
        schema = {"tables": [{"name": "emails", "columns": [{"name": "user_id"}], "constraints": [{"kind": "INDEX", "columns": ["user_id"]}]}]}
        explain = "-> Index lookup on emails using idx_user (user_id=42) (cost=1.2..4.0 rows=10) (actual time=2.0..8.0 rows=9 loops=1)"
        result = sm.analyze_query("SELECT * FROM emails WHERE user_id = 42", schema, explain_text=explain)
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["latency"], {"low_ms": 7.2, "high_ms": 8.8})
        self.assertEqual(result["accesses"][0]["actual_ms"], 8.0)


if __name__ == "__main__":
    unittest.main()
