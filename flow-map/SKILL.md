---
name: flow-map
description: Generate an interactive HTML flow diagram (Mermaid) of a Python entry point showing every check, branch, error path and data flow, with file:line references and a checks table for code review. Use when the user asks for a flow map, control-flow diagram, "what checks happen in X", or wants to trace how data moves through a function or endpoint.
---

Draw a control-flow map of **one Python entry point** (a function, route, CLI command or method) for code review and debugging. Output a single self-contained HTML file: Mermaid diagrams, a checks table, unresolved calls, external calls and review notes.

Read the code; never edit it. The diagram must be **accurate before complete**: never invent a path you can't see in the source.

## 1. Pick the entry point

The user may name a function (`detect_faces`), a route (`POST /anonymize`), a file:function, or nothing.

If it's ambiguous or missing, list candidates and ask which one:

```bash
python3 <skill_dir>/scripts/extract_checks.py <repo_root> --list-entries
```

This finds route/CLI decorators, `main` functions, `if __name__ == "__main__"` calls and module-level script calls. Show the list with `file:line` and the reason, and ask the user to pick. Draw one entry point per run.

## 2. Extract facts with the script

```bash
python3 <skill_dir>/scripts/extract_checks.py <repo_root> --entry <path/to/file.py>:<func_or_Class.method> [--depth N]
```

- `--depth` defaults to 3: how many levels of **project** calls to follow. Use the user's value if they give one.
- Third-party calls are never followed; they appear as `external`.
- The script is stdlib-only (Python 3.9+) and prints JSON. Save it to a temp file if it's large.

What the JSON gives you, per function in `functions`:

- `flow`: an ordered, nested tree of `if` / `loop` / `try` / `with` / `match` / `assert` / `raise` / `return` / `continue` / `break` / `assign` / `expr`. Each item has `loc` (`file:line`), and `check` ids link to `checks`.
- `checks`: every decision point, with `cond`, `kind` and `tags` (`none_check`, `type_check`, `empty_check`, `threshold`, `membership`, `truthiness`).
  - Branch exits are in `true_exits` / `false_exits`.
  - `except` handlers carry `broad` / `bare` / `swallows`.
- `calls`: each call with `kind` = `project` | `external` | `method` | `unresolved`.
- `auth_checks`: auth decorators and FastAPI `Depends` / `Security`.
- `validators`: Pydantic validators on parameter models.
- `params`, `returns`: types for data-flow labels.
- `test_refs`, `test_reach`: test evidence for the function.

Top level:

- `collapsed_beyond_depth`: calls past `--depth`.
- `unresolved`, `external`: call lists.
- `review_notes`: structural findings.
- `test_method`: how "tested" was determined.
- `stats`: counts.

**The script is your checklist, not your diagram.**
1. Open each analysed function's source (`file`, `line`–`end_line`) and read it. You need it to understand what each check *means* and what data moves where.
2. Every entry in every `checks` list must appear in the diagram and in the table. Count them at the end.
3. If you notice a check the script missed (e.g. inside a lambda or a dynamic validator), add it and mark it "(found by reading)".

## 3. Build the Mermaid diagrams

Use `flowchart TD`. Make one **main diagram** for the entry point, with each followed project function as a `subgraph` titled `qualname · file:line`.

### Node labels: plain English first

Every node label has up to three lines, from most to least readable:

1. **What it does, in plain English**, bold: what a reviewer would say out loud. Describe the *purpose*, not the syntax: "Read the input image", not "Call cv2.imread". Checks are phrased as **questions**: "Did the donor image have no faces?", "Is the box empty?". Exits say what the caller gets back: "Stop: return nothing".
2. **The code**, in `<code>`: the actual expression, shortened if long (the table keeps the full text).
3. **Where**, in `<small>`: `file:line`.

```
x1["<b>Read the input image</b><br/><code>cv2.imread(image_path)</code><br/><small>face_blur.py:8</small>"]:::ext
```

Keep line 1 under ~40 characters, and use the domain language of the code (faces, invoices, tokens…), not generic words like "process data". Omit line 2 only for pure data nodes (inputs/outputs) where the code adds nothing. Line 3 is required on every node except data inputs/outputs.

