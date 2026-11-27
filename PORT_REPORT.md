# PORT_REPORT — PID-to-Knowledge-Graph → Hermes + OpenCode skills

Date: 2026-11-17 · Branch: `port/hermes-opencode` · Engineer: Lt. Cmdr. Geordi La Forge

Upstream: **Scott Duncan, PID-to-Knowledge-Graph (MIT)**
https://github.com/ScottDuncanAI/PID-to-Knowledge-Graph
Ported with permission of the MIT licence; `LICENSE` (verbatim upstream) is shipped
inside both skill directories and credit is given in both `SKILL.md` manifests.

Fork: `joelsansana/PID-to-Knowledge-Graph` (origin, SSH) · upstream wired as `upstream`.

---

## 1. What was ported

Upstream ships a Claude skill at `skills/pid-to-kg/` whose one Claude-coupled
assumption is the extraction step: *the model reads the rendered page images*
(`overview_grid.png`, tiles, `crop.py` zooms) and writes one
`extract/page_NN.json` per sheet per `references/extraction_procedure.md`.
Everything else is deterministic Python.

This port maps that one assumption onto each platform and changes nothing else.

### Deliverables

| Path | What it is |
|---|---|
| `hermes-skill/` | Installable Hermes user-local skill: `SKILL.md` + `scripts/` + `references/` + `LICENSE` |
| `opencode-skill/pid-to-kg/` | Installable OpenCode skill (directory form per OpenCode docs): `SKILL.md` + `scripts/` + `references/` + `LICENSE` |
| `PORT_REPORT.md` | This file |

### Install

Hermes (user-local; default profile):

```bash
cp -r hermes-skill ~/.hermes/skills/pid-to-kg
# or, for a named profile:
cp -r hermes-skill ~/.hermes/profiles/<profile>/skills/pid-to-kg
```

OpenCode (global or project scope, per the discovery table in the docs):

```bash
cp -r opencode-skill/pid-to-kg ~/.config/opencode/skills/    # global
cp -r opencode-skill/pid-to-kg <project>/.opencode/skills/   # project
```

The skill ID is derived from the directory name (`pid-to-kg`), which matches the
pattern OpenCode recommends (`^[a-z0-9]+(-[a-z0-9]+)*$`).

---

## 2. Verified platform mechanisms (no stop condition hit)

### OpenCode — skills exist and are documented

Primary citation: **https://opencode.ai/docs/skills/** (the host the brief
called `docs.opencode.ai` now serves the docs at `opencode.ai/docs`; same
pages). Facts used, all from that page:

- A skill is a directory containing `SKILL.md` placed under
  `.opencode/skills/<id>/` (project) or `~/.config/opencode/skills/<id>/`
  (global); the directory form is recommended and gives supporting files a
  private base directory.
- Frontmatter: `name` (display), `description` (advertised to the model);
  portability fields such as `license` are accepted but not interpreted.
- Paths written inside the skill are relative to the directory containing
  `SKILL.md`; when loaded, OpenCode provides the skill's base directory and a
  sample of supporting file paths. Supporting files are read on demand —
  exactly the lazy-reference pattern upstream already uses.
- The skill is loaded by the model via the `skill` tool using its exact ID.

### OpenCode — how the agent "sees" the drawings

Citation: **https://opencode.ai/docs/attachments/**

- The built-in **`read` tool recognizes PNG, JPEG, GIF and WebP by content**
  and passes them to the model as image media (auto-resized within the
  configured `media.image` limits; PNG output of `render_pages.py`/`crop.py`
  qualifies).
- Caveat stated in the docs and mirrored in the ported `SKILL.md`: the
  provider/model must accept image input; a text-only model gets a short note
  instead of the image. The ported skill instructs the agent to stop and ask
  the user to switch to a vision-capable model in that case.

Conclusion: **no Claude-specific API is required.** The extraction step is
generic image reading plus JSON writing.

### Hermes — tool mapping

The Hermes skill body is framed through the agent's own tools:

| Task | Tool |
|---|---|
| Run scripts (`probe_pdf.py`, `render_pages.py`, `make_hints.py`, `build_kg.py`, `crop.py`, `kg_query.py`, `eval_kg.py`, `export.py`) | `terminal` |
| View `overview_grid.png`, tiles, crops | `vision_analyze` with `image_url` = absolute PNG path |
| Read `references/*`, `qa_report.md`, `summary.md` | `read_file` |
| Write `extract/page_NN.json` | `write_file` |

`platforms: [linux]` in the Hermes frontmatter reflects what was actually
audited (Arch Linux, Python 3.14); no other platform was tested.

