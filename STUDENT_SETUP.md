# Student Setup and Study Instructions

This guide is written for students who need to run AgentForge locally, complete their assigned study task, and send their study data back afterward.

## What this system is

AgentForge is a local study website that walks through a four-step software-engineering workflow:

1. `Task Planner`
2. `Patch Author`
3. `Code Reviewer`
4. `Test Runner`

You will complete one of those roles yourself. The system handles the other roles automatically.

## Before you start

Make sure you have all of the following:

- Python 3.9 or newer
- `git`
- Internet access
- the project folder on your computer
- your assigned participant ID

Important:

- Use the same participant ID for the whole study.
- Do not change your participant ID partway through.
- Do not delete the `data/` folder before submitting your study data.

## Part A. Complete local setup instructions

### 1. Open the project folder

If you already received the project as a folder, open a terminal and go into it:

```bash
cd /path/to/LearningSys-main
```

If you are cloning it yourself:

```bash
git clone <REPO_URL>
cd LearningSys-main
```

### 2. Create a virtual environment

Run:

```bash
python3 -m venv .venv
```

### 3. Activate the virtual environment

On macOS or Linux:

```bash
source .venv/bin/activate
```

If activation worked, your terminal usually shows `(.venv)` at the start of the prompt.

### 4. Install the Python dependencies

Run:

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
```

### 5. Confirm the local environment file is present

In the instructor-prepared study copy, the token should already be configured in `.env`, so students should not need to type it.

Check that the file exists:

```bash
ls .env
```

If `.env` is missing, recreate it with:

```bash
cp .env.example .env
```

If you had to recreate `.env`, ask the researcher for a prepared copy instead of typing your own token unless they specifically instruct you to do so.

The file should look like this shape:

```env
AMPLIFY_BASE=https://prod-api.vanderbilt.ai
AMPLIFY_BEARER=YOUR_TOKEN_HERE
AMPLIFY_MODEL_ID=gpt-5
```

Important:

- If `AMPLIFY_BEARER` is missing, the website still opens, but the AI-controlled steps will not work correctly for a real study run.
- Students should not edit `.env` unless the researcher specifically tells them to.
- Do not upload or share your `.env` file with anyone unless the researcher explicitly asks for it.

### 6. Start the system

From the project root, run:

```bash
source .venv/bin/activate
python3 -m uvicorn app:app --host 0.0.0.0 --port 8080
```

Leave that terminal window open while you do the study.

### 7. Open the study website

Open this address in your browser:

```text
http://localhost:8080
```

### 8. Confirm the system is working

The system is working correctly if:

- the page opens in your browser
- you see the `AgentForge` title
- you see a `Participant ID` box
- you see four practice cards

If the page does not load, check the terminal for an error message first.

### Common setup errors

`python: command not found`

- Use `python3` instead of `python`.

`No module named fastapi` or another missing package error

- Your virtual environment is probably not activated, or dependencies were not installed.
- Run:

```bash
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

`Address already in use`

- Another program is already using port `8080`.
- Stop the old process, then run the start command again.

The page opens, but the AI text says the token is missing

- The prepared `.env` file may be missing, incomplete, or invalid.
- Contact the researcher instead of trying random token values.

Task creation takes a long time or fails

- The app may be downloading a benchmark repo snapshot the first time that task is opened.
- Make sure `git` works and your internet connection is active.

## Part B. Complete study participation workflow

### 1. Launch the system

Start the server with:

```bash
source .venv/bin/activate
python3 -m uvicorn app:app --host 0.0.0.0 --port 8080
```

Then open `http://localhost:8080`.

### 2. Enter your participant ID

In the browser:

- find the `Participant ID` box
- type your assigned participant ID exactly as given

Important:

- Use the exact same participant ID every time.
- Do not leave it blank.
- Do not switch to a different ID after starting.

### 3. Start the assigned study task

Click the practice card the researcher told you to complete.

When you click it:

- the system creates a new study session
- it prepares a repo workspace for that session
- it begins the workflow automatically

