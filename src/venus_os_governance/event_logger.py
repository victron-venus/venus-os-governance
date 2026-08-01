"""Event logger integration with dbus-event-log."""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EventLogger:
    """Integration with dbus-event-log for audit trail."""

    def __init__(
        self,
        db_path: str | Path = "/var/lib/dbus-event-log/events.db",
        mqtt_host: str | None = None,
        mqtt_port: int = 1883,
        mqtt_topic_prefix: str = "venus/governance",
    ):
        self.db_path = Path(db_path)
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_topic_prefix = mqtt_topic_prefix
        self._mqtt_client = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database for local event logging."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS governance_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    policy_id TEXT,
                    rule_id TEXT,
                    inverter_action TEXT,
                    battery_soc REAL,
                    battery_status TEXT,
                    allowed INTEGER,
                    policy_action TEXT,
                    approval_required INTEGER,
                    approval_request_id TEXT,
                    details TEXT,
                    metadata TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON governance_events(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_policy ON governance_events(policy_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_action ON governance_events(inverter_action)
            """)

    def log_event(self, event: dict[str, Any]) -> None:
        """Log a policy evaluation event."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO governance_events (
                        timestamp, event_type, policy_id, rule_id,
                        inverter_action, battery_soc, battery_status,
                        allowed, policy_action, approval_required,
                        approval_request_id, details, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("timestamp", datetime.now().isoformat()),
                        event.get("event_type", "policy_evaluation"),
                        event.get("policy_id"),
                        event.get("rule_id"),
                        event.get("inverter_action"),
                        event.get("battery_soc"),
                        event.get("battery_status"),
                        int(event.get("allowed", True)),
                        event.get("policy_action"),
                        int(event.get("approval_required", False)),
                        event.get("approval_request_id"),
                        str(event.get("details", {})),
                        str(event.get("metadata", {})),
                    ),
                )
        except Exception as e:
            logger.exception(f"Failed to log event to SQLite: {e}")

        # Also publish to MQTT if configured
        if self.mqtt_host:
            self._publish_mqtt(event)

    def _publish_mqtt(self, event: dict[str, Any]) -> None:
        """Publish event to MQTT."""
        try:
            import json

            import paho.mqtt.client as mqtt

            if self._mqtt_client is None:
                self._mqtt_client = mqtt.Client()
                self._mqtt_client.connect(self.mqtt_host, self.mqtt_port, 60)

            topic = f"{self.mqtt_topic_prefix}/events"
            payload = json.dumps(event, default=str)
            self._mqtt_client.publish(topic, payload)
        except Exception as e:
            logger.exception(f"Failed to publish MQTT event: {e}")

    def query_events(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        policy_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query governance events."""
        query = "SELECT * FROM governance_events WHERE 1=1"
        params = []

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time.isoformat())
        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time.isoformat())
        if policy_id:
            query += " AND policy_id = ?"
            params.append(policy_id)
        if action:
            query += " AND inverter_action = ?"
            params.append(action)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Failed to query events: {e}")
            return []

    def get_approval_requests(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get approval request events."""
        query = "SELECT * FROM governance_events WHERE event_type = 'approval_request'"
        params = []

        if status:
            query += " AND json_extract(metadata, '$$.status') = ?"
            params.append(status)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Failed to query approval requests: {e}")
            return []
