# DeviceMonitor AppDaemon App – Installation & Configuration Guide

Monitor device power usage patterns to detect abnormal behavior with automatic Home Assistant alerts.

---

## Overview

**DeviceMonitor** continuously analyzes power sensor data from InfluxDB to detect unusual device cycles. It identifies:

- **Active phases** → Power > threshold
- **Inactive phases** → Power ≤ threshold

Then calculates statistical baselines (mean & median) and triggers alerts when cycles deviate from normal patterns.

### What It Detects

| Alert Type | Condition | Example |
|-----------|-----------|---------|
| **Active Too Long** | Current active duration exceeds baseline × (1 + margin) | Fridge running continuously instead of cycling |
| **Active Too Short** | Current active duration below baseline × (1 − margin) | Device stops working mid-cycle |
| **Idle Too Long** | Current idle duration exceeds baseline × (1 + margin) | Device stuck in off state |
| **Idle Too Short** | Current idle duration below baseline × (1 − margin) | Device cycles too frequently |

---

## Prerequisites

- **Home Assistant OS** with admin access
- **AppDaemon add-on** (v4.0+)
- **InfluxDB** with active power sensor data
- **File editor add-on** (optional, for easy file management)
- Network connectivity between Home Assistant and InfluxDB

---

## Installation Steps

### Step 1: Install Python Dependencies

**Via AppDaemon Add-on UI:**

1. Go to **Settings** → **Add-ons** → **AppDaemon**
2. Click **Configuration** tab
3. Under **System Packages**, add:
   ```
   influxdb
   ```
4. Click **Save** → **Restart**

---

### Step 2: Create App Directory

Using **File editor** or SSH, create:

```
/addon_configs/a0d7b954_appdaemon/apps/
```

*(If the folder exists, skip this step)*

---

### Step 3: Copy App File

Save the provided script as:

```
/addon_configs/a0d7b954_appdaemon/apps/device_monitor.py
```

---

### Step 4: Configure AppDaemon

Edit or create:

```
/addon_configs/a0d7b954_appdaemon/apps/apps.yaml
```

### Minimal Configuration

```yaml
device_monitor:
  module: device_monitor
  class: DeviceMonitor
  
  # REQUIRED: Entity & Thresholds
  entity: sensor.fridge_power              # Power sensor entity ID
  threshold_watt: 5.0                      # Activity threshold in watts
  minimum_interval_minutes: 2              # Minimum duration to count as a cycle
  
  # REQUIRED: Timing
  check_interval_seconds: 30               # How often to check (30-60 recommended)
  
  # REQUIRED: InfluxDB Connection
  influx_host: localhost
  influx_port: 8086
  influx_db: homeassistant
  influx_user: homeassistant
  influx_password: your_secure_password
  
  # REQUIRED: Notifications
  notify_service: notify.mobile_app_iphone # Get from Settings > Devices & Services > Notifications
  
  # OPTIONAL: Alert Thresholds (choose ONE)
  margin_percent: 20                       # Deviation threshold as % (e.g., 20%)
  # OR
  # margin_minutes: 5                      # Deviation threshold in minutes
  
  # OPTIONAL: Analysis
  statistic_method: median                 # 'median' (default) or 'mean'
  history_window_hours: 24                 # Data lookback period
  
  # OPTIONAL: Behavior
  alert_cooldown_minutes: 5                # Prevent alert spam
  influx_measurement_var: device_cycles    # InfluxDB measurement name for cycle data
  send_test_notification: true             # Test notification on startup
  debug_logging: false                     # Verbose logging
```

---

## Configuration Reference

### Core Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `entity` | string | ✅ | — | Home Assistant power sensor entity ID |
| `threshold_watt` | float | ✅ | — | Power threshold to consider device "active" (in watts) |
| `minimum_interval_minutes` | float | ✅ | — | Minimum duration (minutes) for a cycle to count (prevents noise) |
| `check_interval_seconds` | int | ✅ | — | Polling interval in seconds (30–60 recommended) |

### InfluxDB Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `influx_host` | string | ✅ | — | InfluxDB server hostname or IP |
| `influx_port` | int | ✅ | — | InfluxDB port (usually 8086) |
| `influx_db` | string | ✅ | — | InfluxDB database name (e.g., `homeassistant`) |
| `influx_user` | string | ✅ | — | InfluxDB username |
| `influx_password` | string | ✅ | — | InfluxDB password |
| `influx_measurement_var` | string | ❌ | `device_cycles` | Measurement name for storing cycle statistics |

### Alert Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `margin_percent` | float | ❌* | 0 | Deviation threshold as percentage (e.g., 20 = ±20%) |
| `margin_minutes` | float | ❌* | 0 | Deviation threshold in minutes (overrides %) |
| `notify_service` | string | ❌ | — | Home Assistant notification service (e.g., `notify.mobile_app_iphone`) |
| `alert_cooldown_minutes` | float | ❌ | 5 | Minimum time between duplicate alerts |

*Choose **one** margin type. If both set, `margin_minutes` takes precedence.

### Analysis Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `statistic_method` | string | `median` | Statistical method: `median` (robust) or `mean` (average) |
| `history_window_hours` | int | 24 | Hours of historical data to analyze |

### Debug Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `debug_logging` | bool | `false` | Enable detailed logging |
| `send_test_notification` | bool | `false` | Send notification on startup |

---

## Finding Your Entity & Service IDs

