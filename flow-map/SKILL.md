---
name: flow-map
description: Generate an interactive HTML flow map (React Flow) of a Python entry point showing every check, branch, error path and data flow, with plain-English labels, file:line references, a checks table and review notes for code review. Use when the user asks for a flow map, control-flow diagram, "what checks happen in X", or wants to trace how data moves through a function or endpoint.
---

Draw a control-flow map of **one Python entry point** (function, route, CLI command or method) as a self-contained interactive HTML page. Use it for code review and debugging. Read the code; never edit it.

A script does all the mechanical work: parsing, call resolution, graph building, layout data, the checks table, test status, auto review notes and the HTML. **Your only job is the human part:** a plain-English label per step, and review notes about what the code *means*. Keep token use low:
- Don't read `graph.json`.
- Don't write HTML or diagram syntax.
- Don't read whole files when the summary already shows the code.

`<skill>` below is this skill's directory.

## 1. Pick the entry point

If the user named one (function, `file.py:func`, `Class.method`, or a route), use it. Otherwise run this, show the list, and ask which entry point to map:

```bash
python3 <skill>/scripts/flow_map.py entries <repo_root>
```

## 2. Extract

```bash
python3 <skill>/scripts/flow_map.py extract <repo_root> --entry <path/to/file.py>:<func_or_Class.method> [--depth N]
```

`--depth` (default 3) is how many levels of project calls to follow. Library internals are never followed.

The output is a compact summary, one line per step:

```
## face_blur:blur_faces  face_blur.py:7-54  depth 0  tests: none
  n2   ext    :8     img = cv2.imread(image_path)   (cv2.imread)
  n6   check  :15    len(fake_results) == 0   {yes→n7, no→n8}
  n7   exit   :17    return
```

- **Line format:** `id`, `kind`, `:line`, then the code. `{...}` shows where branches go, and `(calls X)` marks project calls.
- **Kinds:** `input step call ext unres check loop except error exit`.
- **`[auto: ...]`** means the step already has a label.
- **Line 1** gives the output directory, which holds `graph.json` and is where `labels.json` goes.

## 3. Write `labels.json`

Write `<out_dir>/labels.json`:

```json
{
  "summary": "One or two sentences: what this entry point does, end to end.",
  "functions": { "face_blur:blur_faces": "Replace every face in a photo with a donor face" },
  "nodes": {
    "n2": "Read the photo",
    "n6": "Did the donor image give no results?",
    "n7": "Stop: warn that no donor face was found"
  },
  "edges": { "n2>n3": "img · ndarray, or None if unreadable" },
  "notes": [
    { "loc": "face_blur.py:15", "text": "`len(fake_results) == 0` can never be true: YOLO returns one Results per image. Intended guard: `len(fake_results[0].boxes) == 0`." }
  ]
}
```

### Labels (`nodes`)

Give one label to every node that has no `[auto]` label.
- **Plain English, ≤ 7 words.** Describe the *purpose*, not the syntax: "Read the photo", not "Call cv2.imread".
- **Use the code's domain words** (faces, invoices, tokens), not generic ones like "process data".
- **Checks and loops are questions or "For each …"**: "Is the box empty?", "For each face box". Edge labels already say yes/no.
- **Exits say what the caller gets**: "Stop: report no faces". **Errors say what failed**: "Crash: donor image has no face".

### Function titles (`functions`)

One short plain-English title per function in the summary.

### Data labels (`edges`)

Optional, keyed `"src>dst"`. By default an edge already shows the variable assigned. Override it only where the meaning or type matters, e.g. `"the photo · ndarray, or None if unreadable"`. Aim for 2–5 per function, not every edge.

### Review notes (`notes`)

These are what a reviewer would flag. The summary already lists auto notes (swallowed or bare excepts, unreachable code, duplicate conditions, untested checks), so don't repeat them. Add what needs understanding:
- checks that can never be true or false
- unguarded indexing such as `[0]` or `.get(...)[...]`
- unchecked `None` returns (e.g. `cv2.imread`, `dict.get`, regex `match`)
- error paths that look like success
- missing `else` where the fall-through is wrong
- hard-coded thresholds
- misleading names or messages

Each note: `loc` (`file:line`) plus one or two sentences giving the problem *and its consequence*. These are observations only; no patches unless asked.

To judge these, read only the line ranges you need: the summary gives `file:start-end` for each function, so use `sed -n 'START,ENDp' file`. Never read whole files.

## 4. Render

```bash
python3 <skill>/scripts/flow_map.py render <out_dir>
```

- **Output:** `<repo_root>/docs/flows/<entry>.html` (use `-o` to change it), overwriting any previous version. Git keeps the history.
- **Warnings:** the command lists any unlabelled nodes or untitled functions. Fix those in `labels.json` and re-render; it costs nothing.
- **Re-running `extract` deletes `labels.json`**, because node ids change. If the code changed, relabel from the new summary.

## 5. Report

In a few lines, tell the user:
- the output path (open it in a browser; it needs internet for React Flow from jsDelivr)
- the counts of functions, checks and steps
- the 2–3 most important review notes
- anything unresolved

Mention that `page.html#n12` links straight to a step.

Don't take screenshots or re-read the HTML to verify unless the user asks. Rendering is deterministic.
