# LearningSys

LearningSys is a local FastAPI web app for novice software engineering practice. Students open the site in a browser, choose one of four workflow roles, and complete a guided debugging exercise based on a curated BugsInPy `tqdm` task. The system stores session data locally in SQLite and creates a per-session copy of the practice repository.

## What Students Do

Students complete four separate practice sessions:

1. Task Planner
2. Patch Author
3. Code Reviewer
4. Test Runner

Each session pauses when it reaches the student's assigned role. The student writes that step's response, sends it through the interface, and the workflow continues automatically.

## Required Software

Students should install the following before starting:

- `git`
- Python `3.9+`
- A terminal
- Internet access to GitHub
- Internet access to the Amplify/Vanderbilt API

Important notes:

- On the inspected machine, `python3` existed but `python` did not.
- The repo's `launch.sh` and `start.sh` scripts currently call `python`, so the most reliable instructions use explicit `python3` or `py -3` commands instead of the shell scripts.
- On native Windows, the built-in test commands use `python3 -m pytest ...`, so WSL or Git Bash is the safest option for completing all four tasks end to end.

## Repository Setup

### 1. Clone the Repository

```bash
git clone https://github.com/littlehousezh/LearningSys.git
cd LearningSys
```

You should now be inside the project folder and see files such as `app.py`, `workflow.py`, `tasks.py`, `requirements.txt`, and `.env.example`.

### 2. Create a Virtual Environment and Install Dependencies

#### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

#### Windows PowerShell

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt
```

If installation worked, packages such as `fastapi`, `uvicorn`, `requests`, `python-dotenv`, and `pytest` will be available in the virtual environment.

### 3. Configure Environment Variables

This repo already includes an `.env.example` file. Copy it to `.env` and then add your own API token.

#### macOS / Linux

```bash
cp .env.example .env
nano .env
```

#### Windows PowerShell

```powershell
Copy-Item .env.example .env
notepad .env
```

Use this format with your own value:

```env
AMPLIFY_BASE=https://prod-api.vanderbilt.ai
AMPLIFY_BEARER=your_token_here
AMPLIFY_MODEL_ID=gpt-5
SWE_BENCH_DATASET=princeton-nlp/SWE-bench_Lite
SWE_BENCH_SPLIT=test
```

Do not commit or share `.env`.

For the current built-in student workflow, the most important variable is:

- `AMPLIFY_BEARER`

Optional model overrides are also supported by the app:

- `AMPLIFY_MODEL_ID_PLANNER`
- `AMPLIFY_MODEL_ID_CODER`
- `AMPLIFY_MODEL_ID_REVIEWER`
- `AMPLIFY_MODEL_ID_TESTER`
- `AMPLIFY_MODEL_ID_SUPPORT`

### 4. Launch the Learning System Locally

Start the FastAPI server with an explicit Python command.

#### macOS / Linux

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8080
```

#### Windows PowerShell

```powershell
py -3 -m uvicorn app:app --host 0.0.0.0 --port 8080
```

If the server starts correctly, you should see output indicating that Uvicorn is running and listening on port `8080`.

Optional health check:

```bash
curl http://127.0.0.1:8080/api/health
```

Expected result:

```json
{"status":"ok","loaded_sessions":"0"}
```

### 5. Open the Website and Start a Session

Open this URL in your browser:

```text
http://127.0.0.1:8080/
```

You should see the `AgentForge` study hub with:

- A `Participant ID` input box
- Four practice cards
- A table showing the four curated BugsInPy practice tasks

Enter your participant ID before starting. Use the same participant ID for all four tasks so your data stays grouped together.

## Complete All Four Practice Tasks

The current frontend is wired to these four curated tasks:

1. Task Planner practice: `bugsinpy-tqdm-1`
2. Patch Author practice: `bugsinpy-tqdm-4`
3. Code Reviewer practice: `bugsinpy-tqdm-2`
4. Test Runner practice: `bugsinpy-tqdm-5`

### 6. Finish Each Session

For each practice card:

1. Enter your participant ID.
2. Click the matching `Start ... Practice` button.
3. Wait while the system creates the session.
4. On the first run, the app may clone `tqdm/tqdm` from GitHub and check out the required commit.
5. Watch the workflow auto-run until it reaches your assigned role.
6. When the status says it is waiting for your input, edit the provided response scaffold.
7. If you want help drafting, click `AI Coach`.
8. Review your answer carefully.
9. Click `Send`.
10. Let the workflow continue until it finishes.
11. Return to the study hub and begin the next practice card.

