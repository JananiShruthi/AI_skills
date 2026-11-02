#!/usr/bin/env python3
"""Extract a TiDB/MySQL schema and render a self-contained HTML onboarding map."""
import argparse
import json
import re
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path

IDENT = r"(?:`[^`]+`|[A-Za-z_][\w$]*)"
QUAL = rf"{IDENT}(?:\s*\.\s*{IDENT})?"
CREATE = re.compile(rf"\bCREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>{QUAL})\s*\(", re.I)
FK = re.compile(rf"\bFOREIGN\s+KEY\s*\((?P<cols>[^)]*)\)\s*REFERENCES\s+(?P<target>{QUAL})\s*\((?P<target_cols>[^)]*)\)", re.I | re.S)
INLINE_REF = re.compile(rf"\bREFERENCES\s+(?P<target>{QUAL})\s*\((?P<target_cols>[^)]*)\)", re.I | re.S)
KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "KEY", "INDEX", "CONSTRAINT", "CHECK", "FULLTEXT", "VECTOR", "SPATIAL"}
EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "site-packages"}


def name(value):
    return value.split(".")[-1].strip().strip("`").lower()


def line_at(text, offset):
    return text.count("\n", 0, offset) + 1


def mask_sql(text):
    """Blank comments and string literals while preserving offsets and newlines."""
    out = list(text)
    i = 0
    state = "code"
    while i < len(text):
        c = text[i]
        nxt = text[i:i+2]
        if state == "code":
            if nxt in ("--", "/*") or c == "#":
                state = "block" if nxt == "/*" else "line"
                step = 2 if nxt != "#" else 1
            elif c in ("'", '"'):
                state = c
                step = 1
            else:
                i += 1
                continue
            for j in range(i, min(i+step, len(text))):
                if text[j] != "\n": out[j] = " "
            i += step
        elif state == "line":
            if c == "\n": state = "code"
            else: out[i] = " "
            i += 1
        elif state == "block":
            if nxt == "*/":
                out[i:i+2] = [" ", " "]
                i += 2
                state = "code"
            else:
                if c != "\n": out[i] = " "
                i += 1
        else:
            if c == "\\" and i+1 < len(text):
                out[i:i+2] = [" ", " "]
                i += 2
            elif c == state:
                out[i] = " "
                i += 1
                if i < len(text) and text[i] == state:
                    out[i] = " "
                    i += 1
                else: state = "code"
            else:
                if c != "\n": out[i] = " "
                i += 1
    return "".join(out)


def matching_paren(masked, opening):
    depth = 0
    for i in range(opening, len(masked)):
        if masked[i] == "(": depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0: return i
    return None


def segments(masked, start, end):
    depth = 0
    item_start = start
    for i in range(start, end):
        if masked[i] == "(": depth += 1
        elif masked[i] == ")": depth -= 1
        elif masked[i] == "," and depth == 0:
            yield item_start, i
            item_start = i+1
    yield item_start, end


def preceding_comment(lines, line):
    found = []
    for i in range(line-2, -1, -1):
        s = lines[i].strip()
        if s.startswith("---"):
            break
        if s.startswith("--"):
            found.append(s.lstrip("-").strip())
        elif not s and not found:
            continue
        else:
            break
    return " ".join(reversed(found))


def column_type(raw_tail):
    m = re.match(r"\s*([A-Za-z_][\w]*)", raw_tail)
    if not m: return ""
    end = m.end()
    while end < len(raw_tail) and raw_tail[end].isspace(): end += 1
    if end < len(raw_tail) and raw_tail[end] == "(":
        masked = mask_sql(raw_tail)
        close = matching_paren(masked, end)
        if close is not None: end = close+1
    return raw_tail[:end].strip()


def key_columns(raw):
    m = re.search(r"\(([^()]*)\)", raw)
    return split_names(m.group(1)) if m else []


def default_value(raw):
    m = re.search(r"\bDEFAULT\s+('(?:''|\\.|[^'])*'|\"(?:\"\"|\\.|[^\"])*\"|[^\s,]+)",raw,re.I)
    return m.group(1) if m else ""


def add_constraint(constraints, kind, cols, loc):
    if cols: constraints.append({"kind":kind,"columns":cols,"loc":loc})


