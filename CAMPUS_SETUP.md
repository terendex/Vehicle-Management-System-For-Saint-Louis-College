# Campus setup — restoring live camera scanning

Railway runs the public app. It cannot open the cameras: they sit on the campus
LAN at `192.168.137.x`, and a cloud container has no route to a private address.

The fix needs no new code. Run the **same application** on a machine inside the
campus network, pointed at the **same Neon database** and the **same R2 bucket**.
Both halves then show identical data because there is only one database.

```
   Guards, at the gate                Everyone else, anywhere
   http://<campus-ip>:8000            https://<app>.up.railway.app
            │                                    │
            │  opens RTSP to 192.168.137.x       │  no camera access
            │                                    │
            └──────────► Neon Postgres ◄─────────┘
                         Cloudflare R2
                    (one database, one bucket)
```

The campus half runs on hardware you already own, so it adds no hosting cost.

---

## Install with the setup program

Build it once, on your own machine:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build.ps1
```

That fetches the Inno Setup compiler through winget the first time, regenerates
the icon and wizard artwork from `frontend\src\assets\slclogo.jpg`, and writes
**`installer\out\SLC-Smart-Parking-Campus-Setup.exe`** — about 2 MB.

Copy that one file to the campus machine and run it. The wizard asks for the
install location, the branch to track and the port, then hands off to a setup
window that:

- installs **Git**, **Python 3.11** and **Node.js LTS** through winget, skipping
  any that are already there;
- clones this repository into `<install>\app`;
- opens the port in Windows Firewall;
- leaves a Start Menu entry, an optional desktop shortcut, and an optional
  "start when this computer starts" shortcut.

It does *not* build the virtualenv or the React bundle. Those already happen on
the first run of `run-campus.ps1`, where the launcher streams them into a log
you can watch — twenty silent minutes behind an installer progress bar is how a
slow download becomes indistinguishable from a hang.

It does not ask for credentials either. The launcher has a panel for those and
opens it by itself on a machine that is not configured yet, so there is one
form in one place.

**The setup .exe carries no application code.** That is why it is 2 MB rather
than several hundred, and it is the whole update story: the installed half is a
git checkout, so `git pull` *is* the update. You reissue the installer only when
something in `installer\` changes — the bootstrap, the shortcut, the artwork —
never for an ordinary code change.

The download is about **182 MB** — a measured full clone of one branch, most of
it the two model weight files under `backend/scanning/ml/runs/`. Budget a few
minutes on campus wifi. (A long-lived local `.git` can be many times that from
unreachable objects on other branches; that is not what the server sends.)

> **The branch must already carry these files.** The installer clones from
> GitHub, so `scripts\campus-launcher.ps1` has to be pushed to the branch you
> tell it to track. If it is not, the setup window says so by name rather than
> failing vaguely — push, then use **Repair installation** from the Start Menu.

`{app}` is installed outside Program Files and granted Users:modify on purpose.
A checkout under Program Files can only be written by an elevated process, which
would mean a UAC prompt on every routine update at the gate.

**Keep the install path short.** Windows still caps most paths at 260
characters, and git reports hitting that as `fatal: '$GIT_DIR' too big` — an
error naming neither the path nor the limit. Both the wizard and the bootstrap
refuse an over-long folder up front for that reason. `C:\SLC-VMS` is the
default and is well clear of it.

---

## The launcher

The shortcut opens a window rather than a console. It is a shell around
`scripts\run-campus.ps1`, not a replacement for it — the server logic stays in
one place, and the window starts that script, reads its output, and turns the
lines it already prints into:

- **one state** — Stopped / Starting / Running, and the LAN URL, copyable, with
  buttons that open the two entry points guards actually need;
- **three health pills** — Database, Cameras, Realtime. All three are parsed
  from the server's own startup output, so nothing extra runs to produce them.
  `Cameras 3 up, 1 down` is the same NO ROUTE warning the console prints;
- **the whole log**, live, so a failure is visible instead of silent. It is also
  written to `%LOCALAPPDATA%\SLC-VMS\logs\campus-<date>.log`.

Closing the window stops the server. That is deliberate: a daphne left running
headless would hold the port and the RTSP sessions, and the next start would
fail with an address-already-in-use that has no visible cause.

### Opening the two logins

The launcher has a button for each, and they are genuinely different audiences:

- **Guard terminal** → `/security/guard-login`, the gate scanner
- **Admin login** → `/login`, the account login for CDSO and vehicle owners

Both open in **kiosk mode** by default: full screen, no address bar, no tabs.
That is the point on a gate terminal — a guard gets the scanner and nothing
else, with no address bar to mistype and no way to wander off into another tab.
**Alt+F4 closes it.** Turn kiosk off in Settings on a machine someone also does
paperwork on.

**Open automatically once the server is up** picks one of the two to launch by
itself when the server finishes starting — set it to *Guard terminal* on a box
that lives at a gate, and it comes up scanning after a power cut with nobody
touching it. It fires when daphne actually has the socket, not a second earlier,
so the guard never lands on a connection-refused page.

Kiosk needs **Chrome or Edge**; with neither installed the page opens in the
default browser and the log says so rather than pretending. Two details that
matter and are easy to get wrong:

- the kiosk runs in **its own browser profile** under
  `%LOCALAPPDATA%\SLC-VMS\browser`. Without one, a browser that is already
  running takes the request into its existing window and ignores kiosk
  entirely — and the guard's session stays separate from anything else this
  machine browses.
- on Edge, `--edge-kiosk-type=fullscreen` is set explicitly, because Edge's
  default kiosk is InPrivate and wipes the session on an idle timer — a guard
  who steps away would come back logged out mid-shift.

Closing the launcher closes the kiosk window with it. A full-screen browser with
no address bar, still pointing at a server that has stopped, is the worst thing
to leave on a gate terminal.

### Updates

The launcher fetches the tracked branch every few minutes. When commits have
landed it shows how many and the subject of the newest one, with an **Update and
restart** button. Pressing it stops the server, `git pull --ff-only`s, and starts
again — `run-campus.ps1` rebuilds the React bundle by itself if the sources
moved.

**It never pulls on its own.** This machine serves live gate scanning, and a
restart drops the camera feeds for the length of a rebuild. That is someone's
decision, not a surprise mid-shift.

Two things it refuses to do rather than guess:

- if the checkout has uncommitted changes, it says so and stops. Discarding
  someone's local edit to make an update succeed is never the right trade;
- it pulls `--ff-only`. A merge commit created on the campus box would exist
  nowhere else, and would make the *next* pull fail in a way nobody at a gate
  can fix.

If an update replaces `campus-launcher.ps1` itself, the window reopens itself —
PowerShell read the old file into memory at launch, so the change would
otherwise not take effect until someone happened to restart it.

Port, branch, kiosk and poll interval live in
`%LOCALAPPDATA%\SLC-VMS\launcher.json`, outside the checkout, so a pull or a
reinstall never resets them.

---

## Uninstalling

Add/Remove Programs, or **Uninstall Smart Parking and Vehicle Verification System** in the Start Menu.
Either way it closes a running launcher first — targeted by window title, so it
cannot take out an unrelated PowerShell session — because a running server locks
files under the checkout and the uninstall would otherwise fail halfway.

The firewall rule it created goes without asking. Leaving an inbound allow rule
behind for a port nothing serves is an open hole with no owner.

Then it asks **one** question, about the two things that are yours rather than
the installer's:

| | |
|---|---|
| `{app}\app` | the checkout — `backend\.env` with the shared Railway secret key and Neon URL, the virtualenv, and any model weights trained on this machine |
| `%LOCALAPPDATA%\SLC-VMS` | launcher settings, the kiosk browser profile, and the activity logs |

**Keep** leaves both, so reinstalling later needs no download and no
reconfiguration — that is the right answer for a version upgrade or a repair.
**Delete** removes them, including the saved secret key and database URL, and
cannot be undone.

Deleting the checkout never touches the shared database. This half owns no data
of its own; every record lives in Neon, which the Railway deployment is still
serving.

---

## One-time setup, by hand

The installer above does steps 2 and 3 for you. This is the same thing typed out
— useful on a machine where winget is unavailable, and the reference for what
the installer is actually doing.

**1. Pick the machine.** Any campus PC that can reach the cameras. If it has the
RTX 3060, detection runs on the GPU and is markedly faster.

**2. Clone and start.** There is no separate install or configure step — the
script does both:

```powershell
git clone https://github.com/terendex/Vehicle-Management-System-For-Saint-Louis-College.git
cd Vehicle-Management-System-For-Saint-Louis-College
powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1
```

On first run it creates `backend\venv`, installs the requirements, writes
`backend\.env` from the campus template, and prompts once for the values it
cannot derive. Copy each from Railway:

- `SECRET_KEY` — must be **byte-identical** to Railway's. JWTs are signed with
  it, so a mismatch means a token issued by one half is rejected by the other.
- `DATABASE_URL` — the same Neon string. This is what makes the two halves one
  system rather than two.
- the five `R2_*` values — the same bucket, so evidence photos uploaded by one
  half are served by the other.

`-Reconfigure` re-asks those questions later.

**You are not asked for the addresses.** `ALLOWED_HOSTS`, `FRONTEND_URL`,
`BACKEND_URL` and `CSRF_TRUSTED_ORIGINS` are derived from this machine's LAN
address on every run and passed through the environment, which python-dotenv
lets win over `.env`. A new DHCP lease therefore needs no edit — though a static
IP or a DHCP reservation is still worth having so guards are not chasing a
moving address in their browser history.

`RUN_MIGRATIONS=false` and `SECURE_SSL_REDIRECT=false` are forced the same way,
so this half can never migrate the shared schema and never redirect a plain-HTTP
LAN request into a loop.

The script then pings every camera registered in the database and warns about
any that are unreachable, builds the React bundle **only if the sources changed**
since the last build, collects static files, and serves on `0.0.0.0:8000`. It
prints the LAN URL for guards. `-SkipFrontend` skips the build check entirely;
`-Rebuild` forces one.

**3. Allow the port through the firewall** (once, as Administrator):

```powershell
New-NetFirewallRule -DisplayName "SLC VMS" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

