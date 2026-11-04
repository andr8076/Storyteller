# Storyteller 0.7.1 — Local story memory for Jan

Storyteller acts as a small OpenAI-compatible gateway between Jan's chat UI and Jan's own Local API Server. It is **not** another chat application.

- **Writing model (Jan):** your chosen model, currently Cydonia 24B for the demo.
- **Memory model (CPU):** Qwen3.5-2B Q4_K_M (stored in `models/`).
- **Storage:** ordinary Markdown inside each explicitly registered project folder.
- **No service:** starts with a terminal and stops when that terminal closes.
- **No cloud:** all model endpoints bind to `127.0.0.1`.
- **Original chapters:** never edited.

## What normal chat saves

In normal writing mode, Storyteller records completed **fictional scenes generated
by the writing model**. It saves exact scene text in
`.storyteller/memory/reference-notes/generated-*/`, so important details remain
searchable even when the 2B memory model fails to extract structured facts.
The small CPU model also proposes character and world updates, but facts are
accepted only when the cited original sentences support them. Rejected
suggestions go to `.storyteller/review.jsonl`; no claim is made that every
fact can be extracted reliably.

A pre-existing chapter pasted as a **user message** in an ordinary Jan chat
is not automatically imported as canon. Use **Catch Up (C)** for existing
history, or **Dump Info (I)** to catalogue author-provided facts. Both process
the text directly, without asking Cydonia to rewrite it.

Storyteller ignores chat titles, scene analyses, and factual Q&A for automatic
canon updates. Scenes skipped by older detector versions are safely reconsidered
after upgrading, without duplicating previously verified entries.

## Optional prompt prefix and suffix (v0.7)

Select a project in the terminal panel and press **P** to open **Prompt
Prefix / Suffix**. The menu lets you edit either field using a terminal
text editor, independently toggle each field, and preview the resulting
message. Prefix and suffix are **off by default**; saving nonempty text from
this editor enables that field, and toggling it off keeps its saved text.

For example, with a prefix of **Write in first person.** and a suffix of
**End the scene with suspense.**, the author types **Continue the story.**
in Jan, and the writer model receives ONE user message with this content:

    Write in first person.

    Continue the story.

    End the scene with suspense.

These are **not system messages**. Storyteller modifies only the last user
message in the request it forwards to the model. Jan's displayed original
message stays untouched, and the original user prompt is still recorded for
continuity. This is applied only to normal model requests—**never** to text
captured in Catch Up or Dump Info. It is scoped by project alias, even when
several projects share a folder. The settings are kept locally in
`.storyteller/project-state/<alias>/` with the project's other private data.

### GitHub / local installation

This repository contains application source only. Your `projects.json`,
private chapters, project memory, logs, and model weights stay on your
computer; they are excluded by `.gitignore`. Install Python 3 on Linux,
Konsole for the launcher, and a local `llama-server` plus a small compatible
GGUF model for automatic memory extraction. Run `Start-Storyteller.sh` to
start the terminal interface. The small model path/configuration may require
local adjustment if you have not installed the original development setup.
No login service or cloud integration is installed.

## Terminal control panel

Run `Start-Storyteller.sh` to open the terminal control panel. It starts Storyteller
and its small CPU model for you and **stops them when you quit**. Nothing starts
at login.

| Key | What it does |
| --- | --- |
| Up / Down | Choose a registered project |
| A | Add a project (name, stable Jan alias, existing folder, writing model) |
| E | Edit the selected project's name, folder and writing model |
| D | Remove the project registration (does **not** delete story/memory files) |
| C | **Arm Catch Up** for the next message sent through Jan |
| I | **Arm Dump Info** for the next message sent through Jan |
| F | **Optional file import** of .txt/.md material |
| J | View progress of a Jan prompt import; pause or resume |
| V | Browse rejected or uncertain imported facts |
| T | Show the current story continuation point |
| P | Edit, preview and toggle optional per-project prompt prefix/suffix |
| Enter | Show project information and analysis/review counts |
| S | Start or stop the gateway and CPU memory model |
| L | View recent activity |
| ? | Help |
| R | Refresh |
| Q | Quit and stop Storyteller (if this panel started it) |

The project list updates immediately; no model restart is needed to change it.
The project model alias remains unchanged during edits to preserve its identity in Jan.
Changing a folder only changes the association: it does **not** move existing files.

**If the panel shows EXTERNAL:** another Storyteller terminal is already running.
Close that older terminal, then press **S** to start the version controlled by this
panel. The panel will not terminate processes it didn't start.

The configuration file is `projects.json`. Existing registered projects are
preserved by this upgrade.

## Start and stop

Double-click `Start-Storyteller.sh`, choosing **Execute** when prompted, or run:

```bash
~/Documents/Apps/Storyteller/Start-Storyteller.sh
```

