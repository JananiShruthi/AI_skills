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

    def test_keyword_columns_qualified_fk_and_index_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "db" / "schema.sql"
            path.parent.mkdir()
            path.write_text("""CREATE TABLE `users` (`id` BIGINT PRIMARY KEY, `key` VARCHAR(64), `index` INT, KEY idx_prefix (`key`(10), `index`), INDEX idx_lower ((lower(`key`))));
CREATE TABLE orders (id INT PRIMARY KEY, user_id BIGINT, FOREIGN KEY (user_id) REFERENCES public.users(id), FOREIGN KEY (warehouse_id) REFERENCES inventory.warehouses(id));
""")
            tables, edges, warnings = sm.parse_schema(path, "db/schema.sql")
            self.assertEqual([c["name"] for c in tables[0]["columns"]], ["id", "key", "index"])
            self.assertEqual(tables[0]["constraints"][-2]["columns"], ["key", "index"])
            self.assertEqual(tables[0]["constraints"][-1]["columns"], [])
            self.assertIn("lower",tables[0]["constraints"][-1]["expression"])
            self.assertEqual(edges[0]["to"], "users")
            self.assertEqual(edges[1]["to"], "warehouses")
            self.assertEqual(tables[0]["loc"], "db/schema.sql:1")
            self.assertTrue(any("warehouse_id is not declared" in w for w in warnings))

    def test_migrations_and_safe_render(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "001.sql").write_text("CREATE TABLE parents (id INT PRIMARY KEY);\nCREATE TABLE children (id INT PRIMARY KEY);\n")
            (root / "002.sql").write_text("ALTER TABLE children ADD COLUMN parent_id INT;\nALTER TABLE children ADD CONSTRAINT fk FOREIGN KEY (parent_id) REFERENCES parents(id);\n")
            work = root / "work"
            sm.extract(type("Args", (), {"repo":str(root),"schema":[str(root / "001.sql"),str(root / "002.sql")],"out":str(work)}))
            ann = json.loads((work / "annotations.json").read_text())
            ann["purposes"]["children"]={"text":"[click](javascript:alert(1)) <img onerror=alert(2)>","evidence":["001.sql:2"]}
            (work / "annotations.json").write_text(json.dumps(ann))
            html_path=root / "docs" / "schema_explorer.html"
            md_path=root / "SCHEMA.md"
            html_path.parent.mkdir()
            html_path.write_text("hand-written HTML")
            md_path.write_text("hand-written Markdown")
            args=type("Args", (), {"work":str(work),"output":str(html_path),"markdown":str(md_path)})
            with self.assertRaisesRegex(ValueError,"refusing to overwrite"):
                sm.render(args)
            self.assertEqual(html_path.read_text(),"hand-written HTML")
            self.assertEqual(md_path.read_text(),"hand-written Markdown")
            args.force=True
            sm.render(args)
            page=html_path.read_text()
            self.assertNotIn(str(root),page)
            self.assertIn('"source_base": ".."',page)
            data=json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',page,re.S).group(1))
            self.assertEqual(len(data["edges"]),1)
            self.assertIn("parent_id",[c["name"] for c in data["tables"][1]["columns"]])
            guide=md_path.read_text()
            self.assertNotIn("<img",guide)
            self.assertIn("\\[click\\]",guide)

    def test_code_references_include_java_sql_and_skip_bare_mentions(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root / "schema.sql").write_text("CREATE TABLE users (id INT);")
            (root / "Repo.java").write_text('String query = "SELECT * FROM users";\n')
            (root / "notes.py").write_text("# users are important\n")
            (root / "query.ts").write_text("db.from('users')\n")
            refs=sm.scan_code(root,{root / "schema.sql"},{"users"})
            self.assertEqual(set(refs["users"]),{"Repo.java:1","query.ts:1"})


if __name__ == "__main__":
    unittest.main()
