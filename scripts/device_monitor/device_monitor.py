import appdaemon.plugins.hass.hassapi as hass
from influxdb import InfluxDBClient
from datetime import datetime, timezone
import statistics
from typing import List, Dict, Tuple, Optional, Callable
from enum import Enum


class AlertKind(Enum):
    """Alert types for device monitoring."""
    NONE = ""
    ACTIVE_LONG = "active_long"
    ACTIVE_SHORT = "active_short"
    IDLE_LONG = "idle_long"
    IDLE_SHORT = "idle_short"


class AlertState(Enum):
    """Alert state machine."""
    OK = "ok"
    ALERT = "alert"
    PENDING = "pending"


def tparse(ts: str) -> Optional[datetime]:
    """Parse InfluxDB ISO8601 timestamp into timezone-aware datetime."""
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError) as e:
        return None


class DeviceMonitor(hass.Hass):
    """Monitors a power entity, detects activity cycles, and triggers alerts or logs to InfluxDB."""

    def initialize(self):
        """Initialize configuration, connect to InfluxDB, and start the periodic task."""
        try:
            self._load_config()
            self._init_influx_client()
            self._init_state()
            self._send_startup_notification()
            self._start_monitoring()
        except Exception as e:
            self.log(f"Initialization failed: {e}", level="ERROR")
            raise

    def _load_config(self):
        """Load and validate configuration parameters."""
        args = self.args
        
        # Required parameters
        self.entity = args["entity"]
        self.threshold_watt = float(args["threshold_watt"])
        self.min_interval = float(args["minimum_interval_minutes"])
        self.check_interval = int(args["check_interval_seconds"])
        self.measurement = args["influx_measurement_var"]
        
        # Optional parameters with defaults
        self.margin_percent = float(args.get("margin_percent", 0))
        self.margin_minutes = float(args.get("margin_minutes", 0))
        self.notify_service = args.get("notify_service")
        self.history_window_hours = int(args.get("history_window_hours", 24))
        self.alert_cooldown_minutes = float(args.get("alert_cooldown_minutes", 5))
        self.debug_logging = args.get("debug_logging", False)
        self.send_test_notification = args.get("send_test_notification", False)
        self.statistic_method = args.get("statistic_method", "median").lower()
        
        # Validation
        if self.threshold_watt <= 0:
            raise ValueError("threshold_watt must be positive")
        if self.min_interval <= 0:
            raise ValueError("minimum_interval_minutes must be positive")
        if self.check_interval <= 0:
            raise ValueError("check_interval_seconds must be positive")
        if self.margin_percent < 0 or self.margin_minutes < 0:
            raise ValueError("margin values must be non-negative")
        if self.margin_percent > 0 and self.margin_minutes > 0:
            self.log("Both margin_percent and margin_minutes set - using margin_minutes", level="WARNING")
        if self.statistic_method not in ["median", "mean"]:
            raise ValueError("statistic_method must be either 'median' or 'mean'")

    def _init_influx_client(self):
        """Initialize InfluxDB client with error handling."""
        args = self.args
        try:
            self.client = InfluxDBClient(
                host=args["influx_host"],
                port=int(args["influx_port"]),
                username=args["influx_user"],
                password=args["influx_password"],
                database=args["influx_db"],
                timeout=5
            )
            # Test connection
            self.client.ping()
        except Exception as e:
            self.log(f"InfluxDB connection failed: {e}", level="ERROR")
            raise

    def _init_state(self):
        """Initialize internal state tracking variables."""
        # Pending alert state
        self.pend_active_reason = ""
        self.pend_idle_reason = ""
        self.pend_active_since = ""
        self.pend_idle_since = ""
        
        # Previous phase tracking
        self.prev_phase = None  # Changed from "" to None for clarity
        self.prev_kind = AlertKind.NONE
        
        # Alert state
        self.alert_state = AlertState.OK
        self.alert_kind = AlertKind.NONE
        self.alert_timestamp = ""
        self.last_alert_time = None
        
        # Processing flag to prevent overlapping executions
        self.processing = False

    def _send_startup_notification(self):
        """Send a test notification on startup if configured."""
        if self.send_test_notification:
            margin_str = f"{self.margin_minutes}m" if self.margin_minutes else f"{self.margin_percent}%"
            message = (
                f"DeviceMonitor started for {self.entity}\n"
                f"Threshold: {self.threshold_watt}W\n"
                f"Min interval: {self.min_interval}m\n"
                f"Margin: {margin_str}\n"
                f"Statistic: {self.statistic_method}\n"
                f"Check interval: {self.check_interval}s"
            )
            
            try:
                if self.notify_service:
                    self.call_service(
                        self.notify_service,
                        title="Device Monitor Started",
                        message=message
                    )
                    self.log(f"Startup notification sent for {self.entity}")
                else:
                    self.log("send_test_notification is true but notify_service not configured", level="WARNING")
            except Exception as e:
                self.log(f"Failed to send startup notification: {e}", level="ERROR")

    def _start_monitoring(self):
        """Start the periodic monitoring task."""
        self.run_every(self.tick, self.datetime(), self.check_interval)
        
        margin_str = f"{self.margin_minutes}m" if self.margin_minutes else f"{self.margin_percent}%"
        self.log(
            f"DeviceMonitor initialized for {self.entity} | "
            f"threshold={self.threshold_watt}W | "
            f"min_interval={self.min_interval}m | "
            f"margin={margin_str} | "
            f"statistic={self.statistic_method} | "
            f"history={self.history_window_hours}h | "
            f"check_interval={self.check_interval}s | "
            f"alert_cooldown={self.alert_cooldown_minutes}m"
        )

    # ---------------- InfluxDB and notify helpers ----------------
    
    def _fetch_recent_points(self) -> List[Dict]:
        """Fetch recent power samples from InfluxDB for the configured entity."""
        try:
            query = (
                f"SELECT value, time FROM W "
                f"WHERE entity_id='{self.entity}' "
                f"AND time > now() - {self.history_window_hours}h "
                f"ORDER BY time ASC"
            )
            points = list(self.client.query(query).get_points())
            
            if self.debug_logging:
                self.log(f"Fetched {len(points)} data points from InfluxDB")
            
            return points
            
        except Exception as e:
            self.log(f"InfluxDB query failed: {e}", level="ERROR")
            return []

    def _write_influx(self, now: datetime, fields: Dict):
        """Write computed fields for the ended phase interval into InfluxDB."""
        try:
            self.client.write_points([{
                "measurement": self.measurement,
                "tags": {"entity": self.entity},
                "time": now.isoformat(),
                "fields": fields
            }])
            
            if self.debug_logging:
                self.log(f"Wrote to InfluxDB: {fields}")
                
        except Exception as e:
            self.log(f"Influx write error: {e}", level="ERROR")

    def _notify(self, message: str):
        """Send a Home Assistant notification with cooldown to prevent spam."""
        if not self.notify_service:
            return
            
        # Check cooldown
        now = datetime.now()
        if self.last_alert_time:
            elapsed = (now - self.last_alert_time).total_seconds() / 60
            if elapsed < self.alert_cooldown_minutes:
                self.log(f"Alert suppressed (cooldown: {elapsed:.1f}m < {self.alert_cooldown_minutes}m)")
                return
        
        try:
            self.call_service(
                self.notify_service,
                title="Device Alert",
                message=f"{self.entity}: {message}"
            )
            self.last_alert_time = now
            self.log(f"Alert sent: {message}")
            
        except Exception as e:
            self.log(f"Notify error: {e}", level="ERROR")

    # ---------------- Data analysis helpers ----------------
    
    def _extract_activity_segments(self, samples: List[Dict]) -> Tuple:
        """Return active intervals and timing info by thresholding power samples."""
        if not samples:
            return [], False, None, 0.0, 0.0
            
        active_segments = []
        active_start = None
        last_state = float(samples[0]["value"]) > self.threshold_watt
        last_active_end = None
        recent_active_duration = 0.0
        recent_idle_duration = 0.0

        for sample in samples:
            timestamp = tparse(sample["time"])
            if not timestamp:
                continue
                
            timestamp = timestamp.astimezone()
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
                    active_start = None
                    last_active_end = timestamp
                    
                last_state = is_active

        return active_segments, last_state, active_start, recent_active_duration, recent_idle_duration

    def _extract_idle_segments(self, active_segments: List[Tuple]) -> List[Tuple]:
        """Compute idle intervals between active phases that exceed minimum duration."""
        if len(active_segments) < 2:
            return []
            
        return [
            (active_segments[i - 1][1], active_segments[i][0])
            for i in range(1, len(active_segments))
            if (active_segments[i][0] - active_segments[i - 1][1]).total_seconds() / 60 >= self.min_interval
        ]

    def _compute_statistics(self, active_segments: List[Tuple], idle_segments: List[Tuple]) -> Tuple[float, float, float, float]:
        """Compute mean and median active and idle durations."""
        def calculate_durations(segments):
            if not segments:
                return 0.0, 0.0
            durations = [(b - a).total_seconds() / 60 for a, b in segments]
            if len(durations) == 0:
                return 0.0, 0.0
            mean_val = statistics.mean(durations)
            median_val = statistics.median(durations)
            return mean_val, median_val
        
        mean_active, median_active = calculate_durations(active_segments)
        mean_idle, median_idle = calculate_durations(idle_segments)
        
        return mean_active, median_active, mean_idle, median_idle
    
    def _get_selected_statistic(self, mean_val: float, median_val: float) -> float:
        """Return the configured statistic (mean or median)."""
        return mean_val if self.statistic_method == "mean" else median_val

    def _get_margin_limits(self) -> Tuple[Callable, Callable]:
        """Return lower and upper bound functions based on configured margin."""
        if self.margin_minutes > 0:
            return (
                lambda x: max(0, x - self.margin_minutes),
                lambda x: x + self.margin_minutes
            )
        return (
            lambda x: x * (1 - self.margin_percent / 100),
            lambda x: x * (1 + self.margin_percent / 100)
        )

    def _current_phase_info(self, is_active: bool, active_start: Optional[datetime], 
                           active_segments: List[Tuple]) -> Tuple[str, float, float, datetime]:
        """Determine current phase (active/inactive), elapsed duration, and timestamp."""
        now = datetime.now().astimezone()
        
        if is_active:
            anchor = active_start if active_start else (active_segments[-1][1] if active_segments else now)
            elapsed = (now - anchor).total_seconds() / 60
            return "active", elapsed, 0.0, now
            
        if not active_segments:
            return "inactive", 0.0, 0.0, now
            
        last_end = active_segments[-1][1]
        elapsed = (now - last_end).total_seconds() / 60
        return "inactive", 0.0, elapsed, now

    # ---------------- Alert and state management ----------------
    
    def _check_immediate_alert(self, phase: str, curr_active: float, curr_idle: float,
                              med_active: float, med_idle: float, 
                              lo: Callable, up: Callable) -> Tuple[bool, str, AlertKind]:
        """Check for immediate long-interval alerts."""
        if phase == "active" and med_active > 0 and curr_active >= self.min_interval:
            if curr_active > up(med_active):
                return (
                    True,
                    f"active too long: {curr_active:.1f}m > {up(med_active):.1f}m",
                    AlertKind.ACTIVE_LONG
                )
                
        if phase == "inactive" and med_idle > 0 and curr_idle >= self.min_interval:
            if curr_idle > up(med_idle):
                return (
                    True,
                    f"idle too long: {curr_idle:.1f}m > {up(med_idle):.1f}m",
                    AlertKind.IDLE_LONG
                )
                
        return False, "", AlertKind.NONE

    def _handle_pending_alerts(self, phase: str, curr_active: float, curr_idle: float):
        """Fire delayed 'too short' alerts when phase persists beyond min_interval."""
        if phase == "active" and self.pend_idle_reason:
            if curr_active >= self.min_interval:
                self._notify(self.pend_idle_reason)
                self.log(f"Fired pending idle_short alert after buffer period")
                self.pend_idle_reason = ""
                self.pend_idle_since = ""
                
        elif phase == "inactive" and self.pend_active_reason:
            if curr_idle >= self.min_interval:
                self._notify(self.pend_active_reason)
                self.log(f"Fired pending active_short alert after buffer period")
                self.pend_active_reason = ""
                self.pend_active_since = ""

    def _on_phase_flip(self, flipped: bool, now: datetime, phase: str,
                      mean_active: float, median_active: float, mean_idle: float, median_idle: float,
                      stat_active: float, stat_idle: float, lo: Callable, up: Callable,
                      recent_active: float, recent_idle: float):
        """Handle actions at phase flip: resolve alerts, set pending states, write DB entry."""
        if not flipped:
            return

        # Clear alert state when phase ends
        if self.alert_state == AlertState.ALERT:
            if self.prev_kind == AlertKind.ACTIVE_LONG and self.prev_phase == "active":
                self._notify(f"active long interval ended (duration {recent_active:.1f}m)")
                self.alert_state = AlertState.OK
                self.alert_kind = AlertKind.NONE
                
            elif self.prev_kind == AlertKind.IDLE_LONG and self.prev_phase == "inactive":
                self._notify(f"idle long interval ended (duration {recent_idle:.1f}m)")
                self.alert_state = AlertState.OK
                self.alert_kind = AlertKind.NONE

        # Set pending short alerts
        if self.prev_phase == "active" and stat_active > 0:
            if self.min_interval <= recent_active < lo(stat_active):
                self.pend_active_reason = f"active too short: {recent_active:.1f}m < {lo(stat_active):.1f}m"
                self.pend_active_since = now.isoformat()
                
        elif self.prev_phase == "inactive" and stat_idle > 0:
            if self.min_interval <= recent_idle < lo(stat_idle):
                self.pend_idle_reason = f"idle too short: {recent_idle:.1f}m < {lo(stat_idle):.1f}m"
                self.pend_idle_since = now.isoformat()

        # Write phase completion to InfluxDB with both mean and median
        fields = {
            "phase": self.prev_phase,
            "mean_active_minutes": mean_active,
            "median_active_minutes": median_active,
            "mean_inactive_minutes": mean_idle,
            "median_inactive_minutes": median_idle,
            "statistic_method": self.statistic_method,
            "alert_state": self.alert_state.value,
            "alert_kind": self.alert_kind.value,
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
        # Prevent overlapping executions
        if self.processing:
            self.log("Previous tick still processing, skipping", level="WARNING")
            return
            
        self.processing = True
        
        try:
            self._process_tick()
        except Exception as e:
            self.log(f"Error in tick processing: {e}", level="ERROR")
            import traceback
            self.log(traceback.format_exc(), level="ERROR")
        finally:
            self.processing = False

    def _process_tick(self):
        """Internal tick processing logic."""
        samples = self._fetch_recent_points()
        if not samples:
            self.log("No samples retrieved from InfluxDB", level="WARNING")
            return

        # Extract activity segments
        active_segments, is_active, active_start, recent_active, recent_idle = self._extract_activity_segments(samples)
        
        if not active_segments:
            if self.debug_logging:
                self.log("No valid active segments found in history")
            return

        # Compute statistics
        idle_segments = self._extract_idle_segments(active_segments)
        mean_active, median_active, mean_idle, median_idle = self._compute_statistics(active_segments, idle_segments)
        
        # Select the configured statistic for alert calculations
        stat_active = self._get_selected_statistic(mean_active, median_active)
        stat_idle = self._get_selected_statistic(mean_idle, median_idle)
        
        lo, up = self._get_margin_limits()
        phase, curr_active, curr_idle, now = self._current_phase_info(is_active, active_start, active_segments)
        
        # Detect phase flip
        flipped = self.prev_phase is not None and self.prev_phase != phase

        # Check for immediate alerts
        in_alert, reason, kind = self._check_immediate_alert(
            phase, curr_active, curr_idle, stat_active, stat_idle, lo, up
        )
        
        # Update alert state and notify
        if in_alert and (self.alert_state != AlertState.ALERT or self.alert_kind != kind):
            self.alert_state = AlertState.ALERT
            self.alert_kind = kind
            self.alert_timestamp = now.isoformat()
            self._notify(reason)
        elif not in_alert and self.alert_state == AlertState.ALERT:
            # Clear alert if condition resolved without phase flip
            self.alert_state = AlertState.OK
            self.alert_kind = AlertKind.NONE

        # Handle pending alerts
        self._handle_pending_alerts(phase, curr_active, curr_idle)
        
        # Handle phase flip
        self._on_phase_flip(flipped, now, phase, mean_active, median_active, mean_idle, median_idle, 
                          stat_active, stat_idle, lo, up, recent_active, recent_idle)

        # Logging
        self._log_status(phase, curr_active, curr_idle, stat_active, stat_idle, lo, up, in_alert, reason)

        # Update state
        self.prev_phase = phase
        self.prev_kind = kind if in_alert else AlertKind.NONE

    def _log_status(self, phase: str, curr_active: float, curr_idle: float,
                   stat_active: float, stat_idle: float, lo: Callable, up: Callable,
                   in_alert: bool, reason: str):
        """Log current monitoring status."""
        stat_label = self.statistic_method
        if phase == "active":
            status = f"ALERT: {reason}" if in_alert else "OK"
            self.log(
                f"{self.entity}: active={curr_active:.1f}m, {stat_label}={stat_active:.1f}m, "
                f"limits=[{lo(stat_active):.1f}, {up(stat_active):.1f}], {status}"
            )
        else:
            status = f"ALERT: {reason}" if in_alert else "OK"
            self.log(
                f"{self.entity}: inactive={curr_idle:.1f}m, {stat_label}={stat_idle:.1f}m, "
                f"limits=[{lo(stat_idle):.1f}, {up(stat_idle):.1f}], {status}"
            )

    def terminate(self):
        """Clean up resources on shutdown."""
        try:
            if hasattr(self, 'client'):
                self.client.close()
                self.log("InfluxDB connection closed")
        except Exception as e:
            self.log(f"Error during termination: {e}", level="ERROR")
