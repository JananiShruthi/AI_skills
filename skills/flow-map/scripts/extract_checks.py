#!/usr/bin/env python3
"""Extract checks, branches, error paths, calls and data flow for a Python entry point.

Stdlib only (Python 3.9+). Emits JSON on stdout for the flow-map skill.

Usage:
  extract_checks.py ROOT --list-entries
  extract_checks.py ROOT --entry path/to/file.py:func [--depth 3]
  extract_checks.py ROOT --entry path/to/file.py:Class.method
"""
import argparse
import ast
import builtins
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".tox", ".nox", "build", "dist",
             ".mypy_cache", ".pytest_cache", "site-packages", ".eggs", ".venv", "venv", "env"}
ROUTE_DECORATORS = {"get", "post", "put", "delete", "patch", "route", "api_route", "websocket",
                    "command", "task", "callback", "group", "subscriber", "consumer", "on_event",
                    "middleware", "exception_handler"}
AUTH_HINTS = re.compile(r"auth|login|permission|require|role|scope|token|verify|guard", re.I)
VALIDATOR_DECORATORS = {"validator", "field_validator", "model_validator", "root_validator"}
BROAD_EXCEPTIONS = {"Exception", "BaseException"}
TERMINATORS = {"sys.exit", "exit", "quit", "os._exit", "os.abort"}
LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
TIMERS = {"time.perf_counter", "time.time", "time.monotonic", "time.process_time", "perf_counter",
          "monotonic", "datetime.now", "datetime.datetime.now", "datetime.utcnow", "datetime.datetime.utcnow"}
DUPLICATE_KINDS = {"if", "ternary", "assert"}  # loop headers and except clauses repeat legitimately
BUILTINS = set(dir(builtins))
MAX_TEXT = 120
# cheap text filter so `entries` only parses files that could hold an entry point
ENTRY_HINT = re.compile(r"@[\w.]*\b(" + "|".join(ROUTE_DECORATORS) + r")\b|__main__|\bdef main\b|^[A-Za-z_][\w.]*\(",
                        re.M)


def text(node):
    try:
        s = ast.unparse(node)
    except Exception:
        s = type(node).__name__
    s = " ".join(s.split())
    return s if len(s) <= MAX_TEXT else s[: MAX_TEXT - 3] + "..."


# --------------------------------------------------------------------------- project index

def is_venv(path):
    return os.path.exists(os.path.join(path, "pyvenv.cfg"))


def iter_py_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")
                       and not is_venv(os.path.join(dirpath, d))]
        for f in filenames:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


def module_name(root, path):
    rel = os.path.relpath(path, root)[:-3].replace(os.sep, ".")
    if rel.endswith(".__init__"):
        rel = rel[: -len(".__init__")]
    return rel


def is_test_file(path):
    base = os.path.basename(path)
    parts = path.split(os.sep)
    return base.startswith("test_") or base.endswith("_test.py") or base == "conftest.py" \
        or "tests" in parts or "test" in parts


