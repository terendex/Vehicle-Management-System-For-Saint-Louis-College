# Instructor demo: the time-based rules on a simulated clock

A local copy of the system whose date you can move, to show the time-based rules
working on the real code: the 3 working day payment window, registration closing,
and passes ending on July 31. Nothing here touches the live database.

## Start it

Double-click **`SLC VMS (Simulated Clock).cmd`** in the project folder, or run:

```powershell
.\dev.ps1 -SimClock -SimSetup          # first time, or any time (keeps an existing demo)
.\dev.ps1 -SimClock -SimSetup -Reset   # start the demo over from scratch
```

The first run copies the fictional demo campus (`slc_manual_demo`) into its own
database, `slc_sim_demo`, opens a registration period for the current school
year, and sets the clock to the real date. The browser opens
<http://127.0.0.1:5174> by itself once the demo is up. The demo has its own
ports (8765 and 5174), so it runs beside the campus app on 8000 without
touching it; if a demo port is taken, the launcher stops and says why.

**A copy of the live system instead:** `.\dev.ps1 -SimClock -SimSetup -FromLive`
copies the live database and uploaded files (read-only on the live side) into
the demo, and you log in with your own live account. It holds real people's
data, so it stays on this PC: its emails only reach `SIM_EMAIL_TO`, it writes
no backups, and it leaves the real cameras alone unless `SIM_CAMERAS=1`. Go
back to the fictional campus with `.\dev.ps1 -SimClock -SimSetup -Reset`.

| | |
|---|---|
| Admin login | `cdso.demo@slc-sflu.edu.ph` / `Demo@2026!` |
| 2FA code | authenticator key `JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP`, or `python manage.py sim_clock code` in the backend window |
| Emails | set `SIM_EMAIL_TO=you@example.com` in `backend/.env` to receive them; otherwise they are printed in the backend window. Every email goes to that one inbox, never to the address on the record. |

## Move the date

Admin sidebar → **System → Test Clock**:

* **Enable simulated date**, then pick a date and time, or press **+1 hour**,
  **+1 day**, **+1 working day**, **+1 week**.
* **Run Time Based Jobs Now** does at once what the scheduler does by itself
  within the hour: expires overdue applications, sends payment reminders,
  archives expired accounts.
* **Back to the real date** resets it.

From a terminal (backend window): `python manage.py sim_clock status | set 2026-10-09 10:00 | advance 2wd | reset | run-jobs`.

**How you know it is on:** a red **SIMULATED DATE** bar on every page and
**[SIM]** in the browser tab while the date is moved. No bar means the real date.
The live system can never show it; the clock refuses to start against any
database that is not on this PC.

## Script: the 3 working day rule

1. Test Clock: set **Friday, 3:00 PM**.
2. In another browser (or private window), open `/register`, choose Student,
   answer **Yes, I drive**, and submit. The confirmation says the deadline is
   **Wednesday 3:00 PM** (the weekend is not counted).
3. Press **+1 working day** twice (Tuesday 3:00 PM), then **Run Time Based Jobs
   Now**: still **Pending**, and the **payment reminder** email arrives.
4. Set **Wednesday 5:00 PM**, run the jobs: the application is **Expired** and
   the **expiry** email arrives. Vehicle Registration shows it under Expired.

## Script: registration closing

1. Rule Constraints → Registration Period → New Period: choose a school year.
   The dates fill in as August 1 to July 31 and can be moved only inside it;
   a date outside is refused. A school year that already has a period is
   greyed out.
2. Test Clock: set **Thursday, July 29** of the school year's second year and
   submit an application. Its deadline is the close, **July 31**, not three
   working days later.
3. Set **August 1**, run the jobs: the unpaid application expires with
   "registration closed"; a paid one stays Pending for the CDSO.

## Script: the school year ends

Set the date to **August 1** of next year and run the jobs: owner accounts
accepted this school year (valid until July 31) are archived and emailed.