---

## 3. Divergences from upstream — complete list

**Zero upstream files were modified or deleted.** `git diff --stat
main...port/hermes-opencode` shows only additions under `hermes-skill/`,
`opencode-skill/`, and this report.

| File | Status | Reason |
|---|---|---|
| `hermes-skill/SKILL.md` | NEW | Hermes manifest + body. Frontmatter per house rules (`name`, 54-char `description` ending in a period, `version: 0.1.0`, `author` crediting joelsansana + Scott Duncan upstream, `license: MIT`, `platforms`). Body is upstream's workflow reframed through `terminal`/`vision_analyze`/`read_file`/`write_file`. |
| `opencode-skill/pid-to-kg/SKILL.md` | NEW | OpenCode manifest (`name`, `description`, `license`) + body framed through OpenCode's shell tool and built-in `read` tool, per the cited docs. |
| `hermes-skill/scripts/` (11 entry scripts + `pidkg/` package + `requirements.txt`) | VERBATIM copy | Byte-identical; `diff -r` clean. Entry scripts self-locate (`sys.path.insert(0, Path(__file__).resolve().parent)`) so they run from any install path. |
| `hermes-skill/references/` (4 md + 2 schema JSON) | VERBATIM copy | Byte-identical; `diff -r` clean. `build_kg.py` reads `../references/schema/` relative to `scripts/`, so the side-by-side layout is required. |
| `hermes-skill/LICENSE` | VERBATIM copy of root `LICENSE` | MIT attribution travels with the skill. |
| `opencode-skill/pid-to-kg/scripts/`, `references/`, `LICENSE` | VERBATIM copies | Same as above; `diff -r` clean. |
| `PORT_REPORT.md` | NEW | This file. |

Text-level deltas in the two new `SKILL.md` bodies vs upstream `SKILL.md`
(everything else in the bodies is upstream wording, kept where possible):

1. "Open each `overview_grid.png`" → `vision_analyze` on each PNG (Hermes) /
   `read` tool on each PNG (OpenCode); the tile/crop reading order from
   `extraction_procedure.md` is spelled out as one-image-per-call.
2. Upstream's "if your environment supports subagents, you can extract sheets
   in parallel" → "Extract sheets sequentially." Conservative reading: the
   port does not assume a subagent facility; noted here so the XO can relax it
   later.
3. Setup section: upstream tells Claude to "handle setup yourself"; the ports
   give the explicit `python3 -m pip install --user -r $SKILL/scripts/requirements.txt`
   command and the Python-3.10–3.12 OCR caveat.
4. Hermes description shortened to 54 chars (Hermes convention ≤60); the
   OpenCode description keeps upstream's full text plus one added sentence
   ("requires a model with image input") — OpenCode enforces no description
   length.
5. `references/extraction_procedure.md` and the other reference files are
   untouched even where they say "Open `overview_grid.png`": that instruction
   is addressed to whichever agent is extracting, and each ported `SKILL.md`
   defines how "open" happens on its platform.

Guardrail note: the crew runtime did **not** refuse to write `SKILL.md` files;
both were written directly. No `SKILL.md.content` fallback was needed.

---

## 4. Environment notes (what was installed and why)

- Interpreter: `/usr/bin/python3` = Python 3.14.8 (Arch). It had no `pip`
  module; it was bootstrapped by unzipping the bundled wheel
  `/usr/lib/python3.14/ensurepip/_bundled/pip-26.2.1-py3-none-any.whl` into
  `~/.local/lib/python3.14/site-packages` (what `pip install --user pip`
  would produce; `ensurepip --user` itself is refused by the PEP 668
  EXTERNALLY-MANAGED marker on this system).
- Deps installed (from upstream `scripts/requirements.txt`, into the user
  site): `pypdfium2 5.15.2`, `pillow 13.0.0`, `duckdb 2.0.0`.
  `pydantic 2.13.5`, `networkx 3.6.1`, `pytest 9.1.1` were already present.
  **Nothing was added beyond upstream's requirements file.**
- `rapidocr-onnxruntime` is NOT installed: upstream pins it to
  `python_version < "3.13"` and this box runs 3.14. Consequence (documented in
  both ported skills): OCR hints and OCR-based orientation detection are
  skipped; the agent confirms orientation visually. The full test suite
  passes without it.
- Network use beyond git/pip, for the record: two `curl` fetches of OpenCode
  docs pages (`opencode.ai/docs/skills/`, `opencode.ai/docs/attachments/`) —
  the verification the brief ordered — plus pip installs from PyPI and the
  final `git push` over SSH.