class Module:
    """One source file. Reading and parsing are lazy: big repos only pay for the files the entry point reaches."""
    LAZY = {"tree", "error", "functions", "classes", "imports", "globals", "bases", "attr_types"}

    def __init__(self, root, path):
        self.path = path
        self.rel = os.path.relpath(path, root)
        self.name = module_name(root, path)
        self._loaded = False

    def __getattr__(self, attr):
        if attr == "source":
            with open(self.path, encoding="utf-8", errors="replace") as fh:
                self.source = fh.read()
            return self.source
        if attr in Module.LAZY:
            self._load()
            return self.__dict__[attr]
        raise AttributeError(attr)

    def _load(self):
        self._loaded = True
        self.functions = {}   # qualname -> FunctionDef (nested functions as outer.inner)
        self.classes = {}     # name -> ClassDef
        self.imports = {}     # local name -> ("module", dotted) | ("symbol", dotted_module, name)
        self.globals = {}     # module-level name -> origin description of assigned call
        self.bases = {}       # class name -> [base class expressions]
        self.attr_types = {}  # class name -> {attribute: expression of the class it was built from}
        try:
            self.tree = ast.parse(self.source, filename=self.path)
        except SyntaxError as exc:
            self.tree = None
            self.error = str(exc)
            return
        self.error = None
        self._index()

    def _nested(self, prefix, node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}.{child.name}"
                self.functions.setdefault(q, child)
                self._nested(q, child)
            elif not isinstance(child, (ast.ClassDef, ast.Lambda)):
                self._nested(prefix, child)

    def _origin(self, func):
        """Best-effort dotted origin of a callable, e.g. YOLO -> ultralytics.YOLO."""
        root = root_name(func)
        if root in self.imports:
            imp = self.imports[root]
            base = imp[1] if imp[0] == "module" else f"{imp[1]}.{imp[2]}"
            rest = text(func).split(".", 1)[1] if "." in text(func) else ""
            return f"{base}.{rest}" if rest else base
        return text(func)

    def _index(self):
        pkg = self.name.rsplit(".", 1)[0] if "." in self.name else ""
        if self.path.endswith("__init__.py"):
            pkg = self.name
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
                self._nested(node.name, node)
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
                self.bases[node.name] = node.bases
                attrs = self.attr_types[node.name] = {}
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.functions[f"{node.name}.{sub.name}"] = sub
                        self._nested(f"{node.name}.{sub.name}", sub)
                        if sub.name == "__init__":
                            for st in ast.walk(sub):
                                if isinstance(st, ast.Assign) and isinstance(st.value, ast.Call):
                                    for t in st.targets:
                                        if isinstance(t, ast.Attribute) and root_name(t) == "self" \
                                                and isinstance(t.value, ast.Name):
                                            attrs[t.attr] = st.value.func
                    elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                        attrs[sub.target.id] = sub.annotation  # dataclass / pydantic style fields
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        self.imports[a.asname] = ("module", a.name)
                    else:
                        self.imports[a.name.split(".")[0]] = ("module", a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level:
                    base = pkg.split(".") if pkg else []
                    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                    mod = ".".join([p for p in base + ([mod] if mod else []) if p])
                for a in node.names:
                    self.imports[a.asname or a.name] = ("symbol", mod, a.name)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self.globals[t.id] = self._origin(node.value.func)


class Project:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.modules = {}
        self.test_files = []
        for p in iter_py_files(self.root):
            m = Module(self.root, p)
            self.modules[m.name] = m
            if is_test_file(m.rel):
                self.test_files.append(m)

    def find_module(self, dotted):
        if dotted in self.modules:
            return self.modules[dotted]
        # scripts run from their own directory import siblings by short name
        cands = [m for n, m in self.modules.items() if n.endswith("." + dotted)]
        return cands[0] if len(cands) == 1 else None

    def module_by_path(self, path):
        ap = os.path.abspath(os.path.join(self.root, path)) if not os.path.isabs(path) else path
        for m in self.modules.values():
            if os.path.abspath(m.path) == ap:
                return m
        return None

    def parse_errors(self):
        """Files that were needed but could not be parsed (unparsed files are never read)."""
        return [{"file": m.rel, "error": m.error} for m in self.modules.values() if m._loaded and m.error]

    def resolve_class(self, module, expr):
        """(module, class name) for an expression naming a project class, e.g. `Base`, `helpers.Client`,
        `Optional[Client]`; None if it is not a project class."""
        if isinstance(expr, ast.Subscript):  # Optional[X], list[X] -> X
            for n in ast.walk(expr.slice):
                hit = self.resolve_class(module, n) if isinstance(n, (ast.Name, ast.Attribute)) else None
                if hit:
                    return hit
            return None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):  # "Client" forward reference
            try:
                expr = ast.parse(expr.value, mode="eval").body
            except SyntaxError:
                return None
        if module.tree is None:
            return None
        if isinstance(expr, ast.Name):
            if expr.id in module.classes:
                return module, expr.id
            imp = module.imports.get(expr.id)
            if imp and imp[0] == "symbol":
                target = self.find_module(imp[1])
                if target and imp[2] in target.classes:
                    return target, imp[2]
        elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            imp = module.imports.get(expr.value.id)
            dotted = None
            if imp and imp[0] == "module":
                dotted = imp[1]
            elif imp and imp[0] == "symbol":
                dotted = f"{imp[1]}.{imp[2]}" if imp[1] else imp[2]
            target = self.find_module(dotted) if dotted else None
            if target and target.tree is not None and expr.attr in target.classes:
                return target, expr.attr
        return None

    def find_method(self, module, cls, meth, seen=None):
        """Project key `module:Class.meth` for a method, following project base classes."""
        seen = seen or set()
        if (module.name, cls) in seen:
            return None
        seen.add((module.name, cls))
        if f"{cls}.{meth}" in module.functions:
            return f"{module.name}:{cls}.{meth}"
        for b in module.bases.get(cls, []):
            hit = self.resolve_class(module, b)
            if hit:
                found = self.find_method(hit[0], hit[1], meth, seen)
                if found:
                    return found
        return None

    def find_attr_class(self, module, cls, attr, seen=None):
        """(module, class) of `self.<attr>` when __init__ builds it from a project class."""
        seen = seen or set()
        if (module.name, cls) in seen:
            return None
        seen.add((module.name, cls))
        expr = module.attr_types.get(cls, {}).get(attr)
        if expr is not None:
            return self.resolve_class(module, expr)
        for b in module.bases.get(cls, []):
            hit = self.resolve_class(module, b)
            if hit:
                found = self.find_attr_class(hit[0], hit[1], attr, seen)
                if found:
                    return found
        return None


# --------------------------------------------------------------------------- call resolution

