# Amigo — Capability Tracker

This file is the source of truth for what Amigo's starter code supports. Update the stage status and change log as work is implemented and verified.

**Last updated:** 2026-11-06  
**Project name:** Amigo  
**Current target:** Windows desktop, local Ollama model, optional Gemini API

## Security contract

- [x] The model-exposed filesystem tools are restricted to `%USERPROFILE%\Documents\Amigo\My Materials`.
- [x] Workspace location cannot be changed using `.env` settings.
- [x] Reject absolute paths, `..` path traversal (including Windows backslash forms), Windows drive paths, and NTFS alternate data stream syntax.
- [x] Reject symbolic links and junctions at the workspace root or in a path being accessed; list entries that are links as blocked rather than following them.
- [x] Refuse to read files with multiple hard links as a conservative guard against reading files also linked elsewhere.
- [x] Create new text files in exclusive mode; do not overwrite an existing file.
- [x] Research reports are saved through the same guarded file-creation tool.
- [x] No arbitrary shell, PowerShell, Python execution, or general mouse/keyboard automation tool is exposed to the model.
- [x] Supported text-file updates are exposed as an explicit operation; deletion requires a user-confirmation callback in the UI.
- [x] App launching is restricted to a fixed allowlist.
- [ ] OS-level sandboxing under a separate restricted Windows identity/container has not been configured.

**Important boundary:** this is application-level isolation for the tools exposed to the model. The Python application still runs as the logged-in Windows user. It does not provide a complete OS security sandbox against compromised Python code, malicious dependencies, or a separately compromised process. For that stronger threat model, run Amigo under a dedicated low-privilege Windows account or suitable OS/container sandbox with only the workspace granted write access.

Amigo keeps its SQLite database under `%LOCALAPPDATA%\Amigo\amigo.sqlite3`. On first use, the old project database is copied with SQLite's backup API and the old file is left intact. The source-checkout `workspace/` is also left untouched; selected files can be copied into My Materials through the app.

## Stage 1 — Desktop companion + local chat

**Code status:** Implemented in starter; verify on the target laptop.

- [x] PySide6 desktop window with Today/Goals/Projects/Learning/Knowledge/Calendar workspace on the left and assistant chat on the right.
- [x] Text input and send action; Enter sends and Shift+Enter inserts a newline.
- [x] Local Ollama chat integration.
- [x] Recent conversation turns included as context.
- [x] SQLite conversation history stored locally, separated by chat session.
- [x] Launching Amigo begins a fresh chat; older conversations remain available through History.
- [x] Local text replies stream into the window as they are generated (for ordinary chat, not cloud research or tool workflows).
- [x] Two-column local-first workspace UI with tray show/hide controls.
- [x] System tray show/hide and quit controls.
- [x] Health check for the Ollama server and configured model.
- [ ] Rendered Markdown, task cancellation, and semantic/vector memory search.

## Stage 2 — Desktop and workspace tools

**Code status:** Implemented in starter; verify with local tests before enabling.

- [x] Launch built-in applications plus uniquely named apps discoverable through Windows Start Menu shortcuts (no arbitrary shell commands).
- [x] Open an `http://` or `https://` URL in the default browser.
- [x] List workspace files and folders.
- [x] Create folders inside the workspace.
- [x] Create supported text files inside the workspace.
- [x] Read supported text files inside the workspace (up to 1 MB per file).
- [x] Update supported text files inside the workspace on explicit request.
- [x] Delete workspace files after an explicit confirmation in the UI.
- [x] Rename and move workspace files/folders without overwriting an existing item.
- [x] Find and open files/folders inside the workspace; opening files uses the Windows default app.
- [x] Ask the user to choose when a spoken/normalized name matches multiple workspace items.
- [x] Route clear list/read/open/update/delete/rename/move commands directly to the corresponding local tool, avoiding an unnecessary model turn.
- [x] Verify text-file updates by reading the saved content back.
- [x] Open a validated folder inside the workspace in Windows File Explorer.
- [x] Refuse overwrites and paths outside the workspace.
- [ ] Delete folders or provide general mouse/keyboard GUI automation.

