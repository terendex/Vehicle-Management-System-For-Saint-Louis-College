# Campus setup on Linux (Ubuntu 24.04)

The campus server can run on an Ubuntu 24.04 PC instead of Windows. It is the
same application doing the same job (see [CAMPUS_SETUP.md](CAMPUS_SETUP.md) for
why the campus half exists): it reads the gate cameras, prints visitor slips and
serves the gate terminals. It points at the same Neon database and R2 bucket as
Railway, so both halves show the same data.

On Linux there is no setup program or launcher window. Their jobs are done by:

| Windows | Linux |
|---|---|
| `SLC-Smart-Parking-Campus-Setup.exe` | `sudo scripts/linux/install.sh` (once) |
| Launcher window, "Start with Windows" | the `slc-vms` systemd service (starts on boot, restarts after a crash) |
| Launcher log pane | `journalctl -u slc-vms -f` |
| "Update & restart" button | `scripts/linux/update.sh` |
| Launcher opening the kiosk | an autostart entry that opens the gate page at desktop login |
| `run-campus.ps1` | `scripts/linux/run-campus.sh` |
| `slip-printer-port.ps1` | `sudo scripts/linux/slip-printer-setup.sh` |
| `run-maintenance.cmd` + Task Scheduler | `scripts/linux/run-maintenance.sh` + cron (optional) |

---

## What you need

- **The PC:** Ubuntu 24.04 LTS Desktop, on the campus network where it can reach the cameras. An NVIDIA card is optional; detection uses it when present.
- **An account for the server:** the normal desktop account is fine. On a dedicated gate PC, set that account to log in automatically (Settings, Users, Automatic Login). The kiosk then opens with nobody at the keyboard.
- **Google Chrome**, for the kiosk and the webcam QR scanner. Install it from google.com/chrome; the `.deb` is better than the Chromium snap. Chromium also works (`sudo snap install chromium`).
- **The secret values from Railway:** `SECRET_KEY`, `DATABASE_URL` and the five `R2_*` values. They are the same ones the Windows setup asks for.

## Install

```bash
sudo apt install -y git
git clone <the repository URL> ~/slc-vms
cd ~/slc-vms
sudo scripts/linux/install.sh --open guard
```

Use `--open guard` on a gate terminal, `--open admin` on an office PC, or
`--open none` if nothing should open by itself.

The script does everything in one go, and it is safe to run again:

1. **System packages:** installs Python 3.12, ffmpeg, CUPS and the libraries the
   detectors need, plus Node.js 22 from NodeSource (Ubuntu's own Node is too old).
2. **Printer groups:** adds the account to the `lp` and `lpadmin` groups for the
   slip printer.
3. **Settings file:** saves this PC's settings to `~/.config/slc-vms/campus.conf`.
4. **Firewall:** if `ufw` is on, opens ports 8000 and 8443.
5. **First start:** this runs in your terminal. It asks for the secret values
   (press Enter to keep a value that is already set), then builds the Python
   environment and the web pages. **This takes a long time the first time**:
   several GB are downloaded. Keep the PC online.
6. **Slip printer:** sets it up if one is plugged in (see [Slip printer](#slip-printer)).
7. **Service:** installs and starts `slc-vms`, which starts the server on every boot.
8. **Kiosk:** adds the kiosk autostart entry.

Log out and back in once afterwards, so the new printer groups apply to the
desktop session.

### Options

| Option | Default | Meaning |
|---|---|---|
| `--user NAME` | whoever ran `sudo` | the account the server runs as |
| `--port N` | 8000 | the web port |
| `--tls-port N` | 8443 | the HTTPS port for webcams on other devices (`0` turns it off) |
| `--branch NAME` | main | the branch `update.sh` follows |
| `--open guard\|admin\|none` | none | the page to open at desktop login |
| `--no-kiosk` | | open that page in a normal window instead of full screen |
| `--kiosk` | | full screen again, after an earlier `--no-kiosk` |
| `--skip-packages` | | skip the `apt` step (for a PC set up by hand) |

To change a setting later, edit `~/.config/slc-vms/campus.conf`, then run
`sudo systemctl restart slc-vms`.

## Day to day

| To | Run |
|---|---|
| See whether it is running | `systemctl status slc-vms` |
| Watch the log | `journalctl -u slc-vms -f` |
| Restart it | `sudo systemctl restart slc-vms` |
| Stop it | `sudo systemctl stop slc-vms` |
| Update to the latest version | `scripts/linux/update.sh` |
| Just check for updates | `scripts/linux/update.sh --check` |
| Change the secret values | `scripts/linux/run-campus.sh --reconfigure --setup-only`, then `sudo systemctl restart slc-vms` |
| Open the gate page by hand | `scripts/linux/open-kiosk.sh guard` |

**Updates:** `update.sh` pulls the new version and restarts the service. On that
restart the server reinstalls Python and Node packages, and rebuilds the web
pages, only when the update changed them. A restart that does have something to
install takes a few minutes; follow it in the log.

**The log** shows the same lines the Windows launcher shows: the address it is
"Serving on", one line per camera ("reachable :" or "NO ROUTE  :"), and the slip
printer it found.

## Webcam QR scanning on other devices

Browsers only let a page use the camera over `https://` (or on the PC itself).
The kiosk on the server PC is set up to allow the camera. Any **other** device,
such as another gate PC or a phone, should open:

```
https://<server IP>:8443/security/guard-login
```

The certificate is self-signed, so the first visit shows a warning: choose
**Advanced**, then **Proceed**. This is needed once per browser. The certificate
is issued for the PC's current address and is reissued by itself when the
address changes. It lives in `~/.local/share/slc-vms/tls/`.

## Slip printer

Visitor and no-plate slips print straight from the server to the thermal
printer (POS58 / JP-58H), with no print dialog. On Linux they go through CUPS, as
a "raw" queue: the server sends finished printer commands, so no driver changes
the layout.

`install.sh` sets the printer up if it is plugged in at the time. To set it up
later, or after swapping printers:

```bash
sudo scripts/linux/slip-printer-setup.sh
sudo systemctl restart slc-vms
```

It finds the printer on USB, creates a `POS58` queue and writes
`SLIP_PRINTER=POS58` into `backend/.env`. If it finds a USB printer but none that
looks like a thermal one, it lists them; run it again with
`--uri <one of them>`. CUPS warns that "raw queues are deprecated"; they still
work.

If a slip does not come out, the guard sees why in the error message: the
printer is offline or out of paper, it is not connected, or CUPS is stopped. A
queue that CUPS stopped after a paper jam is restarted by the server on the
next slip, and again whenever the service starts.

`SLIP_PRINTER` in `backend/.env` can also be set by hand:

| Value | Meaning |
|---|---|
| `POS58` | print to this CUPS queue |
| `/dev/usb/lp0` | write straight to the printer's device file, without CUPS (if it is unplugged, the guard is told so) |
| `off` | never print from the server; slips use the browser's print dialog |
| (not set) | use the CUPS queue whose name or USB address looks like a thermal printer |

## Differences from Windows

- **Backup folder Browse button:** on Windows, at the server PC it opens the
  Windows folder window. On Linux it always lists the server's folders inside
  the page. Typing a path works the same either way. Pick a folder the server's
  account can write to, such as `/home/gate/SLC Backups`; `/media` and `/mnt`
  themselves belong to root. Set the folder from the campus system: the cloud
  site refuses to save one, because it cannot check a folder on the campus PC.
- **Backups to a USB drive:** use the path Ubuntu mounts the drive at when it is
  plugged in, such as `/media/gate/USBDRIVE/Backups`. If the drive is unplugged
  at backup time, that path cannot be created, so the backup fails and is
  retried. Do **not** use a folder under a mount point you created yourself (for
  example `/mnt/usb`). If the drive is not mounted, that folder is just an empty
  folder on the main disk, so the backup would quietly land there instead.
- **PDF letterhead fonts:** the letterhead uses Windows fonts. "Saint Louis
  College" is still drawn in its Old English lettering. Without the other fonts,
  the address line and the tagline fall back to Times. To get the exact faces,
  copy `GOTHIC.TTF`, `BOOKOS.TTF` and `BOOKOSI.TTF` from a Windows PC's
  `C:\Windows\Fonts` into `backend/report_assets/`.
- **Slip font:** slips use Liberation Mono, which has the same letter widths as
  the Courier New the Windows slips use, so the layout matches.

## Optional: maintenance backstop

The server runs its daily jobs itself (event rollover, account archiving,
retention). These are the same jobs `run-maintenance.cmd` runs on Windows. If
the PC is often switched off at the time they would run, add a cron job as a
backstop (`crontab -e`):

```
30 6 * * * /bin/bash "/home/<user>/slc-vms/scripts/linux/run-maintenance.sh"
```

Output goes to `backend/maintenance.log`.

## Troubleshooting

| Symptom | Check |
|---|---|
| The service keeps restarting | Read the error with `journalctl -u slc-vms -n 50`. "Not configured: … missing" means a secret is missing: run `run-campus.sh` once in a terminal as described under [Day to day](#day-to-day). |
| Camera feeds stay black | Look for "NO ROUTE" lines in the log, and check that `ffmpeg -version` works. |
| Detection is slow on a PC with an NVIDIA card | Check that `nvidia-smi` works. The log says whether torch can use the card. |
| Other devices cannot open the HTTPS page | Run `sudo ufw status` to check port 8443 is allowed, and look for "HTTPS is off" in the log. |
| The kiosk does not open | Check that Chrome or Chromium is installed and `OPEN_ON_START` in `campus.conf` is `guard` or `admin`. Try `scripts/linux/open-kiosk.sh guard`. |