### What Students Should Expect to See

When things are working correctly:

- The homepage loads with all four practice cards.
- Clicking a card opens a workflow session.
- The system status changes as the workflow progresses.
- The app pauses on the student's assigned role.
- A structured response template appears in the text box.
- After clicking `Send`, the workflow continues automatically.
- When the workflow finishes, the session status becomes complete.

## Where Data, Logs, and Results Are Saved

The current code saves student work in these locations:

- `data/study_sessions.db`
- `data/session_workspaces/`

Details:

- `data/study_sessions.db` is the main SQLite database. It stores sessions, workflow events, student responses, AI support drafts, patch/test records, and other session metadata.
- `data/session_workspaces/` stores a separate working copy of the task repository for each session.

The app also uses:

- `repos/`

That folder contains cached upstream repo snapshots used to create new student workspaces. It is support data, not the main student submission target.

If `LEARNINGSYS_WORKSPACES_ROOT` is set, session workspaces will be saved there instead of `data/session_workspaces/`.

## How to Create the Submission Zip

### 7. Files Students Should Submit

Based on the current storage design, students should zip:

- `data/study_sessions.db`
- `data/session_workspaces/`

If a custom `LEARNINGSYS_WORKSPACES_ROOT` was used, zip that workspace folder instead of `data/session_workspaces/`.

Students should not include:

- `.env`
- `.venv`
- `.git`
- `repos/`

### 8. Zip the Required Files

#### macOS / Linux

```bash
zip -r submission_<your_participant_id>.zip data/study_sessions.db data/session_workspaces
```

#### Windows PowerShell

```powershell
Compress-Archive -Path data\study_sessions.db, data\session_workspaces -DestinationPath submission_<your_participant_id>.zip
```

If you used a custom workspace root:

#### macOS / Linux

```bash
zip -r submission_<your_participant_id>.zip data/study_sessions.db <your_custom_workspace_folder>
```

#### Windows PowerShell

```powershell
Compress-Archive -Path data\study_sessions.db, <your_custom_workspace_folder> -DestinationPath submission_<your_participant_id>.zip
```

### 9. Submit the Zip to the Instructor

This project does not currently include an in-app submission feature. Students should upload the zip file using whatever submission method the instructor specifies.

## Troubleshooting

### `python: command not found`

Use `python3` on macOS or Linux. The current shell scripts assume a `python` alias exists, which may not be true on student machines.

### `No module named fastapi` or `No module named pytest`

The virtual environment is either not activated or dependencies have not been installed yet.

Run:

```bash
python3 -m pip install -r requirements.txt
```

or on Windows:

```powershell
py -3 -m pip install -r requirements.txt
```

### The site loads, but AI help or auto-generated steps fail

Check `.env` and confirm that:

- `AMPLIFY_BEARER` is set
- The token is valid
- The server was restarted after editing `.env`

### A practice task fails to start

Possible causes:

- `git` is not installed
- GitHub is not reachable from the current network
- The first-time clone is still in progress

### Port 8080 is already in use

Start the server on a different port:

```bash
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/
```

### Windows PowerShell blocks virtual environment activation

Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

Then activate the virtual environment again.

## Instructor Checklist

- Provide each student with an Amplify/Vanderbilt API token.
- Tell students to use one consistent participant ID across all four sessions.
- Have students complete all four practice cards.
- Confirm students start the server with explicit Python commands, not the current shell scripts.
- Collect a zip containing `data/study_sessions.db` and `data/session_workspaces/`.
- Do not collect `.env`, `.venv`, `.git`, or `repos/`.
- Recommend WSL or Git Bash for Windows students if they need the built-in testing flow to work reliably.

## Missing Documentation and Setup Problems Found

- The top-level `README.md` and `STUDENT_SETUP.md` were missing from the working tree when this guide was written.
- `launch.sh` and `start.sh` currently call `python` instead of `python3`, which fails on systems where only `python3` exists.
- `launch.sh` prints a message referring to `VANDERBILT_BEARER`, while `.env.example` primarily documents `AMPLIFY_BEARER`.
- The built-in test commands use `python3 -m pytest ...`, which can be awkward for native Windows setups.
- The app has no built-in export or submit button for students, even though the backend stores complete local session data.
- The `repos/` cache is created on first use, but that behavior is not obvious to first-time users.
- `.env.example` includes `SWE_BENCH_*` values even though the current student-facing practice flow uses curated BugsInPy tasks.