class Resolver:
    def __init__(self, project, module, func_qualname, func_node):
        self.p = project
        self.m = module
        self.qual = func_qualname
        first = func_qualname.split(".")[0]
        self.cls = first if "." in func_qualname and first in module.classes else None
        self.locals = {}     # local var -> origin text (for external values)
        self.instances = {}  # local var / param -> (module, class) of a project class instance
        args = func_node.args.posonlyargs + func_node.args.args + func_node.args.kwonlyargs
        self.params = {a.arg for a in args}
        if func_node.args.vararg:
            self.params.add(func_node.args.vararg.arg)
        if func_node.args.kwarg:
            self.params.add(func_node.args.kwarg.arg)
        for a in args:
            hit = project.resolve_class(module, a.annotation) if a.annotation is not None else None
            if hit:
                self.instances[a.arg] = hit

    def nested_function(self, name):
        """`outer.name` if `name` is a function defined inside this function or an enclosing one."""
        parts = self.qual.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in self.m.classes:
                break  # class bodies are not an enclosing scope for bare names
            if f"{prefix}.{name}" in self.m.functions:
                return f"{prefix}.{name}"
        return None

    def class_target(self, mod, cls):
        init = self.p.find_method(mod, cls, "__init__")
        return init or f"{mod.name}:{cls}"

    def project_symbol(self, mod, name):
        target = self.p.find_module(mod)
        if not target:
            return None
        if name in target.functions:
            return f"{target.name}:{name}"
        if name in target.classes:
            init = f"{name}.__init__"
            return f"{target.name}:{init}" if init in target.functions else f"{target.name}:{name}"
        return None

    def note_assign(self, targets, value, annotation=None):
        hit = self.p.resolve_class(self.m, annotation) if annotation is not None else None
        if not hit and isinstance(value, ast.Call):
            hit = self.p.resolve_class(self.m, value.func)
        if hit:
            for t in targets:
                if isinstance(t, ast.Name):
                    self.instances[t.id] = hit
            return
        if not isinstance(value, ast.Call):
            return
        r = self.resolve(value)
        if r["kind"] == "external":
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        self.locals[n.id] = r["target"].split(" (via ")[0]

    def note_for(self, target, iter_node):
        root = root_name(iter_node)
        if root and root in self.locals:
            for n in ast.walk(target):
                if isinstance(n, ast.Name):
                    self.locals[n.id] = self.locals[root]

    def resolve(self, call):
        f = call.func
        if isinstance(f, ast.Name):
            n = f.id
            nested = self.nested_function(n)
            if nested:
                return {"kind": "project", "target": f"{self.m.name}:{nested}"}
            if n in self.m.functions:
                return {"kind": "project", "target": f"{self.m.name}:{n}"}
            if n in self.m.classes:
                return {"kind": "project", "target": self.class_target(self.m, n)}
            if n in self.m.imports:
                imp = self.m.imports[n]
                if imp[0] == "symbol":
                    cls = self.p.resolve_class(self.m, f)
                    if cls:
                        return {"kind": "project", "target": self.class_target(*cls)}
                    hit = self.project_symbol(imp[1], imp[2])
                    if hit:
                        return {"kind": "project", "target": hit}
                    return {"kind": "external", "target": f"{imp[1]}.{imp[2]}"}
                return {"kind": "external", "target": imp[1]}
            if n in self.locals:
                return {"kind": "external", "target": f"{self.locals[n]} (via {n})"}
            if n in self.m.globals:
                origin = self.m.globals[n]
                return {"kind": "external", "target": f"{origin} (via {n})"}
            if n in BUILTINS:
                return {"kind": "builtin", "target": n}
            return {"kind": "unresolved", "target": text(f),
                    "reason": "callable is a parameter or local variable" if n in self.params
                    else "name not found statically"}
        if isinstance(f, ast.Attribute):
            root = root_name(f)
            if root in ("self", "cls") and self.cls and isinstance(f.value, ast.Name):
                hit = self.p.find_method(self.m, self.cls, f.attr)
                if hit:
                    return {"kind": "project", "target": hit}
                return {"kind": "unresolved", "target": text(f),
                        "reason": "method not defined on this class or its project base classes "
                                  "(inherited from a library, or dynamic)"}
            if root == "self" and self.cls and isinstance(f.value, ast.Attribute) \
                    and isinstance(f.value.value, ast.Name):
                owner = self.p.find_attr_class(self.m, self.cls, f.value.attr)
                hit = self.p.find_method(owner[0], owner[1], f.attr) if owner else None
                if hit:
                    return {"kind": "project", "target": hit}
            if isinstance(f.value, ast.Name) and f.value.id in self.instances:
                hit = self.p.find_method(*self.instances[f.value.id], f.attr)
                if hit:
                    return {"kind": "project", "target": hit}
            if root in self.m.imports:
                imp = self.m.imports[root]
                if imp[0] == "module" and isinstance(f.value, ast.Name):
                    hit = self.project_symbol(imp[1], f.attr)
                    if hit:
                        return {"kind": "project", "target": hit}
                if imp[0] == "symbol" and self.p.find_module(f"{imp[1]}.{imp[2]}") \
                        and isinstance(f.value, ast.Name):
                    hit = self.project_symbol(f"{imp[1]}.{imp[2]}", f.attr)
                    if hit:
                        return {"kind": "project", "target": hit}
                origin = imp[1] if imp[0] == "module" else f"{imp[1]}.{imp[2]}"
                if self.p.find_module(origin) is None:
                    return {"kind": "external", "target": f"{origin}.{text(f).split('.', 1)[-1]}"}
            if root in self.locals:
                return {"kind": "external", "target": f"{self.locals[root]} (via {text(f)})"}
            if root in self.m.globals:
                return {"kind": "external", "target": f"{self.m.globals[root]} (via {text(f)})"}
            return {"kind": "method", "target": text(f)}
        return {"kind": "unresolved", "target": text(f), "reason": "dynamic callable expression"}