### Node vocabulary

The style classes are injected by the template, so don't write `classDef`. Below, `L(...)` means the three-line label above.

| Element | Mermaid shape | Class | Example line 1 |
|---|---|---|---|
| Data input (params, file/request read) | `id[/"..."/]` | `io` | `<b>Inputs</b><br/>image path, donor face path, output path` |
| Check (if / while / assert / except / filter / ternary / match case / auth / validator) | `id{"L(...)"}` | `check` | "Did the donor image have no faces?" |
| Loop | `id{{"L(...)"}}` | `loop` | "For each detected face" |
| Processing step / project call | `id["L(...)"]` | none | "Cut the donor face out" |
| External library call | `id["L(...)"]` | `ext` | "Resize donor face to fit" |
| `raise` / error exit / swallowed error | `id(["L(...)"])` | `error` | "Crash: no face in donor image" |
| `return` / normal exit | `id(["L(...)"])` | `exit` | "Stop: report no faces found" |
| Data output (file written, response) | `id[/"..."/]` | `io` | `<b>Output</b><br/>image with faces replaced` |
| Collapsed function (too deep / too big) | `id[["L(...)"]]` | `fn` | "Swap faces ▸ 14 checks (click)" |
| Unresolved call | `id["L(...)"]` | `unres` | "? Calls a handler chosen at runtime" |

### Rules

- **Edges from checks** are labelled with the answer to the question: `|yes|` / `|no|`, or the specific outcome (`|"no faces"|`, `|"score too low"|`, `|ValueError|`). Loops get `|"next face"|` into the body and `|"all done"|` out.
- **Data flow goes on edges**, in plain words plus type where known: `-->|"the image · ndarray, or None if unreadable"|`. Take types from annotations, or infer them from the code (e.g. `cv2.imread` → `ndarray`). Don't label every edge, only where the data changes or matters.
- **Unresolved calls** use a dashed edge `-.->` into an `unres` node. Never guess the target.
- **External calls** are single grey nodes. Never expand library internals.
- **Swallowed exceptions:** an `except` that swallows an error, then flows on as if it succeeded, is an `error`-class node whose edge says what it pretends happened (e.g. `|"returns [] — looks like 'no faces'"|`).
- **Auth and validators:** draw `auth_checks` and `validators` as checks right after the entry input, before the body.
- **Subgraph titles** stay short, because Mermaid clips long cluster titles: `subgraph fn_blur_faces["blur_faces · face_blur.py:7"]`. Put the plain-English summary of the function in the section `<h3>` instead: `<h3>Replace every face in a photo with a donor face · <code>blur_faces</code> · face_blur.py:7</h3>`.

### Mermaid syntax safety

Broken diagrams are the most common failure, so follow these rules strictly:
- **Always** wrap labels in double quotes: `id["..."]`, `id{"..."}`, `-->|"..."|`.
- Inside labels, replace `"` with `#quot;`, `<` with `#lt;`, `>` with `#gt;` **in the text** (the code especially). The only raw HTML tags allowed are `<b>`, `<code>`, `<small>` and `<br/>`. Avoid `{}`, `[]` and `()` inside edge labels.
- Node ids must be plain `[A-Za-z0-9_]`: e.g. `c1`, `s3`, `e2`, `fn_detect_faces`. Never use `end`, `graph`, `subgraph`, `class` or `style` as ids.
- Subgraph syntax: `subgraph fn_detect_faces["detect_faces · detector.py:7"]` … `end`.
- Collapsed-function links: `click f1 href "#fn-replace_faces"`. Ids in the href must match a section id.

### Size

Count nodes. If the main diagram would exceed **~60 nodes**:
1. Collapse each project function below the entry into a single `fn` node, labelled with its name and check count, and link it with `click`.
2. Give each collapsed function its own diagram section further down the page (`id="fn-<name>"`). Apply the rule recursively.

Calls in `collapsed_beyond_depth` always become `fn` nodes with a note "not expanded (depth limit N)". They get no section.

## 4. Write the HTML