def parse_schema(path):
    text = path.read_text(encoding="utf-8")
    masked = mask_sql(text)
    lines = text.splitlines()
    tables, edges, warnings = [], [], []
    matched_starts = set()
    for m in CREATE.finditer(masked):
        table_name = name(m.group("name"))
        opening = m.end()-1
        close = matching_paren(masked, opening)
        start_line = line_at(text, m.start())
        matched_starts.add(m.start())
        if close is None:
            warnings.append(f"{path.name}:{start_line}: unclosed CREATE TABLE {table_name}")
            continue
        cols = []
        constraints = []
        edges_before = len(edges)
        for a, b in segments(masked, opening+1, close):
            raw = text[a:b].strip()
            clean = masked[a:b].strip()
            if not clean: continue
            first = re.match(rf"({IDENT})", clean)
            if not first:
                warnings.append(f"{path.name}:{line_at(text,a)}: unparsed table item in {table_name}")
                continue
            word = first.group(1).strip("`")
            loc = f"{path.name}:{line_at(text,a+len(text[a:b])-len(text[a:b].lstrip()))}"
            if word.upper() not in KEYWORDS:
                col = word.lower()
                tail = raw[first.end():]
                cols.append({"name": col, "type": column_type(tail), "not_null":bool(re.search(r"\bNOT\s+NULL\b",clean,re.I)), "default":default_value(raw), "loc": loc})
                if re.search(r"\bPRIMARY\s+KEY\b",clean,re.I): add_constraint(constraints,"PRIMARY KEY",[col],loc)
                if re.search(r"\bUNIQUE\b",clean,re.I): add_constraint(constraints,"UNIQUE",[col],loc)
                ref = INLINE_REF.search(clean[first.end():])
                if ref:
                    delete = re.search(r"\bON\s+DELETE\s+(CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION|SET\s+DEFAULT)",clean,re.I)
                    edges.append({"from":table_name,"to":name(ref.group("target")),"columns":[col],"target_columns":split_names(ref.group("target_cols")),"kind":"declared","declaration":"inline REFERENCES","on_delete":delete.group(1).upper() if delete else "", "loc":loc})
            else:
                ref = FK.search(clean)
                if ref:
                    delete = re.search(r"\bON\s+DELETE\s+(CASCADE|SET\s+NULL|RESTRICT|NO\s+ACTION|SET\s+DEFAULT)",clean,re.I)
                    edges.append({"from":table_name,"to":name(ref.group("target")),"columns":split_names(ref.group("cols")),"target_columns":split_names(ref.group("target_cols")),"kind":"declared","declaration":"FOREIGN KEY","on_delete":delete.group(1).upper() if delete else "", "loc":loc})
                if re.search(r"\bPRIMARY\s+KEY\b",clean,re.I): add_constraint(constraints,"PRIMARY KEY",key_columns(clean),loc)
                elif re.search(r"\bUNIQUE\b",clean,re.I): add_constraint(constraints,"UNIQUE",key_columns(clean),loc)
                elif re.search(r"\b(?:VECTOR\s+INDEX|INDEX|KEY)\b",clean,re.I) and not ref:
                    add_constraint(constraints,"INDEX",key_columns(clean),loc)
        references = len(re.findall(r"\bREFERENCES\b",masked[opening+1:close],re.I))
        if len(edges)-edges_before != references:
            warnings.append(f"{path.name}:{start_line}: some REFERENCES in {table_name} were not parsed")
        tables.append({"name":table_name,"loc":f"{path.name}:{start_line}","comment":preceding_comment(lines,start_line),"columns":cols,"constraints":constraints})
    by_table={t["name"]:t for t in tables}
    for m in re.finditer(rf"\bCREATE\s+(?P<unique>UNIQUE\s+)?(?:VECTOR\s+)?INDEX\s+{IDENT}\s+ON\s+(?P<table>{QUAL})\s*\(",masked,re.I):
        target=name(m.group("table"))
        close=matching_paren(masked,m.end()-1)
        if target in by_table and close is not None:
            add_constraint(by_table[target]["constraints"],"UNIQUE" if m.group("unique") else "INDEX",key_columns(masked[m.end()-1:close+1]),f"{path.name}:{line_at(text,m.start())}")
    # Warn on CREATE TABLE tokens that could not match the supported header.
    for m in re.finditer(r"\bCREATE\s+(?:TEMPORARY\s+)?TABLE\b", masked, re.I):
        if m.start() not in matched_starts:
            warnings.append(f"{path.name}:{line_at(text,m.start())}: unsupported CREATE TABLE syntax")
    for m in re.finditer(r"\bALTER\s+TABLE\b[^;]*", masked, re.I | re.S):
        statement=m.group()
        if re.search(r"\bFOREIGN\s+KEY\b|\b(?:ADD|DROP|MODIFY|CHANGE|RENAME)\s+(?:COLUMN\s+)?(?!INDEX\b|KEY\b|VECTOR\b)",statement,re.I):
            warnings.append(f"{path.name}:{line_at(text,m.start())}: structural ALTER TABLE is not parsed")
    for m in re.finditer(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:VIEW|TRIGGER|PROCEDURE|FUNCTION)\b",masked,re.I):
        warnings.append(f"{path.name}:{line_at(text,m.start())}: database object outside table parser")
    seen = set()
    for t in tables:
        if t["name"] in seen: warnings.append(f"{t['loc']}: duplicate table name {t['name']}")
        seen.add(t["name"])
        if not t["columns"]: warnings.append(f"{t['loc']}: no columns parsed for {t['name']}")
    for edge in edges:
        if edge["to"] not in seen: warnings.append(f"{edge['loc']}: foreign key target {edge['to']} is outside parsed schema")
    return tables, edges, warnings


