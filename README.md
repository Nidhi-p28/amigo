# Amigo — Local-first Desktop Agent (Windows starter)

Amigo is a local-first Windows desktop companion. Its main window brings Today, Goals, Projects and Study Hubs, Learning, Knowledge, and Calendar together with assistant chat. Chat uses a local Ollama model, workspace actions use guarded tools, and web research can optionally route to Gemini with Google Search grounding.

This is a learning MVP, not a fully autonomous computer-use agent. Each launch starts a fresh chat and keeps older sessions in **History**. Local chat streams as it is generated. The **Goals** workspace supports goals, milestones, and completed activity logs. **Knowledge** adds topic-linked Markdown notes, local full-text search, document indexing, and answers grounded in retrieved local passages. Voice wake-up can open the main window from the system tray and submit silence-ended commands.

**For the live feature checklist and stage-by-stage progress, see [`CAPABILITIES.md`](CAPABILITIES.md).**

## Quick start — Windows

1. Install Python 3.11+ and [Ollama](https://ollama.com/download), then clone the repo:

   ```powershell
   git clone https://github.com/Nidhi-p28/amigo.git
   cd amigo
   ```

2. Set up the Python environment and local settings:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. Download the default local model once and make sure `OLLAMA_MODEL` in `.env` matches it:

   ```powershell
   ollama pull qwen3.5:4b
   ```

4. Open Amigo by double-clicking `run_amigo.bat`, or run `\.venv\Scripts\python.exe -m app.main` from PowerShell.

Your `.env`, local database, downloaded voice model, and workspace files are excluded from Git. The wake-word voice model is optional; run `powershell -ExecutionPolicy Bypass -File .\setup_voice.ps1` if you want that feature.

## Security first: workspace-only file tools

Amigo's model-exposed file tools are confined to `%USERPROFILE%\Documents\Amigo\My Materials`. The root is fixed in application code and cannot be redirected through `.env`. The SQLite database is stored in `%LOCALAPPDATA%\Amigo\amigo.sqlite3`.

- Absolute paths and path traversal are rejected, including Windows backslash paths.
- Symbolic links and junctions are rejected at the workspace root and along accessed paths; linked entries are displayed as blocked rather than followed.
- NTFS alternate data stream syntax is rejected, and reading files with multiple hard links is conservatively blocked.
- New files are created with exclusive mode; existing files are never overwritten by the current tools.
- Research reports are written through the same guarded file tool.
- The model is not given arbitrary shell/PowerShell execution or general GUI automation tools. Updating supported text files requires explicit user intent, and file deletion always asks for UI confirmation.
- App launching uses an allowlist.

**Important limitation:** this is strict application-level isolation for Amigo's exposed tools, not a complete Windows OS sandbox. The Python process runs under your Windows account, so compromised application code/dependencies or a separately compromised process are outside what a path-checking layer can prevent. If you need protection against that stronger threat model, run Amigo under a dedicated low-privilege Windows account or suitable OS/container sandbox that grants write access only to the workspace. On first launch, Amigo copies an existing `data/amigo.sqlite3` into LocalAppData using SQLite's backup API and leaves the original database and project `workspace/` files in place. No workspace files are moved automatically.

## Hardware and model

The project is designed as a starting point for Windows with 16 GB RAM and a 6 GB GPU. Keep your existing Mistral model installed and test `qwen3.5:4b` alongside it rather than removing Mistral. Compare regular chat and tool-call reliability: a newer model is not automatically better for every task.

- Qwen model page: https://ollama.com/library/qwen3.5:4b
- Ollama local API docs: https://docs.ollama.com/api
- Gemini Google Search grounding: https://ai.google.dev/gemini-api/docs/google-search

## Setup

1. Install Python 3.11 or newer and Ollama for Windows.
2. In PowerShell, pull a model (you can keep Mistral too):

   ```powershell
   ollama pull qwen3.5:4b
   ollama list
   ```

3. Open PowerShell in the extracted `amigo_desktop_agent` folder and create a virtual environment:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

   If PowerShell blocks activation, you can use `\.venv\Scripts\python.exe -m pip install -r requirements.txt` without activating it.

4. Edit `.env`. Ensure `OLLAMA_MODEL` exactly matches an entry shown by `ollama list`. Keep the real Gemini key only in `.env`; never paste it into chat or commit it.
5. Run local workspace safety tests first:

   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -v
   ```

   The cloud tests use mocked responses; they do not call Google or use API credits.

6. Check the model before opening the desktop app:

   ```powershell
   .\.venv\Scripts\python.exe tests\test_model_smoke.py
   ```

   This checks the Ollama connection, normal chat, and whether the model emits a tool call. The tool-call check does **not** execute the tool.
7. Start Amigo:

   ```powershell
   .\.venv\Scripts\python.exe -m app.main
   ```

   Or double-click `run_amigo.bat` after setup.

## Stage-by-stage switches

Edit `AMIGO_STAGE` in `.env`, then restart Amigo. The default is Stage 1, so desktop tools and cloud routing are not enabled initially.

### Stage 1 — Desktop companion + local chat

```dotenv
AMIGO_STAGE=1
OLLAMA_MODEL=qwen3.5:4b
```

Try:
- `Hi, introduce yourself in two sentences.`
- `Explain what a Python virtual environment is.`

The PySide6 window supports text chat and keeps recent conversation history in a local SQLite database. This is conversation persistence, not long-term profile/semantic memory.

### Stage 2 — Desktop and workspace tools

```dotenv
AMIGO_STAGE=2
```

Restart Amigo. Supported actions include opening allowlisted apps and validated workspace folders, opening an HTTP(S) URL, listing workspace files, creating folders/files, reading supported text files, updating supported text files, and deleting workspace files after a confirmation prompt.

Try:
- `Open Notepad.`
- `Open VS Code.`
- `List the files in my workspace.`
- `Create a folder named demo.`
- `Create a file named hello.txt containing Hello World.`
- `Read hello.txt.`
- `Rename hello.txt to welcome.txt.`
- `Move welcome.txt into folder demo.`

**All model-exposed file paths are relative to the fixed My Materials folder.** For example: `demo/hello.py`. Absolute paths, traversal, symbolic links/junctions, and NTFS alternate data stream paths are blocked. Creating files, renaming items, and moving items never overwrites an existing workspace item. Rename keeps an item in its current folder; move accepts a workspace-relative destination path or an existing folder. Missing destination folders are not created automatically. Supported text files can be updated when explicitly requested; deleting a workspace file requires a confirmation dialog. Deleting folders is not supported.

Application launching never uses arbitrary shell commands. Built-in aliases include `vscode`, `notepad`, `calculator`, `paint`, `explorer`, `chrome`, and `edge`. Other installed applications can be launched by name when they have a Windows Start Menu shortcut; apps without a shortcut (including some portable apps) may not be discoverable. Workspace file/folder opening is a separate action from application launching. For a spoken or incomplete workspace name, Amigo searches within My Materials and asks you to choose when multiple items match. Clear read/open/update/delete/list/rename/move commands use direct local routing, so they do not need Ollama to guess which tool to call. Updates and file moves/renames are verified before success is reported, and deleting an individual file always requires UI confirmation.

### Stage 3 — Cloud routing + grounded web research + reports

**Turn this stage on only after you have a Gemini API key.** Open Google AI Studio at https://aistudio.google.com/ and use its API Keys page to create a key. Then open `.env` in Notepad and set these values (keep your existing Ollama settings):

```dotenv
AMIGO_STAGE=3
GEMINI_API_KEY=paste_your_key_here
GEMINI_MODEL=gemini-3.8-flash
```

Save `.env`, close Amigo completely, and start it again. Do not paste the real key into source code, a message, a screenshot, or a GitHub commit. `.env` is excluded by `.gitignore`. Check the current model availability, usage limits, and pricing for your Google AI Studio project before relying on free usage. Google documents that Google Search grounding can make multiple search calls for one request, so a single research prompt may use more than one search.

Requests explicitly asking for current information or web research are routed to Gemini with Google Search grounding. Ordinary chat and local tools remain on Ollama, including long/detailed prompts; Amigo does not silently send long prompts to the cloud just because they are long. Routing uses a simple keyword heuristic, so some requests may be routed incorrectly.

**Privacy detail:** only prompts routed to Stage 3 are sent to Google. The request sets `store: false` so the Interactions API does not retain the interaction for later conversation chaining. This is not a promise about all processing or logging under Google's service terms. Amigo does not automatically attach the contents of your workspace files to a web request.

Try these messages in Amigo:
- `Search the web for recent developments in local AI models and summarize the most relevant ones.`
- `Research the latest developments in local AI models and save a report.`

When you request a report, Amigo creates a new Markdown file under `reports/` in My Materials, using the same guarded file-creation tool. It will not overwrite an existing report. Sources are shown when the API returns citation annotations; verify important sources before relying on a summary.

## Voice — Wake word + offline speech

Voice settings are independent of `AMIGO_STAGE`; keep your current value (for example, `3`) so Stage 2 workspace tools and Stage 3 web research remain enabled. `run_amigo.bat` starts Amigo without a console window when `pythonw.exe` is available. Amigo stays in the system tray until it hears “Amigo”, then opens its main window with the workspace beside chat.

1. Merge the contents of the update patch into the existing project, replacing matching code files. Keep your `.env`, legacy `data/amigo.sqlite3`, and project `workspace/` files; Amigo leaves those legacy data files intact while using the new user data folders.
2. Install the added packages in the existing virtual environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. From the Amigo project folder, run the one-time model setup script:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup_voice.ps1
   ```

   It downloads the official small Vosk English model (about 40 MB) into `data/vosk-model-small-en-us-0.15`. Speech recognition and reply speech then run locally. The first download needs an internet connection; audio is not sent to a cloud speech service by this feature.

4. Start Amigo with `run_amigo.bat`. The window stays hidden in the system tray. Say **“Amigo, open Chrome”** as one phrase to try a one-shot command. Or say **“Amigo”**, wait for the window's listening indicator, then speak your command and pause. Amigo sends that transcribed command automatically and reads a short reply aloud, also showing it as text. When you type or use the manual Mic button, replies remain text-only.

Wake-word mode keeps the microphone open for local detection of “Amigo” whenever Amigo is idle, whether the window is hidden in the tray or already visible. Saying “Amigo” opens/focuses the main window and immediately begins listening for a command. You can also say “Amigo, open Chrome” as a single utterance. Voice-wake requests receive a short response that is shown as text and spoken aloud; typed prompts and manual Mic recordings remain text-only. The detector releases the microphone while Amigo transcribes a command, runs a task, or speaks a reply, then resumes.

Wake listening and speech recognition use the local Vosk model; idle audio is not sent to Google or Ollama. Click **Mic** for manual recording; its right-click menu lets you choose a microphone. The same microphone selection is used for wake listening and transcription, and Amigo resamples typical 44.1/48 kHz headset input to Vosk's 16 kHz rate. You can also choose a microphone from the tray menu. Use “Pause wake word listening” to stop microphone listening and “Resume wake word listening” to restart. Set `AMIGO_WAKE_WORD_ENABLED=false` in `.env` to disable wake-word mode.

The small English model is speed-oriented, and transcription quality still varies with accent, microphone quality, and background noise. If recognition remains poor after selecting the right input device, the next step is replacing Vosk command transcription with a heavier local Whisper model.

## Goals, activities, and Knowledge

The main window opens with **Goals** in the left workspace column and chat on the right. It supports goal creation/editing, active/paused/completed states, milestones, actual activity history, linked topics, and derived progress summaries. Progress is based on completed milestones and recorded activities; Amigo does not store a separate progress percentage.

Use **Log activity** for form-based capture, or type a clear quick-capture request such as:

```text
Log activity: Read about LoRA for 45 minutes for goal ML foundations topic: LoRA
```

Amigo resolves a named goal by exact case-insensitive title first, then by a unique partial title match. Missing or ambiguous goal names produce a clarification/error and do not create an activity. Assistant and voice writes are recorded in the local action history. Natural past-tense captures such as `I studied probability for 30 minutes today` are also supported; ordinary questions do not create activity records.

Use **Import a material…** in Goals or Knowledge to select an original file and a topic. Amigo copies the file into `%USERPROFILE%\Documents\Amigo\My Materials`, records its metadata and topic link, and leaves the original unchanged. Imports are limited to 250 MB. Readable `.txt`, `.md`, `.csv`, `.html`, `.xml`, `.json`, `.log`, `.yaml`, `.docx`, and `.pdf` files can be indexed for local search. PDF indexing uses pypdf and only extracts selectable text; image-only pages need OCR, which is not included. Indexing runs in a background worker. **Index all** reprocesses registered materials.

In **Knowledge**, create Markdown notes and assign a topic. Notes are stored under `%USERPROFILE%\Documents\Amigo\Knowledge`; the FTS5 index supports local keyword search across notes and indexed materials. Select a result and choose **Ask Amigo about these sources**, or ask in chat, for example: `What do my notes say about chlorophyll?` Answers use retrieved passages and show their local source paths. The Knowledge phase does not include OCR, semantic/vector search, quizzes, or adaptive mastery.

## Internal Calendar

Open **Calendar** in the left workspace. The day agenda supports planned work sessions linked to multiple goals/topics and separate general calendar events. Items can be edited, rescheduled, or cancelled; cancelled records can be included in the agenda history. Session and event times are stored in UTC and shown in the computer's local time zone.

Planned sessions, general calendar events, completed activities, and goal milestones have separate records. Scheduling or cancelling an item never creates an activity and never changes goal progress. Log work that actually happened from **Goals → Log activity**. This phase is local only and does not synchronize an external calendar.

The first database open copies an existing project `data/amigo.sqlite3` into `%LOCALAPPDATA%\Amigo\amigo.sqlite3`, preserves its conversations, and leaves the old database in place. Existing project `workspace/` contents are not copied automatically; select files through **Import a material…** when you want a copy in the managed materials folder.

## UI, sessions, and latency changes

- The main window has Goals/Knowledge/Calendar on the left and assistant chat on the right; the Goals screen is no longer a separate dialog.
- Calendar queries read planned sessions and calendar events, not completed activities or milestones.
- Document import and reindexing run in background workers so large files do not freeze the window.
- Knowledge answers retrieve a small set of FTS5 passages and display the source material/note paths used.
- Each launch begins with a blank chat. Old conversations remain in the local SQLite database and can be opened through **History**.
- Ordinary local replies stream into the UI instead of waiting for the full answer before displaying any text.
- Local tool definitions are sent when the request appears to ask for an app or workspace action. The action-intent check considers broad action verbs and objects instead of relying on a short list of exact phrases. Simple, unambiguous allowlisted app launches bypass Ollama. This is still a heuristic, so verify consequential actions.
- A **Stop** button is available during a running request, and **Cancel** is available while a wake-triggered transcript is being captured or is waiting to auto-send. Cancellation closes the active Ollama/Gemini response when possible; it cannot undo an action that already completed.
- For local model requests, only the most recent four messages from the active conversation are included, capped at 3,200 total characters. Other saved History remains local and is not automatically sent as model context.
- Web research is still an external Gemini + Google Search request and may be slower or stop when your Google API quota/rate limit is reached. Streaming improves perceived latency for ordinary local chat; it cannot make Google's research service respond faster or grant more quota.

## Project layout

```text
amigo_desktop_agent/
├── app/
│   ├── main.py          # hidden/tray startup and app lifecycle
│   ├── ui.py            # two-column workspace/chat window, history, tray controls
│   ├── agent.py         # orchestration and local tool-call loop
│   ├── tools.py         # allowlisted app launch and workspace-only file tools
│   ├── cloud.py         # Gemini routing and grounded web requests
│   ├── storage.py       # versioned SQLite schema, migration, and chat history
│   ├── services.py      # goal, activity, knowledge-link, and progress services
│   ├── knowledge.py     # Markdown notes, extraction, indexing, and FTS5 search
│   ├── calendar.py      # planned-session/event services and indexed agenda queries
│   ├── calendar_ui.py   # local day agenda and session/event forms
│   ├── domain.py        # typed domain states and records
│   ├── product_ui.py    # Goals and Knowledge workspace panels
│   ├── voice.py         # wake listener, speech input, offline spoken replies
│   ├── wakeword.py      # wake-word text matching helpers
│   └── config.py        # fixed workspace boundary and settings
├── tests/
│   ├── test_tools.py
│   ├── test_cloud.py
│   ├── test_storage.py
│   ├── test_services.py
│   ├── test_knowledge.py
│   ├── test_calendar.py
│   ├── test_ui_layout.py
│   ├── test_agent_routing.py
│   ├── test_agent_stream.py
│   ├── test_voice.py
│   └── test_model_smoke.py
├── workspace/           # legacy source-checkout materials; not auto-migrated
├── setup_voice.ps1      # one-time download of the offline voice model
├── data/                # legacy database source and cached speech model
├── CAPABILITIES.md
├── requirements.txt
├── run_amigo.bat        # normal, no-console startup when pythonw is available
└── run_amigo_debug.bat  # visible console for troubleshooting
```

Runtime user data is outside the checkout: SQLite and settings live in `%LOCALAPPDATA%\Amigo\`; original materials and future Markdown notes live under `%USERPROFILE%\Documents\Amigo\`.

## Known limitations

- Continuous local wake-word detection is included. A small heartbeat indicator is shown while listening; recognition still depends on the Vosk model, microphone, language, and accent. Long-term semantic memory is not implemented yet.
- Goal, activity, progress, topic links, Markdown notes, text extraction, FTS5 retrieval, source-grounded local Q&A, and an internal calendar are implemented. OCR, external calendar sync, assessments, and recommendations remain later phases.
- Performance targets still need verification on the stated 16 GB RAM / 6 GB GPU target. PDF extraction requires the newly listed `pypdf` package from `requirements.txt`.
- Amigo can update supported text files, rename and move workspace files/folders, and delete workspace files after confirmation. It cannot delete folders.
- The text rendering is simple; Markdown display and rich source cards can be improved later.
- The router is heuristic and can choose the wrong model.
- Small local models may fail to call tools or choose poor arguments. Test rather than assuming.
- The current web-search client uses the Gemini Interactions API with `store: false`. If the account/model does not support the configured model or Google Search grounding, choose an available supported model and update `.env`.
- The app-level workspace boundary is not a complete OS sandbox; see the security note above.

## Troubleshooting

- **Amigo is not visible after launch:** Look for its icon in the Windows system tray, including the `^` hidden-icons area. Say “Amigo” to open the main window, or right-click the tray icon and choose Show Amigo. For startup errors, run `run_amigo_debug.bat` to see the console.
- **Wake word or transcription is inaccurate:** Confirm the Vosk folder `data/vosk-model-small-en-us-0.15` exists and Windows microphone access is allowed. Right-click Mic (or use the tray menu) and select the actual headset/laptop microphone, then try “Amigo, open Calculator.” If needed, choose a nearby quiet room and speak clearly after the heartbeat/listening status appears.
- **Ollama not reachable:** Open Ollama and run `ollama list` in PowerShell.
- **Model not found:** Run the exact `ollama pull ...` command for `OLLAMA_MODEL` and check `ollama list`.
- **No tool call:** Run the model smoke test and compare models using the same task.
- **App not found:** Use an allowlisted alias; for VS Code, enable the `code` command in PATH or update the explicit executable lookup in `app/tools.py`.
- **Gemini error:** Verify the API key, selected model, API availability, quotas, and rate-limit/billing settings.
- **Workspace security test fails:** Do not enable Stage 2 or Stage 3 until the failure is understood.

As stages are completed and verified on your laptop, update [`CAPABILITIES.md`](CAPABILITIES.md). Do not mark a stage verified merely because its code exists.


### Wake-word behavior and privacy

- Amigo uses the offline Vosk model to listen for the wake word locally while it is in the tray. It does not stream idle microphone audio to a cloud service.
- After detection, the main window appears and begins local transcription; wait until the status reads **Listening**, speak your command, then pause to submit. The simplest first test is: say “Amigo”, wait for the listening message, then say “Open Calculator”.
- Automatic voice commands can perform the same allowlisted app launches and workspace operations as typed commands. File tools remain restricted to `workspace/`; do not speak secrets you do not want submitted to the configured model.
- Right-click the Amigo tray icon to pause/resume wake listening or quit. The window close button hides it; it does not quit the background listener.
