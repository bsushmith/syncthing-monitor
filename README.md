# syncthing-monitor
 
Python script to monitor Syncthing sync status and send macOS notifications when issues are detected

### Setup:
This script expects syncthing to be running on the system. It's designed for my personal use on macOS, but it should work on other platforms with minor adjustments.

* Install `terminal-notifier` for macOS notifications.
* Create API KEY from syncthing web interface and add it to the file `~/.config/syncthing-monitor/.syncthing-monitor.env`
File contents should be like this:
```
SYNCTHING_API_KEY=syncthing_api_key_here
```

* Setup a crontab entry to run the script on a regular frequency.
* Logs are stored in `~/Library/Logs/syncthing-monitor` with the filename pattern `monitor.log*`