def split_names(value):
    return [name(x.strip()) for x in value.split(",") if x.strip()]


def scan_code(root, schema, table_names):
    refs = defaultdict(list)
    for path in root.rglob("*"):
        if any(part in EXCLUDED or part.startswith(".") for part in path.relative_to(root).parts): continue
        if not path.resolve().is_relative_to(root): continue
        if not path.is_file() or path == schema or path.suffix.lower() not in {".py", ".sql", ".md"}: continue
        try:
            if path.stat().st_size > 500_000: continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError): continue
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(content.splitlines(), 1):
            low = line.lower()
            for table in table_names:
                if re.search(rf"(?<![\w]){re.escape(table)}(?![\w])", low):
                    refs[table].append((0 if rel.startswith("application/") else 1 if rel.startswith("docs/") else 2 if not rel.startswith("tests/") else 3, f"{rel}:{i}"))
    return {table:[loc for _,loc in sorted(hits)[:4]] for table,hits in refs.items()}


def documented_purposes(root, names):
    """Read explicit Markdown table-purpose rows; leave all other prose to review."""
    purposes, categories = {}, {}
    docs = sorted((root/"docs").rglob("*.md"), key=lambda p:("table_reference" not in p.name.lower(),p.as_posix())) if (root/"docs").is_dir() else []
    row = re.compile(r"^\|\s*`?([A-Za-z_][\w]*)`?\s*\|\s*(.*?)\s*\|")
    for path in docs:
        if not path.resolve().is_relative_to(root): continue
        try:
            if path.stat().st_size > 250_000: continue
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError): continue
        heading = ""
        purpose_table = False
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(lines,1):
            if line.startswith("## "): heading = line[3:].strip()
            if line.startswith("|") and re.match(r"^\|\s*Table\s*\|\s*(?:Purpose|What it stores|Role in)",line,re.I):
                purpose_table = True
                continue
            if not line.startswith("|"):
                purpose_table = False
                continue
            if not purpose_table: continue
            m = row.match(line)
            if not m: continue
            table = m.group(1).lower()
            purpose = re.sub(r"\s+", " ", re.sub(r"[`*]", "", m.group(2))).strip()
            if table in names and purpose and table not in purposes and not purpose.startswith("---") and len(purpose)<=240:
                purposes[table] = {"text":purpose,"evidence":[f"{rel}:{i}"]}
                if "table_reference" in path.name.lower():
                    categories[table] = heading
    return purposes, categories


def groups_for(tables, edges, categories):
    names = [t["name"] for t in tables]
    adj = {n:set() for n in names}
    for e in edges:
        if e["from"] in adj and e["to"] in adj:
            adj[e["from"]].add(e["to"])
            adj[e["to"]].add(e["from"])
    left = set(names)
    groups = []
    labels = []
    for category in dict.fromkeys(categories.values()):
        if not category: continue
        members = [n for n in names if categories.get(n)==category]
        while members:
            group=members[:9]; members=members[9:]
            groups.append(group); labels.append(category)
            left.difference_update(group)
    while left:
        seed = max(left, key=lambda n:(len(adj[n] & left),-names.index(n)))
        q = deque([seed]); group=[]
        while q and len(group) < 9:
            n=q.popleft()
            if n not in left: continue
            left.remove(n); group.append(n)
            q.extend(sorted(adj[n] & left, key=lambda x:(-len(adj[x] & left),x)))
        groups.append(group); labels.append("Connected area")
    return groups, labels