def root_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


# --------------------------------------------------------------------------- flow extraction

def cond_tags(cond):
    tags = set()
    for n in ast.walk(cond):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            if n.func.id in ("isinstance", "issubclass", "callable", "hasattr"):
                tags.add("type_check")
            if n.func.id == "len":
                tags.add("empty_check")
        if isinstance(n, ast.Compare):
            for op, right in zip(n.ops, n.comparators):
                if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(right, ast.Constant) and right.value is None:
                    tags.add("none_check")
                if isinstance(op, (ast.In, ast.NotIn)):
                    tags.add("membership")
                if isinstance(op, (ast.Gt, ast.GtE, ast.Lt, ast.LtE)) and \
                        any(isinstance(x, ast.Constant) and isinstance(x.value, (int, float))
                            and not isinstance(x.value, bool) for x in (n.left, right)):
                    tags.add("threshold")
    if isinstance(cond, ast.UnaryOp) and isinstance(cond.op, ast.Not) or isinstance(cond, ast.Name):
        tags.add("truthiness")
    return sorted(tags)


def exits_of(body):
    if not body:
        return "none"
    last = body[-1]
    return {ast.Return: "return", ast.Raise: "raise", ast.Continue: "continue",
            ast.Break: "break"}.get(type(last), "falls_through")


def handler_swallows(h):
    return not any(isinstance(n, ast.Raise) for n in ast.walk(h))


def _message(arg):
    """Readable start of a log message: literal text, with f-string fields shown as …"""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        msg = arg.value
    elif isinstance(arg, ast.JoinedStr):
        msg = "".join(v.value if isinstance(v, ast.Constant) else "…" for v in arg.values)
    else:
        return ""
    msg = " ".join(msg.split())
    return msg if len(msg) <= 48 else msg[:47] + "…"


def log_label(call):
    """Auto label for logging and print calls, so the agent does not have to write one."""
    if not isinstance(call, ast.Call):
        return None
    f = call.func
    if isinstance(f, ast.Name) and f.id == "print":
        msg = _message(call.args[0]) if call.args else ""
        return f"Print: {msg}" if msg else "Print output"
    if isinstance(f, ast.Attribute) and f.attr in LOG_LEVELS and "log" in text(f.value).lower():
        args = call.args[1:] if f.attr == "log" else call.args
        msg = _message(args[0]) if args else ""
        level = "message" if f.attr == "log" else f.attr
        return f"Log {level}: {msg}" if msg else f"Log {level}"
    return None


def is_generator(fn):
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.Yield, ast.YieldFrom)):
            return True
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(n))
    return False