### Power Sensor Entity

1. Go to **Settings** → **Devices & Services** → **Entities**
2. Search for your device (e.g., "fridge")
3. Look for the power entity (e.g., `sensor.fridge_power`)
4. Copy the entity ID

### Notify Service

1. Go to **Settings** → **Devices & Services** → **Services**
2. Search for "notify"
3. Find your device service (e.g., `notify.mobile_app_iphone`)
4. Copy the service name

---

## InfluxDB Setup

### Create Database

```bash
# Via InfluxDB CLI or UI
CREATE DATABASE homeassistant
```

### Create User

```bash
CREATE USER homeassistant WITH PASSWORD 'your_secure_password'
GRANT ALL ON homeassistant TO homeassistant
```

---

## Restart AppDaemon

After configuration:

1. Go to **Settings** → **Add-ons** → **AppDaemon**
2. Click **Restart**
3. Wait ~30 seconds for startup

---

## Monitoring & Logs

### View Logs

**Settings** → **Add-ons** → **AppDaemon** → **Logs**

### Expected Log Examples

**✅ Normal operation:**
```
INFO device_monitor: fridge_power: active=7.3m, median=11.2m, limits=[8.4, 14.0], OK
INFO device_monitor: DeviceMonitor initialized for fridge_power | threshold=5.0W | min_interval=2m
```

**⚠️ Pending alert:**
```
WARNING device_monitor: PENDING: active_short (active too short: 5.8m < 8.4m)
WARNING device_monitor: Fired pending alert after buffer period
```

**🚨 Alert triggered:**
```
ERROR device_monitor: ALERT: active too long: 25.3m > 14.0m
```

### Log Interpretation

| Log Element | Meaning |
|-------------|---------|
| `active=7.3m` | Device has been active for 7.3 minutes |
| `median=11.2m` | Historical median active duration is 11.2 minutes |
| `limits=[8.4, 14.0]` | Alert triggers if duration < 8.4m or > 14.0m (margin applied) |
| `OK` | No alert conditions met |

---

## Example Scenarios

### Scenario 1: Refrigerator

```yaml
device_monitor:
  entity: sensor.fridge_power
  threshold_watt: 10.0         # Compressor draws 10W+
  minimum_interval_minutes: 1  # 1-min cycles minimum
  margin_percent: 20           # ±20% deviation
  statistic_method: median
  check_interval_seconds: 30
  history_window_hours: 48     # Use 2 days of data
```

**Expected behavior:**
- Detects 10–15 minute active cycles (compressor running)
- Detects 30–45 minute inactive periods (compressor off)
- Alerts if compressor runs >18m or idles >54m continuously

---

### Scenario 2: Sump Pump

```yaml
device_monitor:
  entity: sensor.pump_power
  threshold_watt: 50.0
  minimum_interval_minutes: 2
  margin_minutes: 3            # ±3 minutes deviation
  statistic_method: mean
  check_interval_seconds: 60
  history_window_hours: 24
```

**Expected behavior:**
- Detects 5–10 minute pumping cycles
- Alerts if active > 13m or idle > 13m (mean ± 3m)

---

## Troubleshooting

### No Alerts Sent

**Check:**
1. Is `notify_service` configured correctly?
2. Can you manually send a test notification? (Developer Tools → Services → `notify.your_service`)
3. Check AppDaemon logs for errors

### No InfluxDB Data

**Check:**
1. Is InfluxDB running and accessible?
2. Try `telnet localhost 8086` to verify connectivity
3. Verify credentials: `influx_user`, `influx_password`, `influx_db`
4. Ensure Home Assistant is writing to InfluxDB (check its config & logs)

### App Crashes or Won't Start

**Check:**
1. Is `influxdb` Python package installed? (Restart AppDaemon after adding)
2. Are all required parameters set in `apps.yaml`?
3. Review AppDaemon logs for Python exceptions

### Wrong Alert Thresholds

**Solution:**
- Enable `debug_logging: true` to see median/mean values
- Adjust `margin_percent` or `margin_minutes` accordingly
- Use `history_window_hours` to include more/less historical data

---

## Advanced: Writing Data to InfluxDB for Grafana

The app automatically writes cycle statistics to InfluxDB in this format:

```json
{
  "measurement": "device_cycles",
  "tags": {"entity": "sensor.fridge_power"},
  "fields": {
    "phase": "active",
    "mean_active_minutes": 11.2,
    "median_active_minutes": 10.8,
    "mean_inactive_minutes": 38.5,
    "median_inactive_minutes": 40.2,
    "alert_state": "ok",
    "alert_kind": "none"
  }
}
```

### Grafana Dashboard Example

Create a panel to visualize cycle trends:

```sql
SELECT median("median_active_minutes") FROM "device_cycles" 
  WHERE entity='sensor.fridge_power' AND time > now() - 7d 
  GROUP BY time(1h)
```

---

## Performance Notes

- **Check interval:** 30–60 seconds recommended (lower = more responsive, higher = lower CPU)
- **History window:** 24–48 hours recommended (more data = more robust baseline)
- **Polling overhead:** Minimal (~0.5% CPU per monitored device)

---

## Support & Logs

For debugging:
1. Enable `debug_logging: true`
2. Capture 10+ minutes of logs
3. Include your `apps.yaml` config (sanitized passwords)
4. Share any error tracebacks

---

**Last Updated:** 30.11.2025  
**Version:** 1.0
