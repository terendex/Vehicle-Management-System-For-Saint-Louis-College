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

## One-time setup

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

## Daily maintenance (schedule this — it is not automatic)

Two jobs are defined as Celery tasks on a beat schedule in `config/celery.py`,
so they only run when a Celery worker *and* a beat scheduler are running.
Neither is deployed, because both would be extra always-on containers:

- **`auto_manage_events`** — activates events dated today, archives past ones.
  Without it the events list silently stops rolling over.
- **`purge_old_records`** — deletes AccessLog/Violation rows past the retention
  window set in System Settings.

`python manage.py run_maintenance` runs both synchronously, with no broker and
no worker. Schedule it here rather than in the cloud: both jobs key off
`date.today()`, which reads the OS clock, and this machine runs on Manila time.
A UTC container would roll events over about eight hours early.

Register it once, as Administrator, from the repository root:

```powershell
$py   = "$PWD\backend\venv\Scripts\python.exe"
$args = "manage.py run_maintenance"
schtasks /Create /TN "SLC VMS Daily Maintenance" /SC DAILY /ST 00:05 `
  /TR "cmd /c cd /d `"$PWD\backend`" && `"$py`" $args" /RL HIGHEST /F
```

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

**Keep both halves on the same commit.** After any deploy:

```powershell
git pull
powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1
```

Campus code older than the database schema is the one combination that will
break in confusing ways.

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
