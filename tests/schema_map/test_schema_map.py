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
        users = next(item for item in result["accesses"] if item["table"] == "users")
        self.assertEqual(users["access"], "join index lookup")
        self.assertLess(users["estimated_scan_rows"], users["table_rows"])

    def test_join_equalities_are_not_literal_filters(self):
        schema = {"tables": [
            {"name": "tags", "columns": [{"name": "policy_sk"}, {"name": "tag_name"}], "constraints": []},
            {"name": "policies", "columns": [{"name": "policy_sk"}, {"name": "status"}], "constraints": []},
        ]}
        join_only = sm.analyze_query(
            "SELECT * FROM tags t JOIN policies p ON t.policy_sk = p.policy_sk", schema,
        )
        self.assertTrue(all(not item["predicates"] for item in join_only["accesses"]))
        self.assertFalse(any(item.get("sql") for item in join_only["recommendations"]))
        result = sm.analyze_query(
            "SELECT * FROM tags t JOIN policies p ON t.policy_sk = p.policy_sk "
            "WHERE t.tag_name = 'sensitive' AND p.status = 'active'",
            schema,
        )
        by_table = {item["table"]: item for item in result["accesses"]}
        self.assertEqual([p["column"] for p in by_table["tags"]["predicates"]], ["tag_name"])
        self.assertEqual([p["column"] for p in by_table["policies"]["predicates"]], ["status"])
        self.assertEqual(len(result["joins"]), 1)
        self.assertFalse(any("policy_sk" in item.get("sql", "") for item in result["recommendations"]))

    def test_query_parsing_handles_literals_aliases_and_missing_tables(self):
        schema = {"tables": [
            {"name": "tags", "columns": [{"name": "id"}, {"name": "name"}, {"name": "namespace_sk"}], "constraints": [{"kind": "PRIMARY KEY", "columns": ["id"]}]},
            {"name": "policies", "columns": [{"name": "id"}], "constraints": [{"kind": "PRIMARY KEY", "columns": ["id"]}]},
        ]}
        query = "SELECT * FROM <DB_NAME>.tags a, policies b WHERE a.id=b.id AND a.name='id=7' -- a.id=7\n"
        result = sm.analyze_query(query, schema)
        self.assertEqual([a["alias"] for a in result["accesses"]], ["a", "b"])
        self.assertEqual([a["predicates"] for a in result["accesses"]], [[{"column": "name", "operator": "="}], []])
        self.assertEqual(len(result["joins"]), 1)
        self.assertTrue(any("namespace_sk" in r["title"] for r in result["recommendations"]))
        self_join = sm.analyze_query("SELECT * FROM tags a JOIN tags b ON a.id=b.id WHERE b.name='x'", schema)
        self.assertEqual([a["alias"] for a in self_join["accesses"]], ["a", "b"])
        self.assertEqual(self_join["accesses"][0]["predicates"], [])
        self.assertEqual(self_join["accesses"][1]["predicates"], [{"column": "name", "operator": "="}])
        missing = sm.analyze_query("SELECT * FROM missing m JOIN tags t ON m.id=t.id", schema)
        self.assertIsNone(missing["latency"]["low_ms"])
        self.assertTrue(any("missing" in a for a in missing["assumptions"]))
        self.assertEqual(sm.analyze_query("DELETE FROM tags WHERE id=7", schema)["accesses"][0]["estimated_scan_rows"], 1)
        updated = sm.analyze_query("UPDATE tags SET name='x' WHERE id=7", schema)
        self.assertEqual(updated["accesses"][0]["predicates"], [{"column": "id", "operator": "="}])
        hinted = sm.analyze_query("SELECT * FROM tags USE INDEX (idx) WHERE id=7", schema)
        self.assertEqual(hinted["accesses"][0]["alias"], "tags")
        limited = sm.analyze_query("SELECT * FROM tags ORDER BY id LIMIT 1", schema)
        self.assertEqual(limited["accesses"][0]["access"], "ordered index scan")
        self.assertEqual(limited["accesses"][0]["estimated_scan_rows"], 1)
        unique_schema = {"tables": [{"name": "tags", "columns": [{"name": "name"}, {"name": "namespace_sk"}],
                                    "constraints": [{"kind": "UNIQUE", "columns": ["name", "namespace_sk"]}]}]}
        unique = sm.analyze_query("SELECT * FROM tags WHERE name='x' AND namespace_sk=1", unique_schema,
                                  stats={"tables": {"tags": {"rows": 10_000_000}}})
        self.assertEqual(unique["accesses"][0]["estimated_scan_rows"], 1)
        prefix = sm.analyze_query("SELECT * FROM tags WHERE name='x'", unique_schema)
        self.assertFalse(any(r.get("sql") for r in prefix["recommendations"]))

    def test_tidb_explain_rows_and_observed_timing(self):
        schema = {"tables": [{"name": "tags", "columns": [{"name": "id"}], "constraints": []}]}
        explain = """| id | estRows | actRows | task | access object | execution info | operator info |
| TableReader_7 | 100.00 | 90 | root | | time:152.3ms, loops:1 | data:TableFullScan_5 |
| └─TableFullScan_5 | 1000.00 | 1000 | cop[tikv] | table:tags | tikv_task:{time:140ms} | keep order:false |"""
        result = sm.analyze_query("SELECT * FROM tags", schema, explain_text=explain)
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(len(result["plan"]), 2)
        self.assertEqual(result["accesses"][0]["estimated_scan_rows"], 1000)
        self.assertEqual(result["latency"], {"low_ms": 137.07, "high_ms": 167.53})

    def test_alter_indexes_and_templated_code_references(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            schema = root / "schema.sql"
            schema.write_text("""CREATE TABLE tags (id INT, name VARCHAR(64));
ALTER TABLE tags ADD INDEX idx_name (name);
ALTER TABLE tags ADD UNIQUE KEY uq_name (name);
ALTER TABLE tags ADD CONSTRAINT uq_id UNIQUE (id);
""")
            tables, edges, warnings = sm.parse_schema(schema)
            sm.apply_alters(schema, "schema.sql", tables, edges, warnings)
            self.assertEqual([(c["kind"], c["columns"]) for c in tables[0]["constraints"]],
                             [("INDEX", ["name"]), ("UNIQUE", ["name"]), ("UNIQUE", ["id"])])
            self.assertEqual(warnings, [])
            (root / "app.sql").write_text("SELECT * FROM <DB_NAME>.tags WHERE id=1;")
            self.assertEqual(sm.scan_code(root, {schema}, {"tags"})["tags"], ["app.sql:1"])
            result = sm.analyze_query("SELECT * FROM tags WHERE name='x'", {"tables": tables})
            self.assertFalse(any(r.get("sql") for r in result["recommendations"]))

    def test_analyze_keeps_query_history_without_force(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "schema.json").write_text(json.dumps({"tables": [{"name": "tags", "columns": [{"name": "id"}], "constraints": []}]}))
            args = type("Args", (), {"work": str(work), "query": "SELECT * FROM tags", "query_file": None, "explain": None, "stats": None})
            sm.analyze(args)
            original = (work / "query-analysis.json").read_text()
            args.query = "SELECT id FROM tags WHERE id=2"
            sm.analyze(args)
            self.assertEqual((work / "query-analysis.json").read_text(), original)
            self.assertTrue((work / "query-analysis-2.json").is_file())
            args.force = True
            sm.analyze(args)
            self.assertNotEqual((work / "query-analysis.json").read_text(), original)

    def test_explain_analyze_uses_observed_time(self):
        schema = {"tables": [{"name": "emails", "columns": [{"name": "user_id"}], "constraints": [{"kind": "INDEX", "columns": ["user_id"]}]}]}
        explain = "-> Index lookup on emails using idx_user (user_id=42) (cost=1.2..4.0 rows=10) (actual time=2.0..8.0 rows=9 loops=1)"
        result = sm.analyze_query("SELECT * FROM emails WHERE user_id = 42", schema, explain_text=explain)
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["latency"], {"low_ms": 7.2, "high_ms": 8.8})
        self.assertEqual(result["accesses"][0]["actual_ms"], 8.0)

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
                sm.export(args)
            self.assertEqual(html_path.read_text(),"hand-written HTML")
            self.assertEqual(md_path.read_text(),"hand-written Markdown")
            args.force=True
            sm.export(args)
            page=html_path.read_text()
            self.assertNotIn(str(root),page)
            self.assertIn('"source_base": ".."',page)
            data=json.loads(re.search(r'<script id="sw-data" type="application/json">(.*?)</script>',page,re.S).group(1))
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