## Stage 3 — Cloud routing + web research + reports

**Code status:** Implemented in starter; requires a working Gemini API key/model and laptop-side validation.

- [x] Heuristic route of current-information/web-search requests to Gemini.
- [x] Optional cloud reasoning for selected longer analysis requests.
- [x] Google Search grounding request for web research.
- [x] Extract cited source URLs when supplied in the response.
- [x] Save requested research reports as new Markdown files inside `reports/` in My Materials through the guarded file tool.
- [ ] Search-provider fallback, more robust routing evaluation, source-quality scoring, and report export to DOCX/PDF.

## Goals, activities, and knowledge links

**Code status:** Implemented; automated unit/performance checks pass. The interactive UI still needs user-side Windows verification.

- [x] Versioned SQLite schema with WAL mode, foreign keys, transactions, and relationship indexes.
- [x] Goal creation/editing and active, paused, and completed states with append-only goal history.
- [x] Goal milestones with explicit completion records.
- [x] Actual activity records with optional duration, reflection, status, goal links, and topic links.
- [x] Derived milestone/activity summaries; no disconnected progress percentage.
- [x] Topic links between goals, activities, and imported materials.
- [x] Material import copies the selected original into My Materials, requires a topic link, and keeps the original unchanged.
- [x] Explicit assistant/voice activity captures resolve goal names exactly, clarify ambiguous/missing references, and audit writes.
- [x] Database migration preserves chat history and leaves the old project database intact.
- [x] Topic-linked Markdown notes are stored under the managed Knowledge folder.
- [x] Local FTS5 search covers notes and indexed materials and returns source paths.
- [x] Background material indexing supports TXT, Markdown, CSV, HTML, XML, JSON, LOG, YAML, DOCX, and selectable-text PDF files.
- [x] Local Q&A retrieves passages, constrains the local model to those excerpts, and appends source references.
- [x] Focused tests cover note indexing, material extraction, source-grounded answers, and workspace navigation.
- [x] Internal day agenda with separate planned-session and general-event records, rescheduling, cancellation, and goal/topic links.
- [x] Indexed date-range queries display local times; planned items do not create activities or change goal progress.
- [x] Calendar tests cover distinct record types, UTC persistence, overlapping day ranges, cancellation, and progress isolation.
- [ ] OCR for image-only documents and semantic/vector search.
- [x] Today dashboard with local date and greeting, priorities, daily checklist, active-goal previews, actual-work heatmap, and continue card.
- [x] Checklist completion creates one linked actual activity; planned time and estimates remain distinct from recorded work.
- [x] Goal-linked garden stages derive from completed milestones and logged activities.
- [x] Daily Curiosity prompts are connected to local topics and notes, with saved discoveries in Knowledge.
- [x] Projects and Study Hubs connect goals, topics, notes, materials, and activities.
- [x] Adaptive learning states and evidence history, source-grounded quizzes, recorded attempts, and actionable recommendations.
- [x] Editable profile and persistent memories; new and edited memory text requires user approval before entering assistant context.
- [x] Universal search across goals, milestones, projects, calendar plans, activities, topics, notes, materials, saved discoveries, and indexed passages.
- [x] Structured quick capture, weekly review, research comparison, focus sessions, confirmed goal-aware replanning, and ZIP backup export.
- [x] Small contextual help button with plain-language guides for every main page and common tools.

## Voice and follow-on features

**Wake-word code status:** Implemented in starter; microphone accuracy and behavior still require verification on the target laptop.