---

## Daily maintenance

Three jobs are defined as Celery tasks on a beat schedule in `config/celery.py`,
so they only run when a Celery worker *and* a beat scheduler are running.
Neither is deployed, because both would be extra always-on containers:

- **`auto_archive_expired_accounts`** — archives owner accounts past their
  expiry date. **Runs by itself; no setup needed** (see below).
- **`purge_old_records`** — applies the retention window from System Settings:
  deletes AccessLog rows, Violation rows, and **archived accounts** older than
  it. **Runs by itself; no setup needed** (see below). Only archived accounts
  are ever deleted — a live account is never touched, and audit history survives.
- **`auto_manage_events`** — activates events dated today, archives past ones.
  Without it the events list silently stops rolling over. *This is the one that
  still needs the scheduled task below.*

### Archiving and retention run themselves

The server runs both jobs in-process: a daemon thread started with the ASGI app
checks hourly whether each has already run today, and runs it if not
(`backend/vehicles/scheduler.py`). This exists because the System Settings cards
promise expiry and retention happen automatically, and those promises should not
depend on someone remembering to register a Windows task.

They run archive-first, purge-second on the same pass. Archiving stamps
`archived_at` and the purge measures the retention window from it, so an account
archived today gets its full window before anything deletes it.

Because the thread asks *"has this run today?"* rather than *"is it 00:05 now?"*,
a machine that was switched off overnight catches up on its next boot. A
`tbl_daily_job_run` row per (job, day) is the lock, so restarts cannot double-run
a job and starting the server twice is harmless.