Copy `<skill_dir>/assets/template.html` and replace these placeholders. Everything else in the template stays as-is: CDN versions, styles, script.

- `__TITLE__`: the entry point, e.g. `face_blur.py:blur_faces` (appears twice).
- `__META__`: one line: repo root, generated date, depth, and the stats (functions / checks / calls).
- `__DIAGRAMS__`: one block per diagram:

  ```html
  <section id="fn-<name>">
    <h3>Replace every face in a photo with a donor face · <code>blur_faces</code> · face_blur.py:7</h3>
    <div class="diagram"><script type="text/plain">
  flowchart TD
    ...
  </script></div>
  </section>
  ```

  Mermaid source goes in verbatim, with no HTML escaping. It just must not contain `</script>`. The main diagram's section is `id="fn-main"`.
- `__TEST_METHOD__`: the JSON's `test_method`.
- `__CHECK_ROWS__`: one `<tr>` per check, in flow order, numbered to match the diagram ids (`c1`, `c2`…). Columns:
  - **Check:** the full condition in `<code>`, HTML-escaped.
  - **Kind:** with `<span class="tag">` tags.
  - **Location:** `file:line`.
  - **If true →** / **If false →:** what happens next, in plain words (e.g. "returns `{"status": "no_faces"}`", "skips this box (`continue`)").
  - **Tested?:**
    - If coverage was available: `<span class="yes">✓ line ran</span>` or `<span class="no">✗ never ran</span>`.
    - Otherwise from `test_reach`: `<span class="yes">✓ referenced in tests/test_api.py:12</span>`, `<span class="indirect">~ indirect via anonymize</span>` or `<span class="no">✗ no test reference</span>`.
- `__UNRESOLVED__`: a `<ul>` with each unresolved call, its location and why it couldn't be resolved, plus what a reviewer should check. If there are none: `<p class="empty">None: every call was resolved.</p>`.
- `__EXTERNAL__`: a `<ul>` of external libraries/functions and where they're called. Deduplicate the JSON's "(via …)" variants into one entry per library callable.
- `__REVIEW_NOTES__`: a `<ul class="notes">`. Start with the script's `review_notes`, then add what you found by reading. Each note: ⚠️ + `file:line` + what's wrong + the concrete consequence. Look for:
  - broad or bare `except` that swallows errors (especially when the fallback looks like a valid result)
  - `if` without `else` where the missing branch leaves data in a bad state or falls through silently
  - unreachable code, duplicate conditions
  - checks that can never be true or false (e.g. `len(results) == 0` when the library always returns one result per input)
  - indexing (`[0]`) not guarded by a check
  - hard-coded thresholds, `None` returns that callers don't check
  - checks with no test reference

  These are observations only. Don't propose patches unless asked. If there's nothing to report: `<p class="empty">No issues spotted.</p>`.

Write to `<repo_root>/docs/flows/<entry_name>.html`, where `<entry_name>` is the function name, or `Class_method` for methods. Create the folder if needed. Overwrite an existing file; git keeps history.

## 5. Verify, then report

1. **Check coverage:** confirm that every `checks` id from the JSON appears in a diagram and in the table.
2. **Validate the rendering.** Use whichever is available:
   - **Headless Chrome/Chromium** (preferred, as it tests the real page): run `google-chrome --headless=new --disable-gpu --virtual-time-budget=15000 --window-size=1400,1800 --screenshot=/tmp/flow.png file://<abs path to html>`, then view the screenshot.
     - A red "Mermaid could not render" box means a syntax error. Fix it and re-run.
     - Also look for unreadable tangles that should be collapsed.
   - **mermaid-cli** (needs Node 18+): extract each diagram to a `.mmd` file, append the template's `classDef` lines, then run `npx -y @mermaid-js/mermaid-cli@10.9.1 -i file.mmd -o /tmp/out.svg`.
   - **Neither available:** re-read every label against the syntax-safety rules, and tell the user it wasn't render-checked.
3. **Report to the user** in a few lines:
   - the output path
   - number of functions / checks drawn
   - the top 2–3 review notes
   - anything left unresolved

   Tell them to open the file in a browser.
