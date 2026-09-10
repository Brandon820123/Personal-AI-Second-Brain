# Personal-AI-Second-Brain
# 🧠 Personal AI Second Brain

A local-first personal AI knowledge assistant built with Large Language Models (LLMs), Retrieval-Augmented Generation (RAG), and vector databases.

## 📌 Overview

Personal AI Second Brain is a project that aims to build a private AI-powered knowledge management system.

Instead of manually searching through documents, this system allows users to upload personal files, retrieve information through semantic search, and interact with their own knowledge base.

The goal is to create a personal AI assistant that can understand and organize:

- 📚 Learning materials
- 📝 Notes
- 💻 Programming documents
- 🚀 Project files
- 📄 Research papers

---

# 🎯 Goals

The project focuses on building a practical personal knowledge system with:

- Local AI inference
- Private knowledge storage
- Semantic document retrieval
- RAG-based question answering
- Personal knowledge organization

The final goal is to create an AI assistant that can help users:

- Find information from their own files
- Summarize knowledge
- Connect related concepts
- Support learning and projects

---

# 🏗️ Architecture

The system follows a Retrieval-Augmented Generation (RAG) architecture.
Documents
|
v
Document Parser
|
v
Text Chunking
|
v
Embedding Model
|
v
Vector Database
|
v
Semantic Retrieval
|
v
Large Language Model
|
v
Answer + Sources

## Phase 6B: Real-time Local Voice

The PySide6 desktop interface keeps text generation independent from speech:

```text
Ollama token stream
    -> natural sentence segmenter
    -> local Piper synthesis FIFO
    -> local WAV playback FIFO
```

- Chinese `。！？；` and English `. ! ?` boundaries release useful sentences.
- Piper voice models are warmed and cached only while VOICE is enabled.
- Synthesis and playback use separate workers, so the next sentence can be
  prepared while the current sentence is playing.
- Stop Speaking and explicit microphone activation immediately stop playback and
  invalidate pending speech without cancelling the streamed text answer.
- Voice input, STT, TTS, temporary audio, and playback remain on-device. There is
  no wake word, background listening, voice cloning, or cloud speech service.

## Phase 7A: Supabase Storage Cache

AI file resources can optionally be synchronized from the Supabase Storage bucket
`ai-files`. The supported bucket layout is `documents/`, `avatars/`, and `config/`.
Every object is downloaded to the matching location below `data/cache/` before it
is used; document chunking, embeddings, ChromaDB, RAG, Ollama, and chat remain
unchanged and local.

Set `SUPABASE_URL` and `SUPABASE_KEY` in the process environment (see
`.env.example`). Secrets are never stored in application source. At startup, and
when **刷新云文件** is selected, a Qt background worker compares Storage metadata
with `data/cache/.cloud_manifest.json` and downloads only missing or updated
objects. Cached documents are ordinary local paths accepted by the existing
document importer. Cached avatar files take precedence over bundled
`assets/avatars/` files without changing avatar animation behavior.

If configuration or network access is unavailable, the application displays
`Cloud storage unavailable. Using local cache.` and keeps using any existing
cached and bundled files. Synchronization never deletes an unlisted local cache
file automatically, so a temporary cloud outage cannot remove offline resources.

## Phase 8: Local File Scanner v1

`app/file_scanner.py` provides a local-only discovery layer for configured
knowledge folders. It recursively finds PDF, DOCX, TXT, and Markdown files,
prunes development/system folders before traversal, and generates size,
modification-time, and streaming SHA-256 metadata without loading entire files
into memory.

Scanner settings live in `data/config/scanner.json`; scan state is persisted
atomically in `data/config/file_index.json`. Each run classifies supported files
as new, modified, or unchanged by comparing content hashes. New and modified
records use `processed: false`, while unchanged records preserve their existing
processing flag for a later RAG integration phase. The scanner does not currently
load, chunk, embed, index, or upload any discovered document.

## Phase 8.5: Automatic Knowledge Synchronization

`app/knowledge_sync.py` connects scanner results to the existing document import
service without changing document loading, chunking, embeddings, ChromaDB, RAG,
or Ollama logic. `sync_new_documents()` imports new files, safely re-indexes
modified files, skips unchanged processed files, and retries unchanged files that
previously failed while still carrying `processed: false`.

