# 🧩 Installation Guide — DeviceMonitor AppDaemon App

## 1. Requirements

You’ll need:

- **Home Assistant**
- **AppDaemon** (installed or via the official add-on)
- **InfluxDB** (already storing your device’s power data)

---

## 2. Install the Python dependency

The app requires the **InfluxDB Python client**.

### 🧩 If using the official AppDaemon Add-on in Home Assistant:

Open the AppDaemon Add-on settings.

Under System Packages, add `influxdb`

## 3. Copy the app file

Create the following folder structure (if it doesn’t already exist):

`/addon_configs/a0d7b954_appdaemon/apps/`

Then save your script as:

`/addon_configs/a0d7b954_appdaemon/apps/device_monitor.py`

## 4. Configure the app

Edit (or create) your AppDaemon configuration file:

`/addon_configs/a0d7b954_appdaemon/apps/apps.py`

Add the content of the `apps.py` file in there and set the variables to fit your needs.
