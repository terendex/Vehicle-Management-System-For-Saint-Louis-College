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

**2. Clone and install** (skip if the repo is already there):

```powershell
git clone https://github.com/terendex/Vehicle-Management-System-For-Saint-Louis-College.git
cd Vehicle-Management-System-For-Saint-Louis-College
python -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

**3. Configure.** Copy the template and fill in the marked values:

```powershell
copy backend\.env.campus.example backend\.env
notepad backend\.env
```

Three of them matter most:

- `SECRET_KEY` — must be **byte-identical** to Railway's. JWTs are signed with
  it, so a mismatch means a token issued by one half is rejected by the other.
- `DATABASE_URL` — the same Neon string. This is what makes the two halves one
  system rather than two.
- `ALLOWED_HOSTS` / `FRONTEND_URL` / `BACKEND_URL` — this machine's LAN IP. Give
  it a static IP or a DHCP reservation, or guards will chase a moving address.

**4. Start it:**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-campus.ps1
```

The script pings both cameras first and warns if either is unreachable, builds
the React bundle, collects static files, and serves on `0.0.0.0:8000`. It prints
the LAN URL for guards. Use `-SkipFrontend` on later runs to skip the rebuild.

**5. Allow the port through the firewall** (once, as Administrator):

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

Each half broadcasts WebSocket updates only to browsers connected to *it*. A
scan at the gate refreshes every campus screen instantly; a Railway user sees it
on their next refresh or navigation.

Making updates cross between the halves would mean sharing a Redis channel layer,
which requires exposing Railway's Redis over a paid TCP proxy. Set `REDIS_URL` on
both halves to the same instance if you ever want it — the code already supports
it and needs no change.

---

## Troubleshooting

**"NO ROUTE" warning at startup** — the machine is not on the camera network, or
the cameras are off. Live scanning will not work until `ping 192.168.137.83`
succeeds.

**Guards get "Bad Request (400)"** — the address they typed is not in
`ALLOWED_HOSTS`. Add the exact IP or hostname and restart.

**Guards cannot reach the machine at all** — firewall rule missing (step 5), or
the machine's IP changed. Check with `ipconfig`.

**Data looks different between halves** — they are on different commits, or one
is not using the shared `DATABASE_URL`. Confirm both `.env` files carry the same
Neon string.