Every file is isolated as one synchronization operation. A failure is recorded in
`last_error` and does not stop later documents. A successful operation atomically
updates `file_index.json` with `processed: true`, the knowledge library
`knowledge_id`, and a UTC `last_indexed` timestamp. Existing manually imported
documents are matched by their resolved source path and remain compatible with
automatic synchronization. The local document loader also extracts standard DOCX
paragraph text through WordprocessingML so all Phase 8 scanner formats can enter
the same existing import pipeline without Office automation or a cloud service.

## Phase 8.6: Knowledge Sources Management

The desktop Knowledge page contains separate **知识库** and **知识来源** tabs.
Knowledge Sources shows authorized folders, file/index/pending/failure summaries,
first-level folder distribution, searchable file status, scan/sync progress, and
bounded activity logs. Folder access is granted only through Qt's directory
picker; revoking a folder updates `scanner.json` without deleting source files or
existing knowledge records.

Scan, knowledge sync, and the combined scan-and-sync action use the existing Qt
worker thread infrastructure, so hashing, parsing, embeddings, and Chroma writes
never execute on the UI thread. Settings includes an opt-in startup scan, disabled
by default. Startup scanning detects file changes only and never starts a large
indexing operation without a separate user action.

## Phase 9A: Memory System v1 (Backend Only)

`app/memory_store.py` persists deliberate long-term information in the local
SQLite database `data/memory.db`. It uses Python's standard-library `sqlite3`;
there is no new package, embedding model, cloud service, or Chroma collection.
The path is resolved from the project directory, independent of the process's
working directory. Importing the modules does not open a database. The first
storage operation creates the parent directory, tables, and indexes as needed.
Database files and SQLite journal sidecars are excluded from Git.

The `memories` table stores these fields:

| Field | SQLite type | Meaning |
| --- | --- | --- |
| `id` | TEXT PRIMARY KEY | Stable UUID |
| `content` | TEXT NOT NULL | Selected fact or summary, up to 4000 characters |
| `memory_type` | TEXT NOT NULL | `personal`, `project`, or `conversation` |
| `importance` | INTEGER NOT NULL | Integer from 1 to 5; default 3 |
| `created_at` | TEXT NOT NULL | UTC ISO 8601 creation time |
| `updated_at` | TEXT NOT NULL | UTC ISO 8601 last-edit time |
| `source` | TEXT NOT NULL | Origin, e.g. `manual`, `chat:user`, or `project:atlas` |

`personal` holds preferences, habits, and information explicitly selected by the
user. `project` holds progress, decisions, and TODOs. `conversation` holds an
important summary explicitly supplied by the user or a future reviewed-summary
workflow. The store does not infer facts or assess free-form summaries: callers
of `add_memory()` must select information that has long-term value.

The indexed `memory_terms(memory_id, term)` table supports local retrieval. Its
foreign key cascades deletions, and writes update content and search terms in one
transaction. Identical `(content, memory_type)` additions reuse the existing
record without overwriting its metadata. Each operation opens and closes its own
SQLite connection so a manager can be used by the existing background workers.
Invalid input raises `ValueError`; database or filesystem failures raise
`MemoryStoreError`. Missing IDs return `None` for reads/updates and `False` for
deletions. Updates preserve `id` and `created_at`.

`app/memory_manager.py` exposes the five basic functions and a `MemoryManager`
class suitable for dependency injection and a later GUI:

```python
from app.memory_manager import MemoryManager

memory = MemoryManager()  # Optional db_path=... for an isolated database.
record = memory.add_memory(
    "Atlas project decided to use SQLite for local memory.",
    memory_type="project",
    importance=4,
    source="project:atlas",
)
record = memory.get_memory(record["id"])
record = memory.update_memory(record["id"], importance=5)
matches = memory.search_memories("Atlas SQLite", memory_type="project", limit=3)
page = memory.list_memories(memory_type="project", limit=20, offset=0)
deleted = memory.delete_memory(record["id"])
```

