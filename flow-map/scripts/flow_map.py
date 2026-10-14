#!/usr/bin/env python3
"""flow-map: build a control-flow graph for a Python entry point and render it as an interactive HTML page.

Stdlib only (Python 3.9+).

  flow_map.py entries ROOT
      List likely entry points.

  flow_map.py extract ROOT --entry path/to/file.py:func [--depth 3] [--out DIR]
      Build the graph. Writes DIR/graph.json and prints a compact one-line-per-node summary
      for the agent to label.

  flow_map.py render DIR [-o OUTPUT.html]
      Merge DIR/graph.json with DIR/labels.json (optional) into a self-contained HTML page.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_checks as ec  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(SKILL_DIR, "assets", "template.html")


# --------------------------------------------------------------------------- graph building

class GraphBuilder:
    """Turn the nested `flow` tree of one function into nodes and edges."""

    def __init__(self, fn, fn_key, counter, known_functions):
        self.fn = fn
        self.key = fn_key
        self.counter = counter
        self.known = known_functions
        self.checks = {c["id"]: c for c in fn["checks"]}
        self.nodes, self.edges = [], []
        self.loops = []  # stack of {"node": id, "breaks": []}

    def new(self, kind, code, loc, **kw):
        self.counter[0] += 1
        nid = f"n{self.counter[0]}"
        node = {"id": nid, "fn": self.key, "kind": kind, "code": code, "loc": loc}
        node.update({k: v for k, v in kw.items() if v not in (None, [], "")})
        self.nodes.append(node)
        return nid

    def edge(self, src, dst, label="", style=""):
        self.edges.append({"id": f"{src}>{dst}#{len(self.edges)}", "source": src, "target": dst,
                           "label": label, "style": style})

    def connect(self, preds, dst):
        for src, label, style in preds:
            self.edge(src, dst, label, style)

    def check_node(self, kind, cid, code, loc):
        c = self.checks.get(cid, {})
        extra = {"check": cid, "tags": c.get("tags")}
        if "covered" in c:
            extra["covered"] = c["covered"]
        if c.get("kind") == "except":
            extra["swallows"] = c.get("swallows")
            extra["broad"] = c.get("broad")
        return self.new(kind, code, loc, **extra)

    @staticmethod
    def classify(calls):
        real = [c for c in calls if "kind" in c]
        for kind in ("unresolved", "project", "external"):
            hit = next((c for c in real if c["kind"] == kind), None)
            if hit:
                return {"unresolved": "unres", "project": "call", "external": "ext"}[kind], hit
        return "step", None

    def call_extra(self, hit):
        if not hit:
            return {}
        if hit["kind"] == "project":
            return {"target": hit["target"], "expanded": hit["target"] in self.known}
        if hit["kind"] == "external":
            return {"lib": hit["target"].split(" (via ")[0]}
        return {"reason": hit.get("reason")}

    def inline_checks(self, calls, preds):
        """Comprehension filters and ternaries live inside expressions; draw them before the statement."""
        for c in calls:
            cid = c.get("filter_check") or c.get("ternary_check")
            if cid:
                ch = self.checks[cid]
                kind_word = "keep item if" if "filter_check" in c else "if"
                n = self.check_node("check", cid, f"{kind_word} {ch['cond']}", ch["loc"])
                self.connect(preds, n)
                preds = [(n, "", "")]
        return preds

    def build(self):
        fn = self.fn
        sig = f"{fn['qualname']}(" + ", ".join(
            p["name"] + (f": {p['type']}" if p["type"] else "") for p in fn["params"]) + ")"
        if fn["returns"]:
            sig += f" -> {fn['returns']}"
        start = self.new("input", sig, f"{fn['file']}:{fn['line']}",
                         label="Inputs: " + (", ".join(p["name"] for p in fn["params"]) or "none"),
                         decorators=fn["decorators"])
        preds = [(start, "", "")]
        for a in fn["auth_checks"]:
            n = self.new("check", a["check"], a["loc"], tags=[f"auth ({a['via']})"])
            self.connect(preds, n)
            preds = [(n, "passes", "yes")]
        for v in fn["validators"]:
            n = self.new("check", f"{v['model']}.{v['validator']} ({v['decorator']})", v["loc"],
                         tags=["pydantic validator"])
            self.connect(preds, n)
            preds = [(n, "valid", "yes")]
        preds = self.seq(fn["flow"], preds)
        if preds:
            end = self.new("exit", "return None", f"{fn['file']}:{fn['end_line']}",
                           label="End: returns nothing (falls off the end)")
            self.connect(preds, end)
        return self.nodes, self.edges

    def seq(self, items, preds):
        for item in items:
            if not preds:
                break
            preds = self.item(item, preds)
        return preds

    def item(self, it, preds):
        t = it["type"]
        if t in ("assign", "expr"):
            preds = self.inline_checks(it["calls"], preds)
            kind, hit = self.classify(it["calls"])
            code = f"{', '.join(it['targets'])} = {it['value']}" if t == "assign" else it["value"]
            n = self.new(kind, code, it["loc"], **self.call_extra(hit))
            self.connect(preds, n)
            data = ", ".join(it["targets"]) if t == "assign" else ""
            return [(n, data, "data" if data else "")]
        if t == "if":
            preds = self.inline_checks(it["cond_calls"], preds)
            n = self.check_node("check", it["check"], it["cond"], it["loc"])
            self.connect(preds, n)
            yes = self.seq(it["true"], [(n, "yes", "yes")])
            no = self.seq(it["false"], [(n, "no", "no")]) if it["false"] else [(n, "no", "no")]
            return yes + no
        if t == "loop":
            n = self.check_node("loop", it["check"], it["header"], it["loc"])
            self.connect(preds, n)
            self.loops.append({"node": n, "breaks": []})
            body = self.seq(it["body"], [(n, "each", "")])
            for src, label, _ in body:
                self.edge(src, n, label if label and label != "each" else "next", "back")
            ctx = self.loops.pop()
            done = [(n, "done", "")]
            if it["else"]:
                done = self.seq(it["else"], done)
            return done + ctx["breaks"]
        if t == "try":
            before = len(self.nodes)
            body = self.seq(it["body"], preds)
            first = self.nodes[before]["id"] if len(self.nodes) > before else None
            outs = self.seq(it["else"], body) if it["else"] else body
            for h in it["handlers"]:
                ch = self.checks[h["check"]]
                n = self.check_node("except", h["check"], ch["cond"], h["loc"])
                if first:
                    self.edge(first, n, f"raises {h['type'] or 'anything'}", "exc")
                else:
                    self.connect(preds, n)
                outs += self.seq(h["body"], [(n, "", "")])
            if it["finally"]:
                outs = self.seq(it["finally"], outs)
            return outs
        if t == "with":
            kind, hit = self.classify(it["calls"])
            n = self.new(kind if kind != "step" else "step", "with " + ", ".join(it["items"]), it["loc"],
                         **self.call_extra(hit))
            self.connect(preds, n)
            return self.seq(it["body"], [(n, "", "")])
        if t == "match":
            outs = []
            for case in it["cases"]:
                ch = self.checks[case["check"]]
                n = self.check_node("check", case["check"], ch["cond"], ch["loc"])
                self.connect(preds, n)
                outs += self.seq(case["body"], [(n, "matches", "yes")])
                preds = [(n, "no match", "no")]
            return outs + preds
        if t == "assert":
            n = self.check_node("check", it["check"], f"assert {it['cond']}", it["loc"])
            self.connect(preds, n)
            err = self.new("error", "AssertionError", it["loc"], label="Fail: assertion error")
            self.edge(n, err, "fails", "no")
            return [(n, "ok", "yes")]
        if t == "raise":
            preds = self.inline_checks(it["calls"], preds)
            n = self.new("error", f"raise {it['exc']}", it["loc"])
            self.connect(preds, n)
            return []
        if t == "return":
            preds = self.inline_checks(it["calls"], preds)
            kind, hit = self.classify(it["calls"])
            extra = self.call_extra(hit) if kind in ("call", "unres") else {}
            if kind == "unres":
                extra["unresolved"] = True
            n = self.new("exit", f"return {it['value']}" if it["value"] else "return", it["loc"], **extra)
            self.connect(preds, n)
            return []
        if t == "continue":
            if self.loops:
                for src, label, _ in preds:
                    self.edge(src, self.loops[-1]["node"], label or "continue", "back")
            return []
        if t == "break":
            if self.loops:
                self.loops[-1]["breaks"].extend((s, l or "break", st) for s, l, st in preds)
            return []
        return preds  # nested defs etc.: not part of the flow


def build_graph(analysis):
    counter = [0]
    known = set(analysis["functions"])
    functions, nodes, edges = [], [], []
    for key, fn in analysis["functions"].items():
        n, e = GraphBuilder(fn, key, counter, known).build()
        nodes += n
        edges += e
        functions.append({
            "key": key, "qualname": fn["qualname"], "file": fn["file"], "line": fn["line"],
            "end_line": fn["end_line"], "depth": fn["depth"], "test_reach": fn["test_reach"],
            "test_refs": fn["test_refs"][:3],
        })
    return {
        "entry": analysis["entry"], "root": analysis["root"], "max_depth": analysis["max_depth"],
        "test_method": analysis["test_method"], "stats": analysis["stats"],
        "functions": functions, "nodes": nodes, "edges": edges,
        "unresolved": [{"in": u["in"], "loc": u["loc"], "call": u["call"], "reason": u.get("reason")}
                       for u in analysis["unresolved"]],
        "external": sorted({e.split(" (via ")[0] for e in analysis["external"]}),
        "notes": [{"loc": n["loc"], "type": n["type"], "text": n["detail"]} for n in analysis["review_notes"]],
        "parse_errors": analysis["parse_errors"],
    }


# --------------------------------------------------------------------------- compact summary for the agent

KIND_W = 6


def summary(graph, out_dir):
    lines = [f"graph: {os.path.join(out_dir, 'graph.json')}",
             f"labels go in: {os.path.join(out_dir, 'labels.json')}",
             f"stats: {len(graph['functions'])} function(s), {len(graph['nodes'])} nodes, "
             f"{graph['stats']['checks']} checks · tests: {graph['test_method']}", ""]
    out_edges = {}
    for e in graph["edges"]:
        out_edges.setdefault(e["source"], []).append(e)
    for f in graph["functions"]:
        lines.append(f"## {f['key']}  {f['file']}:{f['line']}-{f['end_line']}  depth {f['depth']}  "
                     f"tests: {f['test_reach']}")
        for n in (n for n in graph["nodes"] if n["fn"] == f["key"]):
            line_no = n["loc"].rsplit(":", 1)[-1]
            code = n["code"] if len(n["code"]) <= 90 else n["code"][:87] + "..."
            s = f"  {n['id']:<4} {n['kind']:<{KIND_W}} :{line_no:<5} {code}"
            if n.get("label"):
                s += f"   [auto: {n['label']}]"
            outs = out_edges.get(n["id"], [])
            if n["kind"] in ("check", "loop", "except") or len(outs) > 1:
                branches = [f"{e['label']}→{e['target']}" if e["label"] else f"→{e['target']}" for e in outs]
                s += "   {" + ", ".join(branches) + "}"
            if n.get("target"):
                s += f"   (calls {n['target']}{'' if n.get('expanded') else ', not expanded'})"
            if n.get("unresolved") or n["kind"] == "unres":
                s += "   (UNRESOLVED call)"
            if n.get("lib"):
                s += f"   ({n['lib']})"
            lines.append(s)
        lines.append("")
    if graph["unresolved"]:
        lines.append("unresolved: " + "; ".join(f"{u['call']} @ {u['loc']}" for u in graph["unresolved"]))
    if graph["notes"]:
        lines.append("auto review notes (already in the page):")
        lines += [f"  - {n['loc']}: {n['text']}" for n in graph["notes"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- render

def render(out_dir, output):
    with open(os.path.join(out_dir, "graph.json")) as fh:
        graph = json.load(fh)
    labels_path = os.path.join(out_dir, "labels.json")
    labels = {}
    if os.path.exists(labels_path):
        with open(labels_path) as fh:
            labels = json.load(fh)

    node_ids = {n["id"] for n in graph["nodes"]}
    node_labels = labels.get("nodes", {})
    unknown = sorted(set(node_labels) - node_ids)
    for n in graph["nodes"]:
        if node_labels.get(n["id"]):
            n["label"] = node_labels[n["id"]]
    missing = [n["id"] for n in graph["nodes"] if not n.get("label")]
    edge_labels = labels.get("edges", {})
    for e in graph["edges"]:
        key = f"{e['source']}>{e['target']}"
        if key in edge_labels:
            e["label"] = edge_labels[key]
    fn_titles = labels.get("functions", {})
    for f in graph["functions"]:
        f["title"] = fn_titles.get(f["key"], "")
    graph["notes"] = [{"loc": n.get("loc", ""), "type": "review", "text": n.get("text", "")}
                      for n in labels.get("notes", [])] + graph["notes"]
    graph["summary"] = labels.get("summary", "")

    with open(TEMPLATE) as fh:
        html = fh.read()
    title = graph["entry"]
    data = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("__TITLE__", title).replace("__FLOW_DATA__", data)
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, "w") as fh:
        fh.write(html)

    print(f"wrote {output}  ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")
    if missing:
        print(f"unlabelled nodes (showing code instead): {', '.join(missing)}")
    if unknown:
        print(f"labels for unknown node ids ignored: {', '.join(unknown)}")
    for f in graph["functions"]:
        if not f["title"]:
            print(f"no plain-English title for function {f['key']}")


# --------------------------------------------------------------------------- CLI

def default_out_dir(root, entry):
    h = hashlib.sha1(f"{os.path.abspath(root)}|{entry}".encode()).hexdigest()[:8]
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", entry.split(":")[-1])
    return os.path.join(tempfile.gettempdir(), "flow-map", f"{name}-{h}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("entries")
    p.add_argument("root")
    p = sub.add_parser("extract")
    p.add_argument("root")
    p.add_argument("--entry", required=True)
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--out")
    p = sub.add_parser("render")
    p.add_argument("dir")
    p.add_argument("-o", "--output")
    args = ap.parse_args()

    if args.cmd == "entries":
        project = ec.Project(args.root)
        for e in ec.list_entries(project):
            print(f"{e['entry']}  (line {e['line']}: {e['reason']})")
        return
    if args.cmd == "extract":
        project = ec.Project(args.root)
        analysis = ec.analyze(project, args.entry, args.depth)
        graph = build_graph(analysis)
        out_dir = args.out or default_out_dir(args.root, args.entry)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "graph.json"), "w") as fh:
            json.dump(graph, fh)
        stale = os.path.join(out_dir, "labels.json")
        if os.path.exists(stale):
            os.remove(stale)  # node ids change between runs; old labels would be wrong
        print(summary(graph, out_dir))
        return
    if args.cmd == "render":
        with open(os.path.join(args.dir, "graph.json")) as fh:
            g = json.load(fh)
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", g["entry"].split(":")[-1].replace(".", "_"))
        output = args.output or os.path.join(g["root"], "docs", "flows", f"{name}.html")
        render(args.dir, output)


if __name__ == "__main__":
    main()
