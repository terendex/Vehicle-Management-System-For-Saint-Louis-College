# Re-taking the user-manual figures

Everything that produces the annotated screenshots in `docs/user-manual/images/`,
the in-app help figures in `frontend/src/assets/help/`, and the markdown the Word
document is built from.

This ran once before, in September 2026, from a scratch directory that was never
committed. That is why the first edition's pictures went stale with no way to
refresh them: the callout wording was printed **into** each picture, so it cannot
be corrected by editing the manual. It lives in the repository now.

## What is the source of what

| File | Owns |
|---|---|
| `shots/shots.mjs` | every figure: where to navigate, what to click, and each numbered callout's label, text and target element |
| `shots/notes.mjs` | the prose under a section that is not tied to a numbered box |
| `images/README.md` | **generated** by `shots/index.mjs` from those two |
| `SLC-VMS-User-Manual.docx` | **generated** by `../build_manual.py` from `images/README.md` |
| `frontend/src/pages/Help/helpFigures.js` | **generated** by `shots/build_help_assets.py` |

Callouts point at real elements rather than fixed coordinates, so the boxes
follow the layout instead of drifting off it. A mark whose element has gone is
reported as `! <figure>: mark N "<label>" not found` and the figure is still
written — check the log, do not assume a clean run.

## Running it

A local PostgreSQL holds `slc_manual_demo`, a throwaway database of fictional
people and plates. `demo_settings.py` pins it and switches off R2, Redis, email
and Celery, so a capture cannot reach anything real whatever `backend/.env`
says. The role and password are reused from `backend/.env`; only the host
differs.

```powershell
. docs\user-manual\capture\demo-env.ps1        # or demo-env.sh from bash
& $py backend\manage.py migrate                # demo database follows the app's schema
& $py docs\user-manual\capture\seed_figures.py # top-ups the newer screens need
```

Then start the two servers and leave them up:

```powershell
cd backend;   & $py -m daphne -b 127.0.0.1 -p 8765 config.asgi:application
cd frontend;  $env:BACKEND_URL='http://127.0.0.1:8765'; npx vite --port 5199 --host 127.0.0.1
```

`shots/` resolves `playwright` through a junction to `frontend/node_modules`.
Recreate it if it is missing:

```powershell
New-Item -ItemType Junction -Path docs\user-manual\capture\shots\node_modules `
         -Target (Resolve-Path frontend\node_modules)
```

Capture, compose, and regenerate. Every command takes optional filters matching
part of a figure id, and with none it does all of them:

```bash
cd docs/user-manual/capture/shots
node capture.mjs 12-parking-spaces            # raw/<id>.png + raw/<id>.json
node compose.mjs ../../images 12-parking      # the annotated figure
CLEAN=1 node compose.mjs ../figures-clean 12-parking   # the in-app help version
node index.mjs ../../images                   # rewrite images/README.md
```

```powershell
& $py docs\user-manual\capture\shots\build_help_assets.py   # WebP + helpFigures.js
& $py docs\user-manual\build_manual.py                      # the .docx
```

`index.mjs` and `build_help_assets.py` read every figure, so run them after the
last compose rather than after each one.

## Things that will catch you out

* **Re-running `index.mjs` overwrites `images/README.md`.** Anything written
  there by hand is lost. Edit `shots.mjs` or `notes.mjs`.
* **`CLEAN=1` composes into `figures-clean/`, not `images/`.** The two are
  different renderings of the same capture: one with the header, numbered
  boxes and footer drawn in, one bare for the help page to annotate at render time.
* **Phone captures (`-mobile`) belong only to the help page.** `index.mjs`
  skips them; if one is composed into `images/` by accident, delete the PNG.
* **The camera feeds and the detector status are stubbed.** A real RTSP feed and
  a running detector are not available here, so `capture.mjs` answers the camera
  socket with a sample frame and, for the parking figures, answers
  `camera-status` as though the detector were up. Without that the figures would
  document this laptop rather than a campus install.
* **Two-factor can fail a login with a timeout.** The server refuses a replayed
  timestep and a previous run may have spent the current one. Re-run the figure.
* **`raw/` for the installer and launcher is kept in git.** Those are desktop
  captures of the real setup wizard, not web pages, and re-taking them means
  walking the installer again. The web ones are gitignored.