An explicit remember command is captured immediately before normal chat, for example
`请记住：我长期使用 Python 开发后端。`,
`请记住：[project] Atlas 项目决定使用 SQLite。`, or
`Please remember [conversation]: We agreed to review Atlas milestones weekly.`
The optional tags are `personal`, `project`, and `conversation`; Chinese prefixes
`个人：`, `项目：`, and `对话摘要：` are also accepted. Untagged requests use
`personal`, importance 4, and source `chat:user`. Empty, interrogative, and
oversized remember requests are rejected. Phase 9C adds conservative automatic
selection for other long-term statements; it still does not archive all user
messages, model answers, imported documents, or conversations.

On each normal-chat message, the manager first handles any explicit save request
and then retrieves relevant memory. Both desktop and CLI RAG retrieve from the
original question only; their prompt builder does not capture memories. CLI
document summarization bypasses Memory entirely. Retrieval normalizes English
words and Chinese adjacent-character pairs, removes common filler terms, and
uses at most 64 unique query terms. Results rank by matching terms, followed by
importance and update time. This is lexical retrieval: v1 does not match
synonyms or translate between languages. Empty or unrelated queries return no
memories; listing all records is a separate paginated management operation.

Prompt injection includes at most **3 matching memories**, at most **600
characters per content excerpt**, and **2400 characters for the complete memory
context**. The JSON reference block includes IDs, types, and edit times, and
clearly states that current requests, Persona, language, and RAG grounding rules
take precedence. It is inserted after Persona/language messages and before the
user message; RAG keeps its existing grounding rules after the memory block.
Memories never become document citations or evidence for grounded answers.
No-match requests retain the existing prompt structure. Storage failures log a
generic warning and allow chat to continue; a failed explicit save adds a status
instruction so the assistant does not claim persistence succeeded.

The existing Ollama stream, callbacks, RAG relevance threshold/source formatting,
Persona definitions, ChromaDB, Supabase, Scanner, voice, and GUI are unchanged.
The normal-chat and RAG service entry points accept an optional `memory_manager`
for isolated tests or future integration.

Run the Memory tests and the full regression suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_memory*.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Phase 9B: Memory Management GUI

The desktop sidebar includes a **Memory** page implemented by the reusable
`MemoryPage` widget in `app/ui/memory_page.py`. Four summary cards show total,
Personal, Project, and Conversation counts. The page loads all records through
bounded `list_memories()` pages, so statistics and the scrollable table are not
limited to the first database page. Each row displays content, type, importance,
local display time for `updated_at`, and source.

The search field calls the existing `search_memories()` interface and can be
combined with the All, Personal, Project, or Conversation filter. An empty search
uses the records already loaded through `list_memories()`. Search results retain
the Memory v1 limit of 50 matches.

**＋ 添加记忆** opens a modal editor for content, type, and importance, then calls
`add_memory()` with source `gui:manual`. Selecting a row enables Edit and Delete;
double-clicking also opens Edit. Edits preserve the record source and call
`update_memory()`. Delete requires a confirmation dialog before calling
`delete_memory()`. Every successful write reloads both the complete statistics and
the active filtered view. Read failures stay on the page as a friendly status;
write failures use a short warning dialog without exposing internal paths or a
traceback.

The page uses the existing Persona-aware dark stylesheet but contains no avatar
or animation. Entering it from the sidebar refreshes the database view. Chat,
RAG, Ollama, Persona behavior, ChromaDB, Scanner, Supabase, and Voice are not part
of its CRUD path.

Automatic extraction and consolidation remain outside the Memory page's CRUD
responsibilities and are described in Phase 9C.

## Phase 9C: Automatic Memory Extraction and Deduplication

After a successful normal-chat response, `app/memory_manager.py` evaluates the
user message for at most one strong long-term candidate. It recognizes durable
preferences and habits, long-term goals, explicit project decisions and status,
and clearly marked important conversation conclusions. Questions, greetings,
thanks, weather chat, temporary emotions or plans, short fragments, and text
presented as an AI/assistant answer are rejected. Only the user message is
evaluated; generated model text is never stored as Memory.

