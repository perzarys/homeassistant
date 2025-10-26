import appdaemon.plugins.hass.hassapi as hass
from influxdb import InfluxDBClient
from datetime import datetime, timezone
import statistics


def tparse(ts: str):
    """Parse InfluxDB ISO8601 timestamp into timezone-aware datetime."""
    if not ts:
        return None
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class DeviceMonitor(hass.Hass):
    """Monitors a power entity, detects activity cycles, and triggers alerts or logs to InfluxDB."""

    def initialize(self):
        """Initialize configuration, connect to InfluxDB, and start the periodic task."""
        args = self.args
        self.entity = args["entity"]
        self.threshold_watt = float(args["threshold_watt"])
        self.margin_percent = float(args.get("margin_percent", 0))
        self.margin_minutes = float(args.get("margin_minutes", 0))
        self.min_interval = float(args["minimum_interval_minutes"])
        self.check_interval = int(args["check_interval_seconds"])
        self.notify_service = args.get("notify_service")
        self.measurement = args["influx_measurement_var"]
        self.history_window_hours = int(args.get("history_window_hours", 24))

        self.client = InfluxDBClient(
            args["influx_host"],
            int(args["influx_port"]),
            args["influx_user"],
            args["influx_password"],
            args["influx_db"]
        )

        self.run_every(self.tick, self.datetime(), self.check_interval)
        self.log(
            f"DeviceMonitor started for {self.entity} | "
            f"threshold={self.threshold_watt}W | "
            f"min_interval={self.min_interval}m | "
            f"margin={(str(self.margin_minutes) + 'm') if self.margin_minutes else (str(self.margin_percent) + '%')} | "
            f"history={self.history_window_hours}h | "
            f"interval={self.check_interval}s"
        )

        # Internal state tracking
        self.pend_active_reason = self.pend_idle_reason = self.prev_phase = ""
        self.pend_active_since = self.pend_idle_since = self.prev_kind = ""
        self.alert_state = "ok"
        self.alert_kind = ""
        self.alert_timestamp = ""

    # ---------------- InfluxDB and notify helpers ----------------
    def _fetch_recent_points(self):
        """Fetch recent power samples from InfluxDB for the configured entity."""
        try:
            query = (
                f"SELECT value, time FROM W "
                f"WHERE entity_id='{self.entity}' "
                f"AND time > now() - {self.history_window_hours}h "
                f"ORDER BY time ASC"
            )
            return list(self.client.query(query).get_points())
        except Exception as e:
            self.log(f"InfluxDB query failed: {e}", level="ERROR")
            return []

    def _write_influx(self, now, fields):
        """Write computed fields for the ended phase interval into InfluxDB."""
        try:
            self.client.write_points([{
                "measurement": self.measurement,
                "tags": {"entity": self.entity},
                "time": now.isoformat(),
                "fields": fields
            }])
        except Exception as e:
            self.log(f"Influx write error: {e}", level="ERROR")

    def _notify(self, message):
        """Send a Home Assistant notification if notify_service is configured."""
        if not self.notify_service:
            return
        try:
            self.call_service(
                self.notify_service,
                title="Device Alert",
                message=f"{self.entity}: {message}"
            )
        except Exception as e:
            self.log(f"Notify error: {e}", level="ERROR")

    # ---------------- Data analysis helpers ----------------
    def _extract_activity_segments(self, samples):
        """Return active intervals and timing info by thresholding power samples."""
        active_segments = []
        active_start = None
        last_state = float(samples[0]["value"]) > self.threshold_watt
        last_active_end = None
        recent_active_duration = recent_idle_duration = 0.0

        for sample in samples:
            timestamp = tparse(sample["time"]).astimezone()
            is_active = float(sample["value"]) > self.threshold_watt
            if is_active != last_state:
                if is_active:  # became active
                    if last_active_end:
                        recent_idle_duration = (timestamp - last_active_end).total_seconds() / 60
                    active_start = timestamp
                else:  # became inactive
                    if active_start:
                        duration = (timestamp - active_start).total_seconds() / 60
                        recent_active_duration = duration
                        if duration >= self.min_interval:
                            active_segments.append((active_start, timestamp))
                    active_start, last_active_end = None, timestamp
                last_state = is_active

        return active_segments, last_state, active_start, recent_active_duration, recent_idle_duration

    def _extract_idle_segments(self, active_segments):
        """Compute idle intervals between active phases that exceed minimum duration."""
        return [
            (active_segments[i - 1][1], active_segments[i][0])
            for i in range(1, len(active_segments))
            if (active_segments[i][0] - active_segments[i - 1][1]).total_seconds() / 60 >= self.min_interval
        ]

    def _compute_medians(self, active_segments, idle_segments):
        """Compute median active and idle durations."""
        def median_minutes(segments):
            return statistics.median([(b - a).total_seconds() / 60 for a, b in segments]) if segments else 0.0
        return median_minutes(active_segments), median_minutes(idle_segments)

    def _get_margin_limits(self):
        """Return lower and upper bound functions based on configured margin."""
        if self.margin_minutes:
            return (
                lambda x: max(0, x - self.margin_minutes),
                lambda x: x + self.margin_minutes
            )
        return (
            lambda x: x * (1 - self.margin_percent / 100),
            lambda x: x * (1 + self.margin_percent / 100)
        )

    def _current_phase_info(self, is_active, active_start, active_segments):
        """Determine current phase (active/inactive), elapsed duration, and timestamp."""
        now = datetime.now().astimezone()
        if is_active:
            anchor = active_start or active_segments[-1][1]
            return "active", (now - anchor).total_seconds() / 60, 0.0, now
        last_end = active_segments[-1][1]
        return "inactive", 0.0, (now - last_end).total_seconds() / 60, now

    # ---------------- Alert and state management ----------------
    def _check_immediate_alert(self, phase, curr_active, curr_idle, med_active, med_idle, lo, up):
        """Check for immediate long-interval alerts."""
        if phase == "active" and med_active and curr_active >= self.min_interval and curr_active > up(med_active):
            return True, f"active too long: {curr_active:.1f}m > {up(med_active):.1f}m", "active_long"
        if phase == "inactive" and med_idle and curr_idle >= self.min_interval and curr_idle > up(med_idle):
            return True, f"idle too long: {curr_idle:.1f}m > {up(med_idle):.1f}m", "idle_long"
        return False, "", ""

    def _handle_pending_alerts(self, phase, curr_active, curr_idle):
        """Fire delayed 'too short' alerts when phase persists beyond min_interval."""
        if phase == "active" and self.pend_idle_reason and curr_active >= self.min_interval:
            self._notify(self.pend_idle_reason)
            self.log(f"{self.entity}: fired pending idle_short after buffer")
            self.pend_idle_reason = self.pend_idle_since = ""
        elif phase == "inactive" and self.pend_active_reason and curr_idle >= self.min_interval:
            self._notify(self.pend_active_reason)
            self.log(f"{self.entity}: fired pending active_short after buffer")
            self.pend_active_reason = self.pend_active_since = ""

    def _on_phase_flip(self, flipped, now, phase, med_active, med_idle, lo, up, recent_active, recent_idle):
        """Handle actions at phase flip: resolve alerts, set pending states, write DB entry."""
        if not flipped:
            return

        # End ongoing alerts
        if self.alert_state == "alert":
            if self.prev_kind == "active_long" and self.prev_phase == "active":
                self._notify(f"active long interval ended (duration {recent_active:.1f}m)")
            elif self.prev_kind == "idle_long" and self.prev_phase == "inactive":
                self._notify(f"idle long interval ended (duration {recent_idle:.1f}m)")

        # Set pending short alerts
        if self.prev_phase == "active" and med_active:
            if self.min_interval <= recent_active < lo(med_active):
                self.pend_active_reason = f"active too short: {recent_active:.1f}m < {lo(med_active):.1f}m"
                self.pend_active_since = now.isoformat()
        elif self.prev_phase == "inactive" and med_idle:
            if self.min_interval <= recent_idle < lo(med_idle):
                self.pend_idle_reason = f"idle too short: {recent_idle:.1f}m < {lo(med_idle):.1f}m"
                self.pend_idle_since = now.isoformat()

        # Write results to InfluxDB
        fields = {
            "phase": self.prev_phase,
            "median_active_minutes": med_active,
            "median_inactive_minutes": med_idle,
            "alert_state": self.alert_state,
            "alert_kind": self.alert_kind,
            "alert_ts": self.alert_timestamp,
            "pend_idle_reason": self.pend_idle_reason,
            "pend_idle_since": self.pend_idle_since,
            "pend_active_reason": self.pend_active_reason,
            "pend_active_since": self.pend_active_since,
        }
        if self.prev_phase == "active":
            fields["last_active_minutes"] = recent_active
        elif self.prev_phase == "inactive":
            fields["last_inactive_minutes"] = recent_idle
        self._write_influx(now, fields)

    # ---------------- Main tick loop ----------------
    def tick(self, _):
        """Main periodic function: analyze activity, compute medians, trigger alerts, log state."""
        samples = self._fetch_recent_points()
        if not samples:
            return

        active_segments, is_active, active_start, recent_active, recent_idle = self._extract_activity_segments(samples)
        if not active_segments:
            return

        idle_segments = self._extract_idle_segments(active_segments)
        med_active, med_idle = self._compute_medians(active_segments, idle_segments)
        lo, up = self._get_margin_limits()
        phase, curr_active, curr_idle, now = self._current_phase_info(is_active, active_start, active_segments)
        flipped = self.prev_phase and self.prev_phase != phase

        in_alert, reason, kind = self._check_immediate_alert(phase, curr_active, curr_idle, med_active, med_idle, lo, up)
        if in_alert and (self.alert_state != "alert" or self.alert_kind != kind):
            self.alert_state, self.alert_kind, self.alert_timestamp = "alert", kind, now.isoformat()
            self._notify(reason)

        self._handle_pending_alerts(phase, curr_active, curr_idle)
        self._on_phase_flip(flipped, now, phase, med_active, med_idle, lo, up, recent_active, recent_idle)

        # Compact phase summary log
        if phase == "active":
            self.log(f"{self.entity}: active={curr_active:.1f}m, median={med_active:.1f}, "
                     f"limit={lo(med_active):.1f}/{up(med_active):.1f}, {'ALERT '+reason if in_alert else 'OK'}")
        else:
            self.log(f"{self.entity}: inactive={curr_idle:.1f}m, median={med_idle:.1f}, "
                     f"limit={lo(med_idle):.1f}/{up(med_idle):.1f}, {'ALERT '+reason if in_alert else 'OK'}")

        self.prev_phase, self.prev_kind = phase, (kind if in_alert else "")
