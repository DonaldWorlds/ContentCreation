# Zernio operations

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