class FlowExtractor:
    def __init__(self, resolver, rel):
        self.r = resolver
        self.rel = rel
        self.checks = []
        self.calls = []
        self.notes = []
        self.counter = 0
        self.conds_seen = {}

    def loc(self, node):
        return f"{self.rel}:{node.lineno}"

    def add_check(self, kind, node, **kw):
        self.counter += 1
        cid = f"c{self.counter}"
        entry = {"id": cid, "kind": kind, "line": node.lineno, "loc": self.loc(node), **kw}
        self.checks.append(entry)
        cond = kw.get("cond")
        if cond and kind in DUPLICATE_KINDS:
            if cond in self.conds_seen:
                self.notes.append({"type": "duplicate_condition", "loc": self.loc(node),
                                   "detail": f"`{cond}` also checked at {self.conds_seen[cond]}"})
            else:
                self.conds_seen[cond] = self.loc(node)
        return cid

    def expr_calls(self, node):
        out = []
        chained = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                inner = n.func
                while isinstance(inner, (ast.Attribute, ast.Subscript)):
                    inner = inner.value
                    if isinstance(inner, ast.Call):
                        chained.add(id(inner))
                        break
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and id(n) in chained:
                continue
            if isinstance(n, ast.Call):
                r = self.r.resolve(n)
                if r["kind"] == "builtin":
                    continue
                item = {"line": n.lineno, "loc": self.loc(n), "call": text(n), **r}
                self.calls.append(item)
                out.append(item)
            elif isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                for gen in n.generators:
                    for cond in gen.ifs:
                        c = text(cond)
                        out.append({"filter_check": self.add_check(
                            "comprehension_filter", cond, cond=c, tags=cond_tags(cond),
                            over=text(gen.iter))})
            elif isinstance(n, ast.IfExp):
                c = text(n.test)
                out.append({"ternary_check": self.add_check(
                    "ternary", n, cond=c, tags=cond_tags(n.test), true=text(n.body), false=text(n.orelse))})
        return out

    def block(self, stmts):
        out = []
        terminated = None
        for s in stmts:
            if terminated:
                self.notes.append({"type": "unreachable", "loc": self.loc(s),
                                   "detail": f"statement after `{terminated}` can never run"})
                break
            node = self.stmt(s)
            if node:
                out.append(node)
            if isinstance(s, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                terminated = type(s).__name__.lower()
            elif node and node["type"] == "terminate":
                terminated = node["value"]
        return out

    def stmt(self, s):
        if isinstance(s, ast.If):
            cond = text(s.test)
            is_elif = len(s.orelse) == 1 and isinstance(s.orelse[0], ast.If)
            cid = self.add_check("if", s, cond=cond, tags=cond_tags(s.test),
                                 has_else=bool(s.orelse), else_is_elif=is_elif,
                                 true_exits=exits_of(s.body),
                                 false_exits=exits_of(s.orelse) if s.orelse else "falls_through")
            return {"type": "if", "check": cid, "loc": self.loc(s), "cond": cond,
                    "cond_calls": self.expr_calls(s.test),
                    "true": self.block(s.body), "false": self.block(s.orelse)}
        if isinstance(s, (ast.For, ast.AsyncFor)):
            self.r.note_for(s.target, s.iter)
            cid = self.add_check("loop", s, cond=f"for {text(s.target)} in {text(s.iter)}")
            return {"type": "loop", "check": cid, "loc": self.loc(s), "header": f"for {text(s.target)} in {text(s.iter)}",
                    "iter_calls": self.expr_calls(s.iter), "body": self.block(s.body),
                    "else": self.block(s.orelse)}
        if isinstance(s, ast.While):
            cond = text(s.test)
            cid = self.add_check("loop", s, cond=f"while {cond}", tags=cond_tags(s.test))
            return {"type": "loop", "check": cid, "loc": self.loc(s), "header": f"while {cond}",
                    "body": self.block(s.body), "else": self.block(s.orelse)}
        if isinstance(s, (ast.Try,) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ())):
            handlers = []
            for h in s.handlers:
                etype = text(h.type) if h.type else None
                names = {n.id for n in ast.walk(h.type) if isinstance(n, ast.Name)} if h.type else set()
                bare = h.type is None
                broad = bare or bool(names & BROAD_EXCEPTIONS)
                swallows = handler_swallows(h)
                hid = self.add_check("except", h, cond=f"except {etype or ''}".strip(), bare=bare,
                                     broad=broad, swallows=swallows, exits=exits_of(h.body))
                if broad and swallows:
                    self.notes.append({"type": "swallowed_exception", "loc": self.loc(h),
                                       "detail": f"`except {etype or ''}` catches everything and does not re-raise"})
                elif bare:
                    self.notes.append({"type": "bare_except", "loc": self.loc(h),
                                       "detail": "bare `except:` also catches KeyboardInterrupt/SystemExit"})
                handlers.append({"check": hid, "loc": self.loc(h), "type": etype,
                                 "body": self.block(h.body)})
            return {"type": "try", "loc": self.loc(s), "body": self.block(s.body), "handlers": handlers,
                    "lines": [s.body[0].lineno, getattr(s.body[-1], "end_lineno", s.body[-1].lineno)],
                    "else": self.block(s.orelse), "finally": self.block(s.finalbody)}
        if isinstance(s, (ast.With, ast.AsyncWith)):
            items = [text(i) for i in s.items]
            calls = []
            for i in s.items:
                calls += self.expr_calls(i.context_expr)
                if i.optional_vars is not None:
                    self.r.note_assign([i.optional_vars], i.context_expr)
            return {"type": "with", "loc": self.loc(s), "items": items, "calls": calls,
                    "body": self.block(s.body)}
        if hasattr(ast, "Match") and isinstance(s, ast.Match):
            cases = []
            for c in s.cases:
                pat = text(c.pattern) + (f" if {text(c.guard)}" if c.guard else "")
                cid = self.add_check("match_case", c.pattern, cond=f"{text(s.subject)} matches {pat}")
                cases.append({"check": cid, "pattern": pat, "body": self.block(c.body)})
            return {"type": "match", "loc": self.loc(s), "subject": text(s.subject), "cases": cases}
        if isinstance(s, ast.Assert):
            cid = self.add_check("assert", s, cond=text(s.test), tags=cond_tags(s.test),
                                 msg=text(s.msg) if s.msg else None)
            return {"type": "assert", "check": cid, "loc": self.loc(s), "cond": text(s.test)}
        if isinstance(s, ast.Raise):
            return {"type": "raise", "loc": self.loc(s), "exc": text(s.exc) if s.exc else "re-raise",
                    "calls": self.expr_calls(s.exc) if s.exc else []}
        if isinstance(s, ast.Return):
            return {"type": "return", "loc": self.loc(s), "value": text(s.value) if s.value else None,
                    "calls": self.expr_calls(s.value) if s.value else []}
        if isinstance(s, (ast.Continue, ast.Break)):
            return {"type": type(s).__name__.lower(), "loc": self.loc(s)}
        if isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = s.targets if isinstance(s, ast.Assign) else [s.target]
            if s.value is None:
                return None
            calls = self.expr_calls(s.value)
            self.r.note_assign(targets, s.value, getattr(s, "annotation", None))
            item = {"type": "assign", "loc": self.loc(s), "code": text(s), "targets": [text(t) for t in targets],
                    "value": text(s.value), "calls": calls}
            value = s.value.value if isinstance(s.value, ast.Await) else s.value
            if isinstance(value, ast.Call) and text(value.func) in TIMERS and len(targets) == 1:
                item["auto"] = f"Note the time ({text(targets[0])})"
            return item
        if isinstance(s, ast.Expr):
            value = s.value.value if isinstance(s.value, ast.Await) else s.value
            if isinstance(value, (ast.Yield, ast.YieldFrom)):
                code = text(s.value)
                if code.startswith("(yield") and code.endswith(")"):
                    code = code[1:-1]
                return {"type": "yield", "loc": self.loc(s), "value": code,
                        "calls": self.expr_calls(value.value) if value.value else []}
            if isinstance(value, ast.Call) and text(value.func) in TERMINATORS:
                return {"type": "terminate", "loc": self.loc(s), "value": text(s.value)}
            calls = self.expr_calls(s.value)
            if not calls and not any(isinstance(n, ast.Call) for n in ast.walk(s.value)):
                return None  # docstrings and bare names do nothing
            item = {"type": "expr", "loc": self.loc(s), "value": text(s.value), "calls": calls}
            auto = log_label(value)
            if auto:
                item["auto"] = auto
            return item
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return {"type": "nested_def", "loc": self.loc(s), "name": s.name}
        return None