- [x] Push-to-talk speech-to-text with an editable transcript using an offline Vosk model (requires one-time model setup).
- [x] Optional spoken replies using the Windows offline speech engine via pyttsx3.
- [x] Local Vosk wake-word detection for “Amigo” while the window is hidden in the tray or already open and idle.
- [x] Main window begins command capture when the wake word is heard, then submits after a pause in speech.
- [x] Wake-word replies appear as text and are spoken briefly; typed/manual-mic replies remain text-only.
- [x] Tray menu and Mic context menu allow microphone selection; common non-16 kHz input is resampled for Vosk.
- [x] Subtle animated heartbeat indicator while the microphone listener is active.
- [x] Wake-word detection opens the main workspace window and submits silence-ended voice commands.
- [x] Persistent profile and user-approved memories with view/edit/delete controls.
- [ ] Semantic/vector memory search.
- [ ] More complete task planning, retries, cancellation, and outcome verification.
- [ ] Optional OS-level sandbox/restricted-user deployment.

## Test and verification checklist

Before treating desktop capabilities as verified on the laptop:

1. Run the local suite: `python -m unittest discover -s tests -v` (includes migration, service, Knowledge, UI, file-copy, and 10,000-activity progress-query checks; four symlink cases may skip when Windows denies symlink creation).
2. Run the Ollama smoke test: `python tests/test_model_smoke.py` (tool calls are inspected but not executed by that test).
3. Launch Amigo and test the stage currently enabled in `.env`.
4. Confirm the result exists where expected; do not count a natural-language success claim alone as verification.
5. Update this file's stage checklist and change log below.

## Change log

- **2026-10-23:** Established Amigo project branding; fixed workspace path under project root; hardened path validation against absolute paths, traversal, links/junctions and NTFS alternate data streams; made file creation exclusive; routed report saving through guarded file tools; expanded workspace security tests; created this capability tracker.
- **2026-10-23:** Hardened Stage 3 cloud request privacy with `store: false`, expanded mocked API/routing tests, and clarified key setup and cloud-data handling in the README.
- **2026-10-23:** Added per-chat sessions and History view, first-launch clean chat, a compact coastal-themed UI, streaming local responses, offline push-to-talk transcription, optional offline speech output, and faster no-tool routing for ordinary conversation.
- **2026-10-23:** Added local Vosk wake-word listener for “Amigo”, hidden-by-default tray startup, assistant window, silence-ended automatic voice commands, tray pause/resume controls, and a debug launcher.
- **2026-10-23:** Repaired over-narrow desktop-action routing; added streamed tool-call responses with cancellation support, Stop/Cancel UX for model requests and wake transcripts, a short pre-send transcript cancel window, explicit confirmation for file deletion, guarded text-file updates, workspace-folder opening, and a smaller active-chat context cap.
- **2026-10-23:** Added deterministic workspace action routing for open/read/update/delete/list, ambiguity prompts for colliding file/folder names, verified update writes, append-generated-text behavior, Start Menu shortcut launching for apps beyond built-in aliases, and hard-link protection before opening workspace files. Added regression tests for the reported intent failures.
- **2026-11-04:** Added the first goal/activity domain layer, versioned local SQLite schema, migration to LocalAppData, managed Materials/Knowledge roots, goal and activity UI, linked topic/material metadata, deterministic assistant activity capture, and a 10,000-record progress-query budget check.
- **2026-11-04:** Put Goals in the assistant's left workspace column, added Markdown notes and background indexing for readable materials, enabled FTS5 search and source-grounded local Q&A, and added Knowledge/UI regression tests.
- **2026-11-04:** Added an internal day agenda with separately stored planned sessions and calendar events, goal/topic links, rescheduling/cancellation, local-time display over UTC storage, and progress-isolation tests.
- **2026-11-05:** Added Today with actual-work heatmap, checklist, priorities, goal preview, continue card, curiosity prompt, and goal garden; added Projects/Study Hubs, learning evidence/quizzes/recommendations, approved editable profile memories, global search, structured capture, weekly review, research comparison, focus sessions, confirmed replanning, and database/material/Knowledge ZIP export. Added regression tests for checklist idempotence, learning state, memory approval, focus logging, search, and backup.
- **2026-11-06:** Refined the desktop UI with an uncluttered page switcher, warm neutral surfaces, clearer active navigation, compact dynamic focus priorities, and Georgia display headings paired with Segoe UI body text.
- **2026-11-06:** Added a small top-bar Help button that opens a plain-language guide at the current workspace page.
