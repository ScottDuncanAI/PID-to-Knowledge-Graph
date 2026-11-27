---
name: pid-to-kg
description: Turn P&ID and PFD PDFs into a process knowledge graph.
version: 0.1.0
author: Joel Sansana (joelsansana); upstream Scott Duncan (https://github.com/ScottDuncanAI/PID-to-Knowledge-Graph)
license: MIT
platforms: [linux]
---

# P&ID to knowledge graph

Hermes port of Scott Duncan's MIT-licensed Claude skill `pid-to-kg`
(https://github.com/ScottDuncanAI/PID-to-Knowledge-Graph). `scripts/` and
`references/` in this directory are verbatim from upstream
`skills/pid-to-kg/`; see `LICENSE` for attribution.

Work is split between deterministic scripts and you:

- **Scripts** classify pages (vector vs scanned), render them into readable tiles, collect OCR/text hints, then build, check, store and query the graph.
- **You** read the drawings — with `vision_analyze` on the rendered images — and write one structured extraction file per sheet. Symbol recognition and line tracing are visual reasoning, and that is the part scripts cannot do well.

All files live in this skill's directory, called `$SKILL` below (`scripts/`, `references/`, `LICENSE`). Installed user-local it is typically `~/.hermes/skills/pid-to-kg/` or `~/.hermes/profiles/<profile>/skills/pid-to-kg/`. Keep the work directory in the user's current folder, e.g. `work/<plant>`.

## Tool mapping — how this port runs

| Task | Hermes tool |
|---|---|
| Run every script | `terminal`: `python3 $SKILL/scripts/<script>.py …` |
| View a drawing image (overview grid, tile, crop) | `vision_analyze` with `image_url` = absolute path of the PNG |
| Read the procedure, references, QA report | `read_file` |
| Write each `extract/page_NN.json` | `write_file` |

**Setup.** The scripts need Python 3.10+. Before the first run, install the
dependencies with `terminal`:

```bash
python3 -m pip install --user -r $SKILL/scripts/requirements.txt
```

Use a virtual environment instead if the user's Python is shared with other
work. OCR hints need `rapidocr-onnxruntime`, which supports Python 3.10–3.12
only; on newer Python the skill runs without OCR hints — note that in the
final report.

## Ground rules

- **Only the P&ID pages.** Drawings often sit inside a long permit or design report. If the user names the pages, pass them with `--pages`. If not, and the PDF has more than a few pages, probe it, look at the page overviews or ask the user, then re-run the probe with `--pages` so later steps skip reports, legends and non-P&ID drawings.
- **One plant, one work directory, one graph.** Never put sheets from different plants in the same work directory: tags would merge across unrelated plants. If the user supplies several plants, run the whole workflow separately for each.
- **One configuration per graph.** If the PDF contains two revisions of the same drawing (same title or area, overlapping tags, e.g. an as-built and an older draft), do not merge them. Ask the user which revision to use, extract only that one, and record the excluded pages and the reason in the report. QA flags likely duplicates as `possible_duplicate_revision`. Record each sheet's `issue_status` from the title block or stamps.
- **Record only what is drawn.** Never invent tags, connections or loops to make the graph look complete. Anything unclear goes in with a lower `confidence`, or into `unreadable`.
- **The QA loop is not optional.** Rebuild until there are no errors, and re-check every warning on the drawing.

## Workflow

Copy this checklist into your notes and tick it off (`python3` and absolute
paths omitted for brevity):

```
- [ ] 1. Probe:    terminal: $SKILL/scripts/probe_pdf.py <pdf> --work <work> --plant-name "<name>" [--pages 47-60]
- [ ] 2. Render:   terminal: $SKILL/scripts/render_pages.py <work>
- [ ] 3. Orient:   vision_analyze each <work>/pages/page_NN/overview_grid.png; fix rotation if text is not upright
- [ ] 4. Hints:    terminal: $SKILL/scripts/make_hints.py <work>
- [ ] 5. Extract:  per sheet: read_file $SKILL/references/extraction_procedure.md; view tiles with vision_analyze; write_file <work>/extract/page_NN.json
- [ ] 6. Build:    terminal: $SKILL/scripts/build_kg.py <work>
- [ ] 7. QA loop:  read_file <work>/kg/qa_report.md; fix every error, re-check every warning, rebuild (repeat)
- [ ] 8. Report:   summarize the results and open issues for the user
```

### 1. Probe
`probe_pdf.py` assigns each page a class and an extraction route, and writes `<work>/manifest.json`. With `--pages`, the later steps render and read only those pages.

| Class | What it means | Route |
|---|---|---|
| `vector_text` | Vector drawing with a text layer | Render from vectors; exact text from the text layer as hints |
| `vector_outlined` | Vector drawing, text drawn as outlines (no text layer) | High-resolution vector render; OCR hints |
| `raster` | Scanned image | Native-resolution render, upscaled and contrast-enhanced; OCR hints |
| `raster_with_text` | Scan with an OCR text layer | As raster; text layer as (untrusted) hints |
| `hybrid` | Vectors plus large images | Both routes |

Report the probe result to the user in one line, e.g. "14 vector sheets with outlined text."

Low scan resolution is flagged in the notes. Expect to record more items in `unreadable` on those sheets.

### 2–3. Render and check orientation
`render_pages.py` writes the following for each page:

- `overview.png`
- `overview_grid.png`, which shows the tile names
- overlapping tiles in `tiles/`
- `tiles.json`, which records which tiles are blank

Orientation is detected with OCR when it is installed. Always confirm it yourself: `vision_analyze` each `overview_grid.png` (ask: "Is the drawing text upright? Which direction is the title block rotated?"). If the main drawing text is not upright, re-render that page with an explicit rotation, given clockwise: `render_pages.py <work> --pages N --rotation 90`.

Title blocks are sometimes rotated differently from the drawing. Orient by the drawing labels, not by the title block.

### 4. Hints
`make_hints.py` writes `hints/page_NN.json`. Each file holds candidate tags with page positions, taken from the text layer or from OCR. They are only hints: OCR misreads, and it misses things. You don't need to read the hint files during extraction. QA uses them to flag tags you may have missed. If OCR is not installed the step is skipped; note this in the final report.

### 5. Extract each sheet
This is the core of the job. First `read_file $SKILL/references/extraction_procedure.md` and follow it exactly. It covers:

- the reading order: overview, then every non-blank tile, then zoomed crops
- how to model pipe runs, tees, taps, signals and off-page connectors
- confidence calibration
- a self-check list to run before saving

The reading order maps to `vision_analyze` calls, one image per call:

1. **Overview** — `vision_analyze(<work>/pages/page_NN/overview_grid.png)`: confirm orientation, read the title block (drawing number, title, revision), note the legend, general notes and scope convention, and sketch the sheet in your notes (major equipment, main process lines, where lines leave the sheet).
2. **Every non-blank tile, in order** (r1c1, r1c2, …; blank tiles are grey in the grid and flagged in `tiles.json`) — `vision_analyze` each PNG in `<work>/pages/page_NN/tiles/`: list every tagged item and every unlabelled valve or fitting, follow each line to the tile edge, and read instrument bubbles and their signal lines. Tiles overlap by about 15%, so the same item may appear twice: record it once.
3. **Zoom when unsure** — run `terminal`: `$SKILL/scripts/crop.py <work> --page N --bbox 0.40 0.20 0.55 0.35` (it prints the crop path), then `vision_analyze` that crop for small text, crowded areas, flow arrows and valve symbols. Don't guess a tag you cannot read; put it in `unreadable` instead.
4. **Trace before you write.** For every line, know its two ends and the in-line items between them in flow order before you write it down.

Record each tile you read in `tiles_reviewed`. QA checks the list against the non-blank tiles.

Reference material, to `read_file` when needed:

- `$SKILL/references/isa_5_1.md`: instrument letter codes, bubble symbols, signal line types
- `$SKILL/references/symbol_guide.md`: how equipment and valve symbols map to `kind` values
- `$SKILL/references/ontology.md`: the graph's node and edge types and how they are derived
- `$SKILL/references/schema/extraction.schema.json`: the exact JSON schema of a page file

Write `extract/page_NN.json` with `write_file` and save it before starting the next sheet, so a long job never holds more than one sheet in context. Extract sheets sequentially.

### 6–7. Build and the QA loop
`build_kg.py` validates every page file and stops with a precise message on schema errors. It then writes these files to `<work>/kg/`:

- `graph.json`
- `graph.duckdb`
- `summary.md`
- `qa_report.md`

`read_file` the QA report and work through it:

- **Errors**, such as `unresolved_reference`, are mistakes in the page files. Fix them and rebuild.
- **Warnings** name the tiles to re-check. `vision_analyze` those tiles again, or crop and view a zoom, and correct the extraction if the drawing shows something different. Common warnings:
  - `instrument_not_located`: a sensor with no `measures`
  - `equipment_not_connected`
  - `unmatched_off_page`
  - `possible_missed_tags`: an OCR tag that is not in your extraction
  - `tiles_not_reviewed`
- A warning that turns out correct on the drawing is fine to leave. Examples: an off-page connector whose other drawing was not supplied, or an OCR misread. List these in your report.

Stop when there are no errors and every warning has been checked.

### 8. Report
Tell the user:

- where the outputs are
- the headline counts (equipment, instruments, loops)
- two or three example flow paths and loops taken from `summary.md`
- what remains uncertain

Point to `qa_report.md` for the details. State plainly that the graph is machine-extracted and needs engineering review before it is used for design, operations or safety.

## Using the graph

- `summary.md` is the compact, token-efficient context for an LLM: equipment-level flow, an instrument table with locations, and a loop table.
- `graph.json` is the canonical graph, which any tool can read.
- `graph.duckdb` is for SQL. Its views are `equipment`, `equipment_flow`, `instruments`, `loops` and `signals`, and its table macros are `upstream(id)` and `downstream(id)`.

Use `kg_query.py` (via `terminal`) to answer questions; add `--json` when another tool will consume the output:

```bash
python3 $SKILL/scripts/kg_query.py <work>/kg downstream 660-TK-001 --equipment
python3 $SKILL/scripts/kg_query.py <work>/kg upstream FV-0502
python3 $SKILL/scripts/kg_query.py <work>/kg path 660-TK-001 660-TK-004
python3 $SKILL/scripts/kg_query.py <work>/kg instruments --on 660-PP-011
python3 $SKILL/scripts/kg_query.py <work>/kg loop FIC-0502
python3 $SKILL/scripts/kg_query.py <work>/kg sql "SELECT * FROM equipment_flow"
```

To measure extraction quality on a sheet, hand-check it into a small gold file (format in `scripts/eval_kg.py`) and run `python3 $SKILL/scripts/eval_kg.py <work>/kg gold.json`: it reports tag, flow and loop precision/recall and instrument placement.

To move a graph into other tools: `python3 $SKILL/scripts/export.py <work>/kg graphml` produces a file for yEd, Gephi or NetworkX, and `python3 $SKILL/scripts/export.py <work>/kg neo4j` produces CSVs plus a Cypher import script.