# --------------------------------------------------------------------------- per-function analysis

def decorator_info(project, module, fn):
    decos, auth, validators, route = [], [], [], None
    for d in fn.decorator_list:
        t = text(d)
        decos.append(t)
        name = (d.func if isinstance(d, ast.Call) else d)
        last = name.attr if isinstance(name, ast.Attribute) else getattr(name, "id", "")
        if last in ROUTE_DECORATORS and isinstance(d, ast.Call) and d.args \
                and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str) \
                and d.args[0].value.startswith("/"):
            route = d.args[0].value
        if AUTH_HINTS.search(t) and last not in ROUTE_DECORATORS:
            auth.append({"check": t, "loc": f"{module.rel}:{d.lineno}", "via": "decorator"})
    # FastAPI style: param = Depends(x) / Security(x). Security is always auth; Depends only when it looks like it.
    defaults = fn.args.defaults + [d for d in fn.args.kw_defaults if d is not None]
    for d in defaults:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id in ("Depends", "Security") \
                and (d.func.id == "Security" or AUTH_HINTS.search(text(d))):
            auth.append({"check": text(d), "loc": f"{module.rel}:{d.lineno}", "via": "dependency"})
    # Pydantic models used as parameter types -> their validators are checks too
    for a in fn.args.args + fn.args.kwonlyargs:
        if a.annotation is None:
            continue
        for n in ast.walk(a.annotation):
            if isinstance(n, ast.Name):
                validators += pydantic_validators(project, module, n.id)
    return decos, auth, validators, route


def pydantic_validators(project, module, cls_name):
    target_mod, cls = None, None
    if cls_name in module.classes:
        target_mod, cls = module, module.classes[cls_name]
    elif cls_name in module.imports and module.imports[cls_name][0] == "symbol":
        _, mod, name = module.imports[cls_name]
        tm = project.find_module(mod)
        if tm and name in tm.classes:
            target_mod, cls = tm, tm.classes[name]
    if not cls:
        return []
    out = []
    for sub in cls.body:
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in sub.decorator_list:
                nm = d.func if isinstance(d, ast.Call) else d
                last = nm.attr if isinstance(nm, ast.Attribute) else getattr(nm, "id", "")
                if last in VALIDATOR_DECORATORS:
                    out.append({"model": cls_name, "validator": sub.name, "decorator": text(d),
                                "loc": f"{target_mod.rel}:{sub.lineno}"})
    return out


def analyze_function(project, module, qualname, depth):
    fn = module.functions[qualname]
    res = Resolver(project, module, qualname, fn)
    fx = FlowExtractor(res, module.rel)
    decos, auth, validators, route = decorator_info(project, module, fn)
    flow = fx.block(fn.body)
    params = [{"name": a.arg, "type": text(a.annotation) if a.annotation else None}
              for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs if a.arg not in ("self", "cls")]
    return {
        "id": f"{module.name}:{qualname}",
        "file": module.rel, "line": fn.lineno, "end_line": getattr(fn, "end_lineno", None),
        "qualname": qualname, "depth": depth,
        "params": params, "returns": text(fn.returns) if fn.returns else None,
        "decorators": decos, "auth_checks": auth, "validators": validators, "route": route,
        "generator": is_generator(fn),
        "flow": flow, "checks": fx.checks, "calls": fx.calls, "notes": fx.notes,
    }


# --------------------------------------------------------------------------- tests & coverage