Set `DISABLE_DAILY_SCHEDULER=1` to turn the thread off — do that on any second
machine, and on the cloud instance if the campus box is the one you trust for
Manila-time dates. **Note this also stops the retention purge**, which is the
only thing that ever deletes archived accounts.

### Events rollover still needs scheduling

`python manage.py run_maintenance` runs all three synchronously, with no broker
and no worker, ignoring the daily ledger. Schedule it here rather than in the
cloud: the jobs key off `date.today()`, which reads the OS clock, and this
machine runs on Manila time. A UTC container would roll events over about eight
hours early.

Register it once, from the repository root:

```powershell
schtasks /Create /TN "SLC VMS Daily Maintenance" /SC DAILY /ST 00:05 `
  /TR "`"$PWD\scripts\run-maintenance.cmd`"" /F
```

`scripts\run-maintenance.cmd` is the wrapper the task runs. It resolves the repo
from its own location — so moving the checkout does not break the task — and
appends output to `backend\maintenance.log`, which is the only record you get of
a job that runs with no console attached.

Elevation is not required: the job only talks to the database. Adding `/RL
HIGHEST` needs an Administrator prompt and buys nothing here.

Verify and test it:

```powershell
schtasks /Query /TN "SLC VMS Daily Maintenance"
schtasks /Run   /TN "SLC VMS Daily Maintenance"
```

To try it without deleting anything, run it by hand with `--skip-purge`:

```powershell
backend\venv\Scripts\python.exe backend\manage.py run_maintenance --skip-purge
```

> Schedule this on **one** machine only. Both jobs are idempotent, so a second
> run is harmless, but running the purge from two places doubles the load on a
> shared database for no benefit.

---

## Rules for running two halves against one database

**Railway owns the schema.** `RUN_MIGRATIONS=false` in the campus `.env` is
deliberate. Two instances racing to migrate a shared database — possibly from
different commits — is how a shared database gets corrupted. Deploy schema
changes through Railway, then `git pull` on the campus machine.

