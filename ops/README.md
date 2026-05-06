# Zernio operations

## Run the system end-to-end

There are TWO long-running processes you need running for the auto-flow:

```bash
# Terminal 1 — capture daemon (watches recordings/, listens for F8, cuts clips)
python -m zerino.capture.main

# Terminal 2 — scheduler daemon (dispatches due posts to Zernio)
python -m zerino.publishing.batch.scheduler_runner
```

Or install the scheduler as a launchd / Task Scheduler service so it stays
alive across reboots — see below. The capture daemon you typically only
run while you're streaming.

### One-time setup before your first stream
```bash
python -m zerino.db.migrate                                              # init DB
python -m zerino.cli.add_account add --platform twitter \                 # register account
       --handle @yourhandle --zernio-account-id <24-char-id>
python -m zerino.cli.captions add "Wait for it 👀" --hashtags "#cod"     # seed pool
python -m zerino.cli.captions add "Banger play 🔥" --hashtags "#warzone" --weight 3
# ... add 10–20 captions
```

### Live workflow
1. Start `zerino.capture.main` in a terminal (leave it running).
2. Start `zerino.publishing.batch.scheduler_runner` in another terminal
   (or have it launchd-managed — see below).
3. Start your OBS recording (writes to `recordings/`).
4. Press **F8** every time you want a clip-cut point.
5. Stop the OBS recording.
6. Watchdog detects the file stops growing → clip worker cuts every clip
   → captions pool feeds each one a random caption → first clip posts
   immediately, the rest are scheduled +120 min apart.
7. Scheduler dispatches them on time.

---

## Auto-start the batch scheduler (macOS)

The scheduler picks up due posts and dispatches them to Zernio every 5 seconds.
You want it running 24/7 so scheduled posts (the +120 min ones from the
auto-flow) actually fire even if you close the terminal or your Mac restarts.

### Install
```bash
cp ops/com.zerino.scheduler.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.zerino.scheduler.plist
```

### Verify
```bash
# Should show "com.zerino.scheduler" with a PID and exit code 0
launchctl list | grep zerino

# Live log
tail -f logs/zerino.log
# or scheduler-specific stdout/stderr
tail -f logs/scheduler.stdout.log logs/scheduler.stderr.log
```

### Stop / restart / uninstall
```bash
# Stop (keeps the plist in place)
launchctl unload ~/Library/LaunchAgents/com.zerino.scheduler.plist

# Restart
launchctl unload ~/Library/LaunchAgents/com.zerino.scheduler.plist
launchctl load   ~/Library/LaunchAgents/com.zerino.scheduler.plist

# Permanently remove
launchctl unload ~/Library/LaunchAgents/com.zerino.scheduler.plist
rm ~/Library/LaunchAgents/com.zerino.scheduler.plist
```

`KeepAlive=true` means launchd will restart the process if it crashes.
`RunAtLoad=true` + the plist living in `~/Library/LaunchAgents/` means it
auto-starts when you log in after a reboot.

---

## Auto-start on Windows (equivalent of launchd)

Use Task Scheduler:

1. Open **Task Scheduler** → **Create Task** (not the basic wizard).
2. **General** tab: name `Zerino Scheduler`, check "Run whether user is
   logged on or not", check "Run with highest privileges".
3. **Triggers** tab: New trigger → "At log on" of your user.
4. **Actions** tab: New action →
   - Program/script: `C:\Path\to\Content_Business\venv\Scripts\python.exe`
   - Add arguments: `-m zerino.publishing.batch.scheduler_runner`
   - Start in: `C:\Path\to\Content_Business`
5. **Settings** tab:
   - Check "If the task fails, restart every: 1 minute"
   - "Attempt to restart up to: 99 times"
   - Uncheck "Stop the task if it runs longer than" (we want it to run forever).

That's the Windows equivalent of `KeepAlive=true` + `RunAtLoad=true`.