def coverage_lines(root):
    """Return ({abs_path: set(executed lines)}, source) from coverage.xml or .coverage if present."""
    xml_path = os.path.join(root, "coverage.xml")
    if os.path.exists(xml_path):
        try:
            tree = ET.parse(xml_path)
            srcs = [s.text for s in tree.iter("source") if s.text] or [root]
            hits = {}
            for cls in tree.iter("class"):
                fname = cls.get("filename")
                for src in srcs:
                    p = os.path.abspath(os.path.join(src, fname))
                    if os.path.exists(p):
                        break
                lines = {int(l.get("number")) for l in cls.iter("line") if int(l.get("hits", 0)) > 0}
                hits.setdefault(p, set()).update(lines)
            return hits, "coverage.xml"
        except Exception:
            pass
    db = os.path.join(root, ".coverage")
    if os.path.exists(db):
        try:
            con = sqlite3.connect(db)
            files = dict(con.execute("select id, path from file"))
            hits = {}
            try:
                for fid, numbits in con.execute("select file_id, numbits from line_bits"):
                    s = hits.setdefault(os.path.abspath(files[fid]), set())
                    for i, byte in enumerate(numbits):
                        for j in range(8):
                            if byte & (1 << j):
                                s.add(i * 8 + j)
            except sqlite3.OperationalError:
                for fid, line in con.execute("select file_id, lineno from line"):
                    hits.setdefault(os.path.abspath(files[fid]), set()).add(line)
            return hits, ".coverage"
        except Exception:
            pass
    return None, None


MOCKERS = {"patch", "setattr", "object", "patch_object", "delattr"}


