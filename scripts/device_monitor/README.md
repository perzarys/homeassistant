# 🧩 Installation Guide — DeviceMonitor AppDaemon App

## 1. Requirements

You’ll need:

- **Home Assistant OS**
- **File editor** (installed or via the official add-on)
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

Add the content of the `apps.py` file in there and 

set the variables to fit your needs.



## 📊 5. Logs & Debugging

Check the logs in Settings > Add-ons > AppDaemon > Logs.

Typical logs

```
INFO device_monitor: fridge_power: active=7.3m, median=11.2, limit=8.4/14.0, OK
INFO device_monitor: fridge_power: PENDING set: active_short (active too short: 5.8m < 8.4m)
INFO device_monitor: fridge_power: fired pending idle_short after buffer
```

You’ll see:

  - Active/inactive phase info
  
  - Current median values
  
  - Dynamic min/max thresholds
  
  - Alert or OK state
  
  - Pending and fired alerts


## 🧠 6. Example interpretation

If your fridge cycles every ~12 minutes (active) and rests ~35 minutes (inactive):

| Case             | Condition           | Description     |
| ---------------- | ------------------- | --------------- |
| Active too long  | > 12 × (1 + margin) | Immediate alert |
| Active too short | < 12 × (1 − margin) | Pending alert   |
| Idle too long    | > 35 × (1 + margin) | Immediate alert |
| Idle too short   | < 35 × (1 − margin) | Pending alert   |

This way, the monitor adapts to your device’s natural rhythm over time.