**Keep both halves on the same commit.** After any deploy, press **Update and
restart** in the launcher — that is exactly what it is for, and it will already
be offering the update. Without the launcher:

```powershell
git pull
powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1
```

Campus code older than the database schema is the one combination that will
break in confusing ways.

Which branch the campus box tracks is a real decision, not a default. Tracking
your working branch means every push reaches the gate within minutes, which is
what you want while still building; tracking `main` means only merged work gets
there, which is what you want once guards depend on it.

**It is chosen once, in the installer, and cannot be changed afterwards.** The
launcher shows it as a read-only chip on the Updates card but offers no way to
edit it — a gate terminal repointed at an arbitrary branch by whoever happens to
be sitting at it is a way to put untested code in front of guards. To change it
on a machine that is already installed, reinstall over the top and pick the new
branch on the deployment options page; the checkout, credentials and settings
all survive an in-place upgrade.

---

## What runs where

| | Campus (`http://<ip>:8000`) | Railway (`https://…`) |
|---|---|---|
| Live camera scanning | **yes** | no — no LAN route |
| Parking zone monitoring | **yes** | no |
| Registration, approvals, violations, reports | yes | yes |
| QR scanning (phone camera) | yes | yes |
| Reachable off-campus | no | **yes** |
| Django admin (`/django-admin/`) | no — see below | **yes** |
| Hosting cost | none (your hardware) | Railway usage |

**Django admin does not work on the campus half.** With `DEBUG=false` Django
marks its session cookie `Secure`, and the LAN serves plain HTTP, so the browser
never sends it back and login silently fails. Use the Railway URL for Django
admin — it is the same database either way. (Enabling `DEBUG=true` would "fix"
this by disabling security protections on a machine holding live data. Don't.)

---

## Live updates

Data is always shared instantly — there is one database, so a write from either
half *is* the write. What is optional is whether a browser already sitting on a
page gets **told**.

**Default (no `REDIS_URL`):** each half notifies only the browsers connected to
it. A scan at the gate refreshes every campus screen instantly; a Railway user
sees it on their next refresh or navigation.

**Linked (same `REDIS_URL` on both halves):** a change on either half refreshes
open screens on *both*. The fan-out group is a single constant with no
per-instance scoping, so this needs no code change — only a Redis both machines
can reach:

- a managed Redis with a public TLS endpoint (`rediss://…`) works directly and
  avoids paying for a raw TCP proxy;
- Railway's own Redis plugin is internal-only, so sharing that one requires
  enabling its paid TCP proxy.

Set the identical value on both halves, then **prove it** rather than assuming:

```powershell
# on the campus machine
backend\venv\Scripts\python.exe backend\manage.py check_realtime --listen

# on Railway (or the other machine), while the above is waiting
python manage.py check_realtime --send
```

A received probe means the halves are linked. A timeout means they are not, no
matter what the config claims.

> **Why the explicit check.** `broadcast_change` swallows channel-layer errors
> by design — a failed notification must never break the database write that
> triggered it. So a wrong or unreachable `REDIS_URL` does not raise anything:
> screens just quietly stop refreshing. `check_realtime` with no arguments
> reports which mode is active and round-trips a message locally; the server
> also prints the mode at startup (`[settings] realtime: …`).
>
> The trap: `REDIS_URL` falls back to `CELERY_BROKER_URL`, which commonly still
> holds a dev-time `redis://127.0.0.1`. A loopback address cannot link two
> machines, and if nothing is listening there, live updates die silently on that
> half. Both the startup line and `check_realtime` call this out explicitly.

---

## Troubleshooting

**"NO ROUTE" warning at startup** — the machine is not on the camera network,
or the cameras are off. The warning names the camera and its address, read from
the database, so `ping <that address>` is the next step. Everything except live
camera scanning still works meanwhile.

**"could not read the camera list"** — the database is unreachable from here.
Check `DATABASE_URL`; the same failure would stop the app itself from starting.

**Guards get "Bad Request (400)"** — they typed an address the server does not
answer to. The script puts this machine's detected LAN address in
`ALLOWED_HOSTS` automatically, so this usually means they used an old address
after the IP changed: use the one the script prints at startup.

**Guards cannot reach the machine at all** — firewall rule missing (step 5), or
the machine's IP changed. Check with `ipconfig`.

**Data looks different between halves** — they are on different commits, or one
is not using the shared `DATABASE_URL`. Confirm both `.env` files carry the same
Neon string.