---

## 5. Test evidence

All commands run in `/home/joel/Documents/projects/PID-to-Knowledge-Graph`
unless pathed otherwise.

1. Upstream tests, unmodified:

```
$ /usr/bin/python3 -m pytest tests/ -q
78 passed in 6.78s
```

2. Ported entry point executes (done-criterion 4):

```
$ /usr/bin/python3 hermes-skill/scripts/build_kg.py --help ; echo $?
0
```

(`opencode-skill/pid-to-kg/scripts/build_kg.py --help` also exits 0.)

3. Verbatim-copy proof:

```
$ diff -r skills/pid-to-kg/scripts hermes-skill/scripts
$ diff -r skills/pid-to-kg/references hermes-skill/references
$ diff -r skills/pid-to-kg/scripts opencode-skill/pid-to-kg/scripts
$ diff -r skills/pid-to-kg/references opencode-skill/pid-to-kg/references
$ diff LICENSE hermes-skill/LICENSE
$ diff LICENSE opencode-skill/pid-to-kg/LICENSE
(no output — byte-identical)
```

4. End-to-end smoke build through the ported tree (upstream `mini_plant`
   fixture, scratch work dir outside the repo):

```
$ /usr/bin/python3 hermes-skill/scripts/build_kg.py <scratch>/mini --plant-name "Mini plant" --plant-id mini
Mini plant: 38 nodes, 57 edges -> <scratch>/mini/kg
  nodes: Boundary 2, Component 4, ControlLoop 4, Equipment 3, Instrument 9,
         OffPageConnector 2, PipeSegment 11, Plant 1, Sheet 2
  edges: ACTUATES 3, CASCADES_TO 1, FEEDS 5, FLOWS_TO 23, MEASURES 5,
         MEMBER_OF 10, PART_OF 2, SIGNAL_TO 7, TRIPS 1
  loops: control 3, interlock 1
  issues: 0 errors, 0 warnings, 2 info
$ /usr/bin/python3 hermes-skill/scripts/kg_query.py <scratch>/mini/kg downstream TK-100 --equipment
from: {id: TK-100, type: Equipment, kind: tank, name: Feed tank}
items: P-101 (pump, 4 hops), TK-200 (tank, 15 hops), TO DISTRIBUTION (boundary, 17 hops)
$ /usr/bin/python3 hermes-skill/scripts/kg_query.py <scratch>/mini/kg loop FIC-101
LOOP:F-101 — sensors [FT-101], controllers [FIC-101], final element FV-101,
  cascade slave of L-200   (all exit 0)
```

5. Hermes frontmatter audit (scripted check):

```
hermes-skill/SKILL.md
  description (54 chars, ends '.'=True): 'Turn P&ID and PFD PDFs into a process knowledge graph.'
  name: 'pid-to-kg'  version: 0.1.0  license: MIT  platforms: [linux]
  author: Joel Sansana (joelsansana); upstream Scott Duncan (…)
```

6. Branch hygiene (after commit):

```
$ git commit …   # 86559f5 on port/hermes-opencode
$ git diff --stat main...port/hermes-opencode
 PORT_REPORT.md                                     |  248 +++++
 hermes-skill/… (28 files: SKILL.md, LICENSE, references/, scripts/)
 opencode-skill/… (28 files: pid-to-kg/SKILL.md, LICENSE, references/, scripts/)
 57 files changed, 12540 insertions(+)
```

No `delete mode` / `rewrite` lines: zero upstream files modified or deleted.

7. Push (criterion 6):

```
$ git push -u origin port/hermes-opencode
$ git log origin/port/hermes-opencode -1 --format='%H'   # equals local HEAD
```

---

## 6. Done-criteria mapping

| # | Criterion | Evidence |
|---|---|---|
| 1 | Upstream tests pass unmodified | §5.1 — `78 passed in 6.78s` |
| 2 | `git diff --stat main...port/hermes-opencode` shows exactly the three new paths, zero upstream files touched | §5.6 — 57 files, 12540 insertions(+), no deletions |
| 3 | `diff -r` scripts vs port shows only intentional documented changes | §5.3 — byte-identical; the only authored files are the two `SKILL.md` (§3) |
| 4 | `/usr/bin/python3 hermes-skill/scripts/build_kg.py --help` exits 0 | §5.2 |
| 5 | OpenCode docs URL cited | §2 — https://opencode.ai/docs/skills/ (+ https://opencode.ai/docs/attachments/) |
| 6 | Pushed; `git log origin/port/hermes-opencode -1` == local HEAD | §5.7 |