def _string_values(node):
    """String constants in a test, with f-string fields replaced by X so route paths still match."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values)
    return None


def route_pattern(path):
    parts = re.split(r"\{[^}]*\}", path.rstrip("/") or "/")
    return re.compile(r"(^|/)" + r"[^/?]+".join(re.escape(p.lstrip("/")) for p in parts) + r"/?(\?.*)?$")


def test_references(project, functions):
    """Where tests call each function, and where they only mock it.

    A function counts as called when a test calls it by name or method name, or (for a route) sends a request
    to its path. Names inside patch(...) / monkeypatch.setattr(...) count as mocked, not tested. Comments and
    docstrings never count. Still a static guess: a call of a same-named method on another class also counts."""
    shorts = {f["id"]: f["qualname"].split(".")[-1] for f in functions}
    for f in functions:
        if shorts[f["id"]] == "__init__":
            shorts[f["id"]] = f["qualname"].split(".")[-2]
    routes = {f["id"]: route_pattern(f["route"]) for f in functions if (f.get("route") or "").strip("/")}
    called = {k: [] for k in shorts}
    mocked = {k: [] for k in shorts}
    for tm in project.test_files:
        src = tm.source
        wanted = [k for k, n in shorts.items() if n in src or k in routes]
        if not wanted or tm.tree is None:
            continue
        mock_args = set()
        for n in ast.walk(tm.tree):
            if not isinstance(n, ast.Call):
                continue
            fname = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if fname in MOCKERS:
                for a in list(n.args) + [kw.value for kw in n.keywords]:
                    mock_args.update(id(x) for x in ast.walk(a))
                    v = _string_values(a)
                    for k in wanted:
                        if v and v.split(".")[-1] == shorts[k]:
                            mocked[k].append(f"{tm.rel}:{n.lineno}")
                continue
            if id(n) in mock_args:
                continue
            for k in wanted:
                if fname == shorts[k]:
                    called[k].append(f"{tm.rel}:{n.lineno}")
        for k, pat in routes.items():
            for n in ast.walk(tm.tree):
                v = _string_values(n)
                if v and pat.search(v.split("://")[-1]):
                    called[k].append(f"{tm.rel}:{n.lineno}")
                    break
    return called, mocked


# --------------------------------------------------------------------------- entry points

def list_entries(project):
    out = []
    for m in project.modules.values():
        if is_test_file(m.rel) or not ENTRY_HINT.search(m.source) or m.tree is None:
            continue
        for q, fn in m.functions.items():
            for d in fn.decorator_list:
                nm = d.func if isinstance(d, ast.Call) else d
                last = nm.attr if isinstance(nm, ast.Attribute) else getattr(nm, "id", "")
                if last in ROUTE_DECORATORS:
                    out.append({"entry": f"{m.rel}:{q}", "reason": f"decorator @{text(d)}", "line": fn.lineno})
                    break
            if q.split(".")[-1] == "main":
                out.append({"entry": f"{m.rel}:{q}", "reason": "function named main", "line": fn.lineno})
        for node in m.tree.body:
            calls = []
            if isinstance(node, ast.If) and "__name__" in text(node.test) and "__main__" in text(node.test):
                calls = [n for s in node.body for n in ast.walk(s) if isinstance(n, ast.Call)]
                why = "called under if __name__ == '__main__'"
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                calls = [node.value]
                why = "called at module level (script)"
            for c in calls:
                if isinstance(c.func, ast.Name) and c.func.id in m.functions:
                    out.append({"entry": f"{m.rel}:{c.func.id}", "reason": why, "line": c.lineno})
    seen, uniq = set(), []
    for e in out:
        if e["entry"] not in seen:
            seen.add(e["entry"])
            uniq.append(e)
    return uniq


# --------------------------------------------------------------------------- main

def analyze(project, entry, max_depth):
    path, _, qual = entry.partition(":")
    module = project.module_by_path(path)
    if module is None:
        sys.exit(f"error: {path} not found under {project.root}")
    if module.tree is None:
        sys.exit(f"error: cannot parse {path}: {module.error}")
    if qual not in module.functions:
        sys.exit(f"error: {qual} not found in {path}. Functions: {', '.join(sorted(module.functions))}")

    functions, collapsed, queue, seen = {}, [], [(module, qual, 0)], set()
    while queue:
        mod, q, d = queue.pop(0)
        key = f"{mod.name}:{q}"
        if key in seen:
            continue
        seen.add(key)
        info = analyze_function(project, mod, q, d)
        functions[key] = info
        for c in info["calls"]:
            if c["kind"] != "project":
                continue
            tmod_name, _, tq = c["target"].partition(":")
            tmod = project.modules.get(tmod_name)
            if not tmod or tq not in tmod.functions:
                continue
            if d + 1 > max_depth:
                collapsed.append({"from": key, "target": c["target"], "loc": c["loc"]})
            else:
                queue.append((tmod, tq, d + 1))

    cov, cov_src = coverage_lines(project.root)
    called, mocked = test_references(project, list(functions.values()))
    for f in functions.values():
        f["test_refs"] = called[f["id"]] or mocked[f["id"]]
        if cov is not None:
            lines = cov.get(os.path.abspath(os.path.join(project.root, f["file"])))
            for ch in f["checks"]:
                ch["covered"] = None if lines is None else ch["line"] in lines  # None: file not measured

    # Test reach: direct reference in tests, or reached from a function that is referenced.
    edges = {k: [c["target"] for c in f["calls"] if c["kind"] == "project" and c["target"] in functions]
             for k, f in functions.items()}
    for f in functions.values():
        f["test_reach"] = "direct" if called[f["id"]] else "mocked" if mocked[f["id"]] else "none"
    changed = True
    while changed:
        changed = False
        for k, f in functions.items():
            if f["test_reach"] in ("none", "mocked"):
                continue
            for t in edges[k]:
                if functions[t]["test_reach"] == "none":
                    functions[t]["test_reach"] = f"indirect via {f['qualname']}"
                    changed = True

    unresolved = [{"in": f["id"], **c} for f in functions.values() for c in f["calls"] if c["kind"] == "unresolved"]
    external = sorted({c["target"] for f in functions.values() for c in f["calls"] if c["kind"] == "external"})
    notes = [{"in": f["id"], **n} for f in functions.values() for n in f["notes"]]
    for f in functions.values():
        checks = [ch for ch in f["checks"] if ch["kind"] != "loop"]
        if cov is not None:
            missed = [ch for ch in checks if ch.get("covered") is False]
            if missed:
                notes.append({"in": f["id"], "type": "untested_check", "loc": missed[0]["loc"],
                              "detail": f"{len(missed)} check line(s) never executed per {cov_src}: "
                                        + ", ".join(f"`{ch.get('cond', ch['kind'])}` ({ch['loc']})" for ch in missed)})
        elif checks and f["test_reach"] == "none":
            notes.append({"in": f["id"], "type": "untested_check", "loc": f"{f['file']}:{f['line']}",
                          "detail": f"`{f['qualname']}` ({len(checks)} checks) is not called by any test, "
                                    "directly or through its callers"})
        elif checks and f["test_reach"] == "mocked":
            notes.append({"in": f["id"], "type": "untested_check", "loc": f"{f['file']}:{f['line']}",
                          "detail": f"`{f['qualname']}` ({len(checks)} checks) is only mocked in tests "
                                    f"({f['test_refs'][0]}), so its own checks never run there"})

    total_checks = sum(len(f["checks"]) for f in functions.values())
    return {
        "root": project.root, "entry": entry, "max_depth": max_depth,
        "test_method": f"line coverage from {cov_src}" if cov_src else
                       ("calls found in test files (static guess)" if project.test_files else "no test files found"),
        "test_files": [t.rel for t in project.test_files],
        "stats": {"functions": len(functions), "checks": total_checks,
                  "calls": sum(len(f["calls"]) for f in functions.values())},
        "functions": functions, "collapsed_beyond_depth": collapsed,
        "unresolved": unresolved, "external": external, "review_notes": notes,
        "parse_errors": project.parse_errors(),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="project root directory")
    ap.add_argument("--entry", help="path/to/file.py:function or path/to/file.py:Class.method (path relative to root)")
    ap.add_argument("--depth", type=int, default=3, help="how many levels of project calls to follow (default 3)")
    ap.add_argument("--list-entries", action="store_true", help="list likely entry points and exit")
    args = ap.parse_args()

    project = Project(args.root)
    if args.list_entries or not args.entry:
        result = {"root": project.root, "entries": list_entries(project)}
    else:
        result = analyze(project, args.entry, args.depth)
    json.dump(result, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