def add_cardinality(data):
    by_name={t["name"]:t for t in data["tables"]}
    for edge in data["edges"]:
        child=by_name.get(edge["from"])
        if not child or edge["kind"]!="declared": continue
        unique=any(k["kind"] in ("PRIMARY KEY","UNIQUE") and k["columns"]==edge["columns"] for k in child["constraints"])
        col_by_name={c["name"]:c for c in child["columns"]}
        optional=any(not col_by_name.get(c,{}).get("not_null") for c in edge["columns"])
        edge["cardinality"]=("1" if unique else "N")+" → "+("0..1" if optional else "1")


def md_escape(value):
    return str(value).replace("|","\\|").replace("\n"," ")


def schema_markdown(data, html_path):
    lines=["# Schema guide", "", f"Source: `{data['schema']}`. This guide describes the schema defined in that file and the cited repository evidence.", ""]
    if data["warnings"]:
        lines += ["**Schema may be incomplete.** " + " ".join(data["warnings"]), ""]
    lines += ["## How data moves", ""]
    pipeline=data.get("pipeline",[])
    if pipeline:
        lines += ["```mermaid", "flowchart LR"]
        for i,step in enumerate(pipeline):
            label=step["title"].replace('"',"'")
            lines.append(f'  s{i}["{i+1}. {label}"]')
            if i: lines.append(f"  s{i-1} --> s{i}")
        lines += ["```", ""]
        for i,step in enumerate(pipeline,1):
            lines += [f"{i}. **{step['title']}** — {step['summary']} Tables: "+", ".join(f"`{n}`" for n in step["tables"])+". Evidence: "+", ".join(f"`{loc}`" for loc in step["evidence"])+"."]
        lines.append("")
    else:
        lines += ["The data pipeline has not been verified from repository evidence. Confirm it with the project owner.", ""]
    lines += ["## Table areas", ""]
    for label,group in zip(data["group_labels"],data["groups"]):
        lines += [f"### {label}", "", "| Table | Purpose |", "| --- | --- |"]
        for n in group:
            p=data["purposes"].get(n)
            purpose=p["text"] if p else "Confirm with the project owner"
            lines.append(f"| `{n}` | {md_escape(purpose)} |")
        lines.append("")
    lines += [f"For columns, keys, relationship direction, and source links, open `{html_path}`.", ""]
    return "\n".join(lines)


def extract(args):
    root = Path(args.repo).resolve()
    schema = Path(args.schema).resolve()
    if not root.is_dir() or not schema.is_file() or not schema.is_relative_to(root):
        raise ValueError("schema must be a file inside repo_root")
    tables, edges, warnings = parse_schema(schema)
    if not tables: raise ValueError("no CREATE TABLE statements parsed")
    work = Path(args.out).resolve() if args.out else Path(tempfile.mkdtemp(prefix="schema-map-"))
    work.mkdir(parents=True, exist_ok=True)
    table_names={t['name'] for t in tables}
    purposes,categories=documented_purposes(root,table_names)
    data = {"repo":str(root),"schema":schema.relative_to(root).as_posix(),"tables":tables,"edges":edges,"warnings":warnings,"references":scan_code(root,schema,table_names),"categories":categories}
    (work/"schema.json").write_text(json.dumps(data,indent=2),encoding="utf-8")
    (work/"annotations.json").write_text(json.dumps({"purposes":purposes,"categories":{},"inferred_edges":[]},indent=2),encoding="utf-8")
    print(f"Work directory: {work}\nTables: {len(tables)}; declared relationships: {len(edges)}; documented purposes: {len(purposes)}; warnings: {len(warnings)}")
    for w in warnings: print("WARNING",w)
    for t in tables:
        refs = ", ".join(data["references"].get(t["name"],[])[:3]) or "none"
        print(f"{t['name']} | {t['loc']} | {t['comment'][:100] or 'no SQL comment'} | refs: {refs}")


def valid_loc(root, loc):
    m = re.fullmatch(r"(.+):(\d+)",loc)
    if not m: return False
    path = (root/m.group(1)).resolve()
    if not path.is_relative_to(root) or not path.is_file(): return False
    try:
        with path.open(encoding="utf-8") as handle:
            return 1 <= int(m.group(2)) <= sum(1 for _ in handle)
    except (OSError, UnicodeError): return False