The launcher opens a Konsole control panel. It starts the gateway on port **18880** and a low-priority, CPU-only memory model on **18885**. Press **Q** to quit (or close the terminal) to stop both. If the CPU model fails, the gateway can still retrieve existing memories and reports the error.

## Jan connection

Start **Jan → Settings → Local API Server** on port **1337**, then select your previously configured custom OpenAI-compatible provider:

- Base URL: `http://127.0.0.1:18880/v1`
- API key: `storyteller-local` (placeholder for Jan's UI)
- Demo model ID: `storyteller-proof`

Storyteller sends the prompt plus relevant memory to Jan's Local API Server and streams the response back. It does not load a duplicate writing model.

## Create your own project

Register an existing directory; Storyteller does not have to own the story folder:

```bash
cd ~/Documents/Apps/Storyteller
python3 storyteller.py add-project \
  --name "My Story" \
  --alias storyteller-my-story \
  --folder "$HOME/Documents/Writing/MyStory" \
  --model "Cydonia-24B-v4.3-heretic-v3.IQ4_XS.gguf"
```

Restart Storyteller. Select model ID `storyteller-my-story` in Jan through the same provider. A provider's model picker may need refreshing.

To view projects:

```bash
python3 storyteller.py list-projects
```

## How the files work

Within the project folder:

```text
.storyteller/
  memory/
    canon.md                  manually edited — always included
    summary.md                manually edited — always included
    recent-events.md          automatically generated
    events.jsonl              append-only extraction history
    characters/
      irena-solis.md          auto-created / updated for explicit facts
    world/
      <topic>.md              automatically extracted world facts
  pending-exchanges.jsonl     completed Jan responses, queued safely
  processing.sqlite3          tracks what has already been analyzed
  review.jsonl                uncertain or rejected suggestions
```

Each completed narrative reply is queued. The CPU model extracts explicit, high-confidence changes using indexed sentences, and code checks its evidence before writing. Profile updates are appended to a dedicated **Automatically recorded facts** section, preserving manual content. Old events remain in `events.jsonl`.

Memory retrieval re-reads files on every model request. Recent events are injected automatically; character/world files are included when their filenames match names in recent messages. Memory budget is currently around 12,000 characters.

## Safety and current limitations

- **Experimental:** The 2B model is not perfect. Inspect automatic facts and `review.jsonl` for errors. A rejected suggestion does not change a profile.
- Short replies and factual question-answer exchanges are skipped by the extractor. This prevents many non-story answers from becoming canon, but it is only a heuristic.
- It does not yet resolve conflicting facts, follow renamed characters reliably, track regenerated/deleted Jan messages, or offer a one-click rollback UI. The append-only event log retains source evidence for auditing.
- Conversations are associated with projects through the model alias, **not** Jan's conversation ID. Only use a project's alias for chats that should share canon.
- When Storyteller is closed, the Storyteller model alias is unavailable. Ordinary Jan models continue working, but those requests are **not** automatically captured for catch-up yet.
- Your existing Storyteller demo uses invented characters and is deliberately separate from your actual Sparrow files.

## Diagnostics

Run only the gateway and disable CPU extraction for troubleshooting:

```bash
python3 storyteller.py serve --no-analysis
```

CPU model startup output is written to `memory-model.log` next to the program. The terminal shows saved fact updates and skipped/rejected counts.

**Next development steps:** conversation identity, handling regenerated responses, automatic conflict resolution, and recovery of missed chat activity.

## Catch Up and Dump Info through Jan (new in 0.6)

**This is the default, easiest workflow.** Select a project in Storyteller,
press **C** or **I** and return to Jan. Make sure Jan is using that project's
**Storyteller model alias** (shown in the terminal panel). The **very next
message you send** is treated as imported source material and is **not sent to
the creative writing model**.

- **C / Catch Up:** pasting a story/chapter/catalogue adds all supported facts
  and, when import processing successfully finishes, advances the continuation
  point to the END of that supplied material.
- **I / Dump Info:** pasting character data, historical events, story world
  rules, descriptions or other notes adds them to the SAME memory system but
  NEVER changes the continuation point.

Jan returns a short acknowledgement instead of generating a story. The
terminal's **J / Import Status** screen reports processing progress, confirmed
facts, review counts, and lets you pause (P) and resume (R) the saved import.
The mode automatically returns to normal after capturing one valid Jan message.

**To cancel:** while a mode is armed, press the same C or I key again in the
terminal, and confirm cancellation. Pressing I while C is armed (or vice versa)
changes the armed mode for that project. The main terminal display shows the
selected project's armed status. Modes are independent between project aliases,
including when projects share a story folder.

**Input is processed even if longer than the memory model's context window,**
because it is split into resumable sections. However, Jan itself may limit how
much text you can paste or send. If the chat interface refuses your huge input,
use **F** in the terminal to import a file/folder instead. The Storyteller
gateway caps capture text at 32 MiB. Only use files you trust and inspect
automatically generated facts if continuity is important.

**Important:** The import runs while Storyteller is open, using its low-priority
CPU model. It is not a cloud task or a system service. An interrupted import
keeps its local source and checkpoints; use J to resume if it was paused or
failed. A queued/running import also resumes after you restart Storyteller.
The project memory is updated only after each completed source section.

After a Jan prompt is captured, its original text and acknowledgement are
automatically omitted from subsequent requests forwarded to the creative
writing model. This keeps already-imported large chapters from consuming the
writer's context again. The text remains available as searchable project
memory; Jan's visible chat transcript itself is unchanged.

## Narrative position vs. timeless world information (v0.5)

**Catch Up** and **Dump Info** now share the same fact-extraction method,
updating the same character, world, and event records. Their only behavioral
difference is how they affect the project's narrative continuation point:

- **Catch Up (C):** once the *entire* import finishes successfully, sets the
  story's current position to the **end of the last source chapter**, in the
  natural file/chapter order. The point includes the original final scene
  excerpt plus a clearly labelled AI-generated interpretation.
- **Dump Info (I):** adds facts, backstory, lore, technology and world rules
  but **never** moves the continuation point or appends anything to the
  recent-narrative-events feed.
- **Jan-generated story scenes:** once a qualifying creative-writing response
  finishes, Storyteller updates the continuation point **before returning the
  completed answer to Jan**, independently of whether the CPU extractor later
  succeeds. Questions, summaries and informational replies should not move it.
- **T / Story Position:** inspect where the next scene is expected to begin.

Project-owned state files:

    .storyteller/project-state/<alias>/story-state.md
    .storyteller/project-state/<alias>/story-state.json
    .storyteller/project-state/<alias>/story-state-history.jsonl

The story-position Markdown is automatically injected into subsequent Jan
requests **before** character/world facts. It remains separate from the
general story summary and the facts catalogued by Dump Info.

The continuation point is isolated by project alias, even if two projects
use the same folder and share other character/world memory files.

**Cautions:** Automatically identifying a new story scene from a Jan prompt is
heuristic, not perfectly reliable. Regenerated or discarded Jan responses are
not yet reconciled automatically, and contradictory canon facts still require
review. The original chapter files are never modified.

## Catch Up and Dump Info — v0.4

Both import tools are available from the **Storyteller terminal control panel**.
Select a project with the arrow keys before choosing a mode.

### Catch Up (C)

This mode advances the narrative to the end of its imported material only
after the entire import has completed. An interrupted import does not move
the story point.

Use this for existing chapters or a substantial backlog of fiction. Choose a
plain-text / Markdown file, or a folder containing several chapter files.
The tool discovers files recursively and sorts their filenames naturally:
chapter 2 comes before chapter 10.

It extracts explicit character and world facts, important story events, and
relationships into the project's memory files. On completion it writes
`memory/summary.md` (a compact plot synopsis) and an extended section-by-section
summary into `memory/imported-summaries/`. Summaries are AI-generated and should
be checked for errors.

### Dump Info (I)

This mode never changes the story's continuation point, even if the notes
describe historical events or characters' present-day circumstances.

Use this for character profiles, setting rules, technology descriptions, and
other author-supplied notes. Information is catalogued into character/world
profiles and canon as appropriate.

For the optional **F** file-import workflow, choose **1** to import a file or
folder, or **2** to paste/compose text. The
latter opens nano in the same terminal. Paste the text and press **Ctrl+O**,
**Enter**, then **Ctrl+X** to save and return to Storyteller.

### Progress and safety

- Supported file types: `.txt`, `.md`, `.markdown`. Files outside these
  formats are not imported.
- Once processing begins, **P** or **Esc** pauses after the active section.
  You may close Storyteller and resume later using the exact same input.
- Finished sections are checkpointed individually. Importing an unchanged,
  fully completed source again does not duplicate it.
- The original source files are **never edited**. The program only writes in
  the project's hidden `.storyteller/` directory.
- After a successful import, verbatim source excerpts are preserved in
  `.storyteller/memory/reference-notes/`. Jan can retrieve relevant passages,
  even if the 2B memory AI did not extract every fact.
- Press **V** in the panel to inspect rejected suggestions. They are not
  automatically written into structured canon.
- The CPU model is small and fallible. Inspect generated profiles and summaries,
  especially after large or complex stories, to catch mistakes or contradictions.
- Altering the input after completing an import creates a **new import history**
  instead of deleting earlier facts; conflicting versions require review.

A project normally stores import progress under
`.storyteller/imports/progress.sqlite3`, plus human-readable status files in
`.storyteller/imports/jobs/`. Pasted inputs remain in
`.storyteller/imports/inputs/` for later reuse.

Advanced command-line interface (the terminal UI is preferred):

```bash
python3 import_modes.py scan --project storyteller-your-project --mode catchup --source /path/to/chapters
python3 import_modes.py run  --project storyteller-your-project --mode dump    --source /path/to/reference.md
```

No systemd service or automatic login startup is installed.