Each candidate carries an in-process `confidence` value and reason. Automatic
candidates require confidence **0.80** or higher, while leading explicit remember
commands use confidence 1.0 and save directly through the same consolidation
path. Confidence is decision metadata rather than a new SQLite column, so the
Phase 9A schema and Memory GUI remain compatible. Importance is assigned by
long-term value: goals use 5, preferences/habits/project state use 4, and an
important conversation conclusion uses 3. Automatic records use source
`chat:auto`; explicit requests keep `chat:user`.

Before writing, the manager searches up to 20 same-type memories. Normalized
content, sequence similarity, and lexical overlap suppress equivalent wording.
For project records, shared subject terms connect states such as planned, in
progress, blocked, and completed. A newer status updates the existing row and its
search terms while preserving its ID and creation time. A stale progress message
cannot replace a completed status. The resulting decision is one of `rejected`,
`added`, `updated`, or `duplicate`.

Desktop and CLI normal chat run automatic extraction only after Ollama has
returned response text. A failed or empty stream does not create an automatic
memory. Explicit remember commands are handled before generation so the assistant
can report a save failure honestly, then skipped by the post-chat pass. RAG,
document summarization, Persona, ChromaDB, Scanner, Supabase, Voice, and the
Memory page do not enter the extraction path.

Every decision emits a module-level DEBUG message in the form
`Memory candidate -> <decision>`. Normal GUI operation does not enable DEBUG
logging, so these diagnostics remain available to developers without appearing
in the interface.

## Phase 10A: Agent Core v1

`app/tool_registry.py` provides an allowlist-based Tool Registry. Each tool has a
name, description, JSON-style parameter schema, and private Python callable.
Callables are never included in the serializable catalog returned to the model.
The registry rejects duplicate or malformed registrations, unknown tools,
missing or extra arguments, wrong JSON value types, invalid enum values, and
values outside declared limits before invoking a callable. Tool failures are
wrapped as controlled execution errors.

`app/agent_core.py` adds a bounded orchestration loop:

```text
user request
    -> local-data route guard
    -> Ollama JSON tool decision
    -> strict registry validation
    -> read-only tool execution (maximum 3 calls)
    -> bounded tool-result context
    -> existing Persona/language/Memory prompt
    -> existing Ollama response stream
```

The route guard bypasses Agent planning for ordinary chat. Clear requests to
search local knowledge, inspect indexed files or sources, or recall project and
long-term history use the local `qwen3.5:4b` model as a tool router. Each decision
must be exactly `{"tool": <registered-name-or-null>, "arguments": {...}}` and is
parsed as JSON; there is no natural-language tool-call parser. The model may stop
with a null tool, and the loop cannot execute more than three tools.

Phase 10A registers only these tools:

| Tool | Existing interface used | Behavior |
| --- | --- | --- |
| `search_knowledge` | Embeddings, `VectorStore.search()`, RAG filtering | Returns a small set of relevant indexed chunks and source labels |
| `search_memory` | `MemoryManager.search_memories()` | Returns bounded Personal, Project, or Conversation matches |
| `scan_knowledge_sources` | Scanner config and `scan_folder()` | Reads configured folders with `save_index=False` |
| `list_knowledge_files` | `knowledge_library.list_documents()` | Lists bounded metadata for already indexed files |

No file deletion, system-file mutation, shell, PowerShell, or arbitrary command
tool is registered. Results are JSON-bounded and enclosed in a clearly marked,
untrusted system-context block after the active Persona and language instructions
and before the user message. The final answer still uses the existing response
stream, so Fairy and Delamain retain their identity and style. A rejected call,
planner failure, or tool exception becomes result context and does not stop the
final response.

Agent decisions, selected tools, tool results, and final-response completion use
module-level DEBUG logging. The desktop GUI does not enable these logs. Run the
focused Agent tests or complete regression suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_tool_registry tests.test_agent_core -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Phase 10C: Multi-step Agent v1

Complex local requests now use `app/agent_plan.py` through Agent Core:

```text
local-data route guard -> complex-task guard -> PLANNING
  -> validated JSON plan -> EXECUTING -> next step
  -> WAITING_CONFIRMATION (before each write) -> EXECUTING
  -> COMPLETE or FAILED -> existing Persona response stream
```

