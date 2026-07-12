# Skill: Dependency Update

> **Trigger**: "bump", "upgrade", "update dependency", "requirements.txt", "package version"

## Procedure

### 1. Identify scope
- Find the exact line(s) in `requirements.txt` for the package(s) in
  question. Note which tool(s) actually import it — `grep -rn "^import <pkg>\|^from <pkg>" --include="*.py"`.
- Check if the package has a pinned version (`==`) vs. a floor (`>=`).
  This repo uses `>=` floors almost everywhere — preserve that style unless
  the task specifically asks for a pin (pins are usually only justified when
  a newer version is known-broken).

### 2. Check for breaking changes before touching the file
- Look up the target version's changelog/release notes (web search if
  available) for breaking API changes, especially for:
  - `faster-whisper` — transcription API (used by clipharvest, autochapters)
  - `opencv-python-headless` — cv2 API (used by watermarkwipe, aspectshift)
  - `pyrogram` / `tgcrypto` — MTProto client (used by bot/mtproto_transfer.py
     for >50MB Telegram delivery — a breaking change here silently kills
     LofiLoop's 2GB delivery path)
  - `librosa` — audio analysis (clipharvest scorer)
- If the changelog mentions a renamed function/class/parameter that this
  repo calls, that's a blocking issue — either pin below the breaking version
  or make the corresponding code change as part of the same PR (not silently
  — call it out in the commit message).

### 3. Make the minimal edit
- Change only the version specifier on the line(s) in question.
- Don't reformat or reorder the file, don't touch its section comments.
- If pyproject/pip-tools/lockfiles existed in future, honor them too — but as
  of this writing this repo uses a single flat `requirements.txt`.

### 4. Test before committing
```bash
pip install -r requirements.txt
# Import-check every tool that touches the changed package:
python -c "import <affected_tool>.main" && echo OK
python -c "import bot.run_job" && echo OK
# If the package has a CLI/quick smoke test, run it:
python <affected_tool>/main.py --help
```
If there's no way to fully exercise the changed code path in this sandboxed
environment (e.g. no real video for ffmpeg + cv2), at minimum confirm imports
succeed and note in the commit body that deeper testing should happen on the
next real job run.

### 5. Commit
`chore(deps): bump <package> to <version>[ - <breaking change note if any>]`

## Anti-patterns (do NOT)

- Do not bump multiple unrelated packages in one commit — one logical change
  per commit, per `AGENTS.md` rule 6.
- Do not switch a `>=` floor to `==` pin without a stated reason.
- Do not remove a package "because it looks unused" without grep-confirming
  zero imports across the entire repo first.

## Example

**Input**: "Bump faster-whisper to the latest version"

**Output**:
```
Checked faster-whisper 1.0.1 -> 1.1.0 changelog: no breaking API changes,
WhisperModel.transcribe() signature unchanged.

Files changed:
  requirements.txt — faster-whisper>=1.0.1 -> faster-whisper>=1.1.0

Verified:
  pip install -r requirements.txt  OK
  python -c "import clipharvest.main"    OK
  python -c "import autochapters.main"   OK

Commit: chore(deps): bump faster-whisper to >=1.1.0
```
