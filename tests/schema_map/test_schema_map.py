import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "schema-map" / "scripts" / "schema_map.py"
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
                sm.render(type("Args", (), {"work": str(work), "output": str(output)}))
            self.assertFalse(output.exists())

    def test_onboarding_outputs_keep_cross_area_edge_and_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "schema.sql").write_text("CREATE TABLE parent (id INT PRIMARY KEY);\nCREATE TABLE child (id INT PRIMARY KEY, parent_id INT NOT NULL, FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE CASCADE);\n")
            work = root / "work"
            sm.extract(type("Args", (), {"repo": str(root), "schema": str(root / "schema.sql"), "out": str(work)}))
            ann = json.loads((work / "annotations.json").read_text())
            ann["categories"] = {"parent": "Core", "child": "Work"}
            ann["pipeline"] = [{"title": "Create child", "summary": "Store a child linked to its parent.", "tables": ["child", "parent"], "evidence": ["schema.sql:2"]}]
            (work / "annotations.json").write_text(json.dumps(ann))
            output, markdown = root / "map.html", root / "SCHEMA.md"
            sm.render(type("Args", (), {"work": str(work), "output": str(output), "markdown": str(markdown)}))
            payload = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', output.read_text(), re.S).group(1))
            self.assertEqual(payload["edges"][0]["cardinality"], "N → 1")
            self.assertEqual(payload["edges"][0]["on_delete"], "CASCADE")
            self.assertEqual(payload["group_labels"], ["Core", "Work"])
            self.assertIn("flowchart LR", markdown.read_text())


if __name__ == "__main__":
    unittest.main()