The first load may take a little longer while the repo is prepared.

### 4. Wait for your turn

The system will run the steps it owns automatically.

When it reaches your step:

- the workflow pauses
- a response box appears on the left
- the box is pre-filled with the format you should use

### 5. Complete your assigned role

Read:

- the task summary on the right
- the workflow board
- the live context panel
- any files shown in the file viewer, if needed

Then write your response in the response box.

Important:

- Keep the required headings that already appear in the response template.
- Replace the placeholder text with your own answer.
- Do not delete the whole template structure.

### 6. Use AI Coach only if your study instructions allow it

The `AI Coach` button can generate a draft for your current step.

If your study instructions say you may use it:

- click `AI Coach`
- wait for the draft to appear
- review it carefully
- edit it before sending if needed

If your study instructions say not to use it, leave that button alone.

### 7. Send your response

When your answer is ready:

- click `Send`

After that:

- your response is saved
- the workflow continues automatically
- the system may finish, or it may move to the next step

### 8. Wait for completion

Stay on the page until the interface shows that the workflow is complete.

In the current UI, completion is shown with a completed status message such as:

- `Workflow complete`
- or a message telling you that the practice task is complete and you can return to the Study Hub

### 9. Before closing the browser

Before you close anything, make sure all of these are true:

- your last response was sent successfully
- the workflow shows as complete
- there is no loading spinner still running
- the terminal is still open and the server has not crashed

### 10. Before stopping the system

Your data is written to the local SQLite database as the workflow runs, but do this before you shut everything down:

1. Wait until the workflow is clearly complete.
2. Leave the browser open for a few extra seconds.
3. Return to the terminal where the server is running.
4. Press `Ctrl+C` once to stop the server cleanly.

Stopping the server cleanly is important because the database uses SQLite WAL mode.

### Important current limitation

The current browser UI does not include:

- a session resume screen
- a built-in export/download button

That means:

- do not assume you can close the browser and reopen the same session from the UI later
- do not start over with a new participant ID if something goes wrong

If you accidentally close the tab or think something broke, contact the researcher before creating a replacement session unless they told you otherwise.

## Part C. Complete data submission instructions

### Where your data is stored

Your study data is stored inside the project folder in:

- `data/study_sessions.db`

Because the app uses SQLite WAL mode, you may also see:

- `data/study_sessions.db-wal`
- `data/study_sessions.db-shm`

### Which files you should submit

For the current codebase, the safest submission set is:

- `data/study_sessions.db`
- `data/study_sessions.db-wal` if it exists
- `data/study_sessions.db-shm` if it exists

These files contain the recorded study session data. In the current implementation, this is the cleanest participant-safe submission path because the browser does not provide its own export button.

### Recommended packaging method

After you stop the server:

1. Open the project folder.
2. Open the `data/` folder.
3. Select:
   - `study_sessions.db`
   - `study_sessions.db-wal` if present
   - `study_sessions.db-shm` if present
4. Compress those files into a single zip file.

Recommended zip filename:

```text
<participant-id>_agentforge_data.zip
```

Example:

```text
student-01_agentforge_data.zip
```

If you are not sure whether to include the `-wal` and `-shm` files, include them whenever they exist.

### How to send or upload the data

Send the zip file using the method the researcher gave you, such as:

- email
- shared drive upload
- course upload portal
- survey upload form

Include your participant ID in the filename or message so the researcher can match the file to your session.

### How to confirm the submission is complete

You are done when:

- the server has been stopped with `Ctrl+C`
- your zip file was created successfully
- the zip file includes the database file
- you uploaded or sent the zip file
- you received whatever confirmation the researcher expects

If your upload method does not give a confirmation message, keep the zip file until the researcher confirms receipt.

### Minimal safe submission note

The current codebase does support API exports in the backend, but the browser does not expose them directly to students and the UI does not display an obvious session export flow. Because of that, submitting the SQLite database files is the minimal safe submission method that matches the current implementation.