def render(args):
    work = Path(args.work).resolve()
    data = json.loads((work/"schema.json").read_text(encoding="utf-8"))
    ann = json.loads((work/"annotations.json").read_text(encoding="utf-8"))
    root = Path(data["repo"]).resolve()
    names = {t["name"] for t in data["tables"]}
    if len(names) != len(data["tables"]): raise ValueError("duplicate table names prevent a precise map")
    errors = []
    for table, purpose in ann.get("purposes",{}).items():
        if table not in names: errors.append(f"unknown purpose table: {table}")
        if not isinstance(purpose,dict) or not purpose.get("text") or not purpose.get("evidence"):
            errors.append(f"purpose needs text and evidence: {table}")
            continue
        if len(purpose["text"]) > 240: errors.append(f"purpose too long: {table}")
        for loc in purpose["evidence"]:
            if not valid_loc(root,loc): errors.append(f"invalid evidence: {loc}")
    for table, category in ann.get("categories",{}).items():
        if table not in names or not isinstance(category,str) or not category.strip() or len(category)>80:
            errors.append(f"invalid category: {table}")
    inferred=[]
    for edge in ann.get("inferred_edges",[]):
        if edge.get("from") not in names or edge.get("to") not in names or edge.get("from")==edge.get("to"):
            errors.append(f"invalid inferred edge: {edge}"); continue
        if not edge.get("evidence") or any(not valid_loc(root,x) for x in edge["evidence"]):
            errors.append(f"invalid inferred edge evidence: {edge}"); continue
        inferred.append({**edge,"kind":"inferred","loc":edge["evidence"][0]})
    pipeline=ann.get("pipeline",[])
    if not isinstance(pipeline,list):
        errors.append("pipeline must be a list"); pipeline=[]
    for i,step in enumerate(pipeline):
        if not isinstance(step,dict) or not step.get("title") or not step.get("summary") or not step.get("tables") or not step.get("evidence"):
            errors.append(f"pipeline step {i+1} needs title, summary, tables and evidence"); continue
        if any(t not in names for t in step["tables"]): errors.append(f"pipeline step {i+1} has an unknown table")
        if any(not valid_loc(root,loc) for loc in step["evidence"]): errors.append(f"pipeline step {i+1} has invalid evidence")
        if len(step["title"])>80 or len(step["summary"])>240: errors.append(f"pipeline step {i+1} is too long")
    for edge in data["edges"]:
        if edge["from"] not in names or edge["to"] not in names:
            errors.append(f"relationship target missing: {edge['from']} → {edge['to']}")
    if errors: raise ValueError("\n".join(errors))
    data["edges"] += inferred
    add_cardinality(data)
    categories={**data.get("categories",{}),**ann.get("categories",{})}
    data["groups"],data["group_labels"] = groups_for(data["tables"],data["edges"],categories)
    data["purposes"] = ann.get("purposes",{})
    data["pipeline"] = pipeline
    output = Path(args.output).resolve() if args.output else root/"docs"/"schema_explorer.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data,ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    template = (Path(__file__).resolve().parent.parent/"assets"/"template.html").read_text(encoding="utf-8")
    output.write_text(template.replace("__SCHEMA_DATA__",payload),encoding="utf-8")
    md_path=getattr(args,"markdown",None)
    md_output = Path(md_path).resolve() if md_path else root/"SCHEMA.md"
    md_output.parent.mkdir(parents=True,exist_ok=True)
    html_label=output.relative_to(root).as_posix() if output.is_relative_to(root) else str(output)
    md_output.write_text(schema_markdown(data,html_label),encoding="utf-8")
    unverified = len(names)-len(data["purposes"])
    print(f"HTML: {output}\nMarkdown: {md_output}\nTables: {len(names)}; declared relationships: {len(data['edges'])-len(inferred)}; inferred relationships: {len(inferred)}; unverified purposes: {unverified}; warnings: {len(data['warnings'])}")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    a=sub.add_parser("extract"); a.add_argument("repo"); a.add_argument("schema"); a.add_argument("--out")
    b=sub.add_parser("render"); b.add_argument("work"); b.add_argument("-o","--output"); b.add_argument("--markdown")
    args=p.parse_args()
    try: extract(args) if args.command=="extract" else render(args)
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr); return 1
    return 0


if __name__=="__main__": sys.exit(main())