A plan contains the original `goal` and 1–5 sequential `steps`. Each step names
an allowlisted `tool`, an optional `instruction`, optional schema-valid
`arguments`, and a `critical` flag (default true). Runtime-owned `status` and
`attempts` are never trusted from the model. Arguments omitted by the planner
are resolved immediately before that step, using the original goal, current
plan, prior results, up to six recent context messages (6000 characters), and
the existing relevant Memory block (2400 characters). The initial plan is capped
at 24000 characters; tool results retain the existing 7000-character bound.
Reference data is untrusted and does not grant permission or override the goal.

Research summaries can use `search_knowledge` then the new read-only
`summarize_knowledge` tool. Review-note requests can add `create_note` after
synthesis. Multiple todos use one `create_todo` step per item. Ordinary questions
such as “What is GPU?” bypass the Agent; simple local requests retain the
lightweight 10A/10B decision route and its three-call bound.

Complex plans have a shared maximum of five actual tool calls, including retries
and calls resumed after confirmation. Read failures can retry once. A failed
critical step stops the task; explicitly optional independent read failures are
recorded and may be skipped. Writes are never retried automatically because an
error may follow a partial side effect. There is no replanning loop.

`create_note`, `update_memory`, `create_todo`, and `complete_todo` retain 10B's
registry enforcement and frozen, one-use confirmation IDs. Every write pauses
for its own approval; approving a plan or an earlier action never approves later
writes. Confirmation resumes the same in-memory plan and call budget. Denial
cancels remaining steps, preserving earlier completed actions. Starting another
request invalidates outstanding approval IDs. Plans are not persisted across
restarts. GUI status uses plain Agent text, without changing Persona animation.

