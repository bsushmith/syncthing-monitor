from typing import Union

import requests
import os

import subprocess
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.config/syncthing-monitor/.syncthing-monitor.env"))

# -------- Logging Setup --------
LOG_DIR = Path.home() / "Library/Logs/syncthing-monitor"
LOG_FILE = LOG_DIR / "monitor.log"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("syncthing_monitor")
logger.setLevel(logging.DEBUG)

file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=2
)
file_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# -------- Configuration --------
API_KEY = os.environ.get("SYNCTHING_API_KEY", "")
BASE_URL = "http://localhost:8384/rest"

SYNCTHING_PATH = "/Applications/Syncthing.app/Contents/Resources/syncthing/syncthing"


def call_syncthing_api(endpoint: str, params: dict = None, timeout: int = 10) -> Union[dict, None]:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    r = requests.get(f"{BASE_URL}/{endpoint}", headers=headers, params=params, timeout=timeout)

    if not r.ok:
        logger.error(f"Error fetching {endpoint}: HTTP {r.status_code}")
        return None

    data = r.json()
    return data


def get_syncthing_device_id() -> str:
    response = call_syncthing_api(endpoint="system/status", timeout=2)
    if not response:
        return ""
    else:
        return response["myID"]


def is_syncthing_running() -> Union[bool, None]:
    response = call_syncthing_api(endpoint="system/ping", params={}, timeout=10)
    return response is not None and response.get("ping") == "pong"


def start_syncthing() -> bool:
    logger.warning("Syncthing is not running. Starting it now...")

    proc = subprocess.Popen([SYNCTHING_PATH, "--no-browser"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not proc:
        logger.error("Failed to start Syncthing")
        return False

    time.sleep(5)
    logger.info("Syncthing started successfully.")
    return True


def get_devices() -> dict[str, dict]:
    response_json = call_syncthing_api(endpoint="config/devices", params={}, timeout=10)

    if not response_json:
        logger.error(f"Error fetching devices")
        return []

    devices = {}
    for device in response_json:
        device_id = device.get("deviceID")
        if device_id:
            devices[device_id] = device
    logger.debug(f"Fetched {len(devices)} devices.")
    logger.debug("devices: " + ", ".join([f"{dev['name']} ({dev_id})" for dev_id, dev in devices.items()]))
    return devices


def notify_mac(title: str, message: str) -> None:
    script = f'display notification "{message}" with title "{title}" subtitle "Subtitle" sound name "Ping"'
    subprocess.run(["osascript", "-e", script])
    logger.info(f"Notification sent - {title}: {message}")


def get_system_connection_status() -> Union[dict[str, bool], None]:
    response_json = call_syncthing_api(endpoint="system/connections", params={}, timeout=10)

    if not response_json:
        logger.error(f"Error fetching system connections")
        return None

    connections = response_json.get("connections", {})
    connection_status = {}
    for device_id, detail in connections.items():
        connection_status[device_id] = detail.get("connected", False)
    return connection_status


def check_system_errors() -> bool:
    sys_errors = call_syncthing_api("system/error")
    if sys_errors and sys_errors.get("errors"):
        alert_message = f"{sys_errors["errors"][0]["message"]} at {sys_errors["errors"][0]["when"]}"
        logger.error(f"System error: {alert_message}")
        notify_mac("Syncthing System Error", alert_message)
        return True
    return False


def check_folder_sync_errors(folder: dict, folder_errors_data: dict) -> list:
    issues = []
    f_id = folder['id']
    f_label = folder.get('label', f_id)

    if folder_errors_data and folder_errors_data.get("errors"):
        count = len(folder_errors_data["errors"])
        issues.append(f"Folder '{f_label}' has {count} out-of-sync files.")
        logger.warning(f"Folder '{f_label}' has {count} out-of-sync files.")

    return issues


def check_device_sync_status(folder, device_entry, device_details, my_device_id, system_connection_status):
    issues = []
    d_id = device_entry["deviceID"]
    d_name = device_details[d_id]["name"]
    f_id = folder['id']
    f_label = folder.get('label', f_id)

    completion = call_syncthing_api("db/completion", {"folder": f_id, "device": d_id})
    if completion:
        if d_id != my_device_id and not system_connection_status[d_id]:
            # for now, I don't care if a device is offline. As long as they are connected/online at some point, they will sync.
            # I only care about devices that are connected but not in-sync.
            pass
        elif completion.get("completion") < 100:
            needed = completion.get("needItems", 0)
            issue = f"Device {d_name} needs {needed} items in '{f_label}'"
            issues.append(issue)
            logger.warning(issue)
        elif completion.get("completion") == 100 and completion.get("needItems", 0) > 0:
            issue = f"Device {d_name} is showing 100% but still needs {completion.get('needItems', 0)} items in '{f_label}'"
            issues.append(issue)
            logger.warning(issue)

    return issues


def run_health_check():
    logger.info("Starting health check...")
    if not is_syncthing_running():
        if not start_syncthing():
            logger.error("aborting health check!")
            return

    if check_system_errors():
        return

    folders = call_syncthing_api("config/folders")
    if not folders:
        logger.error("No folders found in config!")
        return

    device_details = get_devices()
    my_device_id = get_syncthing_device_id()
    system_connection_status = get_system_connection_status()

    if not all([device_details, my_device_id, system_connection_status]):
        logger.error("Failed to get required system information!")
        return

    issues = []

    for folder in folders:
        f_id = folder['id']

        folder_errors = call_syncthing_api("folder/errors", {"folder": f_id})
        issues.extend(check_folder_sync_errors(folder, folder_errors))

        for dev_entry in folder.get("devices", []):
            device_issues = check_device_sync_status(
                folder, dev_entry, device_details, my_device_id,
                system_connection_status
            )
            issues.extend(device_issues)

    if issues:
        alert_msg = "\n".join(issues[:3])  # Show first 3 issues
        if len(issues) > 5: alert_msg += f"\n...and {len(issues) - 5} more."
        logger.error(f"Health check found {len(issues)} issues")
        notify_mac("Syncthing Sync Issues", alert_msg)
    else:
        logger.info("Health check passed - all folders and devices are in sync")


if __name__ == "__main__":
    run_health_check()