The isolated `tests/test_agent_plan.py` suite covers acceptance scenarios A–E,
context transfer, all four protected tools, cancellation, replay and payload
tampering, optional failures, oversized plans, write failures, and budgets across
confirmation pauses. Run it with:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_agent_plan tests.test_agent_core tests.test_tool_registry -v
```

RAG, Memory storage/retrieval, Scanner, Supabase, Conversation Persistence,
Persona animation, and Voice are unchanged.

## Persona Avatar Assets

The desktop UI uses processed, square Persona artwork from `assets/avatars/`:

- `delamain.png` is presented in a mildly rounded cyan system frame.
- `fairy.png` is presented with a circular violet mask and halo.
- Neutral retains the minimal programmatic placeholder.

One reusable Qt avatar widget loads and caches source and high-DPI prepared
pixmaps for dialogue panels, Persona selection cards, and the current-Persona
header. Lightweight QPainter rendering separates each image avatar into a soft
background aura/geometric layer, the sharp cached PNG core, and crisp foreground
ring or HUD accents. Persona-specific motion is applied to those lightweight
layers rather than distorting a portrait. Fairy keeps its base image and widget
geometry stable while a clipped inner ring layer, including its circular accent,
rotates at a constant speed during active work; it does not emit listening waves,
flash, wobble, or rotate the entire PNG. Active-state transitions preserve the same
avatar widget, rotation phase, and timer, so streamed RESPONDING text does not
interrupt motion.

Persona timing and overlay policy live behind `PersonaAnimationProfile` in
`app/ui/avatar_animation_profiles.py`. `FairyAnimationProfile` retains the circular
companion language, while `DelamainAnimationProfile` owns a separate formal HUD
renderer for boot, listening, vertical knowledge scan, processing, responding,
online, and restrained error visuals. The generic widget remains responsible for
pixmap caching, fixed geometry, timer ownership, and three-layer composition.

The latest chat avatar uses a 550 ms `ENTRY_REVEAL` when chat first opens, the user
returns to chat, switches Persona, or creates a new response card. The reveal is
paint-only: internal opacity, 0.94-to-1.0 scale, six-pixel upward settling, glow
activation, and a restrained ring/HUD sweep. It never changes widget geometry or
reflows the dialogue card, and it retargets its post-reveal mode if dialogue state
changes while tokens stream.

Delamain overrides that shared entry timing with a 720 ms system-boot sequence:
HUD atmosphere activates first, the portrait fades in from 0.95 scale, one cyan
scan passes through the frame, and the system stabilizes without bounce or
overshoot. Its idle portrait remains completely fixed while frame brightness
cycles smoothly from 100% to 85% and back over four seconds. The former dot and
below-avatar scan-wave indicators are replaced by a clipped face-identification
scan: once per four-second idle cycle, a 14%-height cyan gradient band spends 1.5
seconds passing through the portrait with shallow rippled scan lines and a brief
frame response. A persistent 8-by-10 low-opacity sampling grid, fine electronic
refresh lines, and three shallow horizontal refraction traces keep the full face
digitally active between scans without moving or deforming the source portrait.
The grid strengthens locally inside the scan band. SEARCHING uses the strongest
continuous scan, while THINKING uses a restrained 1.6-second continuous scan and
stronger monitoring surface. No effect spills into message text or resembles an
audio equalizer, heartbeat, or water ripple.

Delamain's core renderer also divides the complete square portrait into 18 clipped
horizontal signal strips and applies sub-pixel, phase-driven lateral refraction of
at most one logical pixel. A broad 56%-height gradient scan curtain simultaneously
modulates the whole portrait tint, grid clarity, local contrast impression, and HUD
frame response. This makes the face, dark background, sampling surface, and frame
read as one monitored digital window while retaining the cached source image and
avoiding frame-by-frame bitmap generation.
After completion, only the latest response switches to a time-based standby loop.
Fairy uses a 2.1-second 0.98–1.05 breathing scale plus gentle accent motion;
Delamain keeps its portrait scale fixed and uses a restrained cyan HUD/glow pulse.
Sending the next message settles Fairy back to base scale over 200 ms and makes
the previous Persona avatar static history. A
dedicated ENTRY_REVEAL / WORKING / IDLE_BREATHING / HISTORY_STATIC mode keeps this ownership separate
from dialogue state. Hidden, header, selection, older completed, and SPEAKING
avatars remain static. Missing image assets log a development warning and fall
back to the programmatic renderer.

---

# 🛠️ Technology Stack

## AI

- Ollama
- Qwen Series Models

## Backend

- Python
- FastAPI

## Knowledge Retrieval

- Embedding Models
- ChromaDB

## Frontend

- HTML
- CSS
- JavaScript

## Development

- GitHub
- VS Code

---

# 💻 Hardware Environment

Current development environment:

| Component | Specification |
|-|-|
| CPU | Intel Core Ultra 7 155H |
| RAM | 32GB |
| OS | Windows |

The project is designed to run efficiently on consumer hardware.

---

# 🚀 Development Roadmap

## Phase 0: Environment Setup

- [x] Create GitHub repository
- [x] Install Ollama
- [ ] Test local LLM inference
- [ ] Select suitable local model

---

## Phase 1: Local AI Engine

- [ ] Connect Python with Ollama API
- [ ] Build basic AI interaction interface

---

## Phase 2: Document Processing

- [ ] Support PDF/TXT/Markdown files
- [ ] Extract document contents
- [ ] Implement text chunking

---

## Phase 3: Knowledge Retrieval

- [ ] Generate embeddings
- [ ] Store vectors
- [ ] Implement semantic search

---

## Phase 4: RAG System

- [ ] Retrieve relevant knowledge
- [ ] Generate answers based on documents
- [ ] Provide source references

---

## Phase 5: Web Application

- [ ] Build user interface
- [ ] Upload documents
- [ ] Chat with knowledge base

---

## Phase 6: Personal Memory System

Future features:

- Knowledge organization
- Learning history
- Project memory
- Knowledge graph
- AI-assisted planning

---

# 📂 Project Structure
AI-Second-Brain/

├── app/
│
├── data/
│
├── tests/
│
├── docs/
│
├── README.md
│
└── requirements.txt

---

# 🌱 Vision

The project aims to explore how local AI models can become a personal knowledge companion.

By combining:

- Local Large Language Models
- Retrieval-Augmented Generation
- Personal Data Management

this project hopes to create a long-term AI system that grows together with its user.

---

# 📜 License

MIT License
