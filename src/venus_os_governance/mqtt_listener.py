"""MQTT listener for command interception and policy evaluation."""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

# pylint: disable=import-error
import paho.mqtt.client as mqtt
from paho.mqtt.client import CallbackAPIVersion

# pylint: enable=import-error
from .dbus_integration import VenusDBusClient
from .engine import PolicyEngine, PolicyEvaluationResult
from .event_logger import EventLogger
from .models import (
    ApprovalRequest,
    BatteryState,
    InverterAction,
)

logger = logging.getLogger(__name__)


@dataclass
class InverterCommand:
    """Parsed inverter command from MQTT."""

    command: str
    payload: dict[str, Any]
    topic: str
    timestamp: datetime


class CommandMapper:
    """Maps inverter MQTT commands to InverterAction enum."""

    COMMAND_MAP: ClassVar[dict[str, InverterAction]] = {
        "toggle": InverterAction.EXTERNAL_CONTROL,
        "press": InverterAction.EXTERNAL_CONTROL,
        "setpoint": InverterAction.SET_POWER_SETPOINT,
        "dry_run": InverterAction.EXTERNAL_CONTROL,
        "limits": InverterAction.SET_SOC_LIMIT,
        "ess_mode": InverterAction.EXTERNAL_CONTROL,
        "loop_interval": InverterAction.EXTERNAL_CONTROL,
    }

    @classmethod
    def map_command(cls, command: str) -> InverterAction | None:
        """Map command string to InverterAction."""
        return cls.COMMAND_MAP.get(command)

    @classmethod
    def extract_battery_state(cls, state: dict[str, Any]) -> dict[str, Any]:
        """Extract relevant battery state from inverter state."""
        return {
            "soc": state.get("battery", {}).get("soc", 50.0),
            "voltage": state.get("battery", {}).get("voltage", 48.0),
            "current": state.get("battery", {}).get("current", 0.0),
            "power": state.get("battery", {}).get("power", 0.0),
            "status": state.get("battery", {}).get("status", "idle"),
            "temperature": state.get("battery", {}).get("temperature"),
        }


@dataclass
class MQTTListenerConfig:
    """Configuration for MQTTListener."""

    broker: str = "localhost"
    port: int = 1883
    subscribe_prefix: str = "governance"
    forward_prefix: str = "inverter"
    client_id: str | None = None


class MQTTListener:
    """MQTT listener that intercepts commands and evaluates them against policies."""

    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        policy_engine: PolicyEngine,
        config: MQTTListenerConfig | None = None,
        event_logger: EventLogger | None = None,
        dbus_client: VenusDBusClient | None = None,
    ):
        self.policy_engine = policy_engine
        self.event_logger = event_logger
        self.dbus_client = dbus_client
        self.config = config or MQTTListenerConfig()
        self.client_id = self.config.client_id or f"venus-governance-{uuid.uuid4().hex[:8]}"

        self._client: mqtt.Client | None = None
        self._connected = False
        self._running = False
        self._latest_battery_state: dict[str, Any] = {}
        self._pending_tasks: set[asyncio.Task] = set()

    def connect(self) -> bool:
        """Connect to MQTT broker."""
        try:
            self._client = mqtt.Client(  # type: ignore[call-arg]
                client_id=self.client_id,
                callback_api_version=CallbackAPIVersion.VERSION2,
            )
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message
            self._client.on_disconnect = self._on_disconnect

            self._client.connect_async(self.config.broker, self.config.port, 60)
            self._client.loop_start()
            logger.info(
                "Governance MQTT connecting to %s:%s",
                self.config.broker,
                self.config.port,
            )
            return True
        except Exception as e:  # pylint: disable=broad-except
            logger.exception("Governance MQTT connection failed: %s", e)
            return False

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            logger.info("Governance MQTT disconnected")

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: dict[str, Any],
        _rc: int,
        _properties: Any | None = None,
    ) -> None:
        """Connected to broker."""
        self._connected = True
        self._running = True
        logger.info("Governance MQTT connected")

        # Subscribe to governance command topics
        topic = f"{self.config.subscribe_prefix}/cmd/#"
        _client.subscribe(topic)
        logger.info("Subscribed to %s", topic)

        # Also subscribe to inverter state for battery state tracking
        state_topic = f"{self.config.forward_prefix}/state"
        _client.subscribe(state_topic)
        logger.info("Subscribed to %s", state_topic)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _rc: int,
        _properties: Any | None = None,
    ) -> None:
        """Disconnected from broker."""
        self._connected = False
        self._running = False
        if _rc != 0:
            logger.warning("Governance MQTT disconnected unexpectedly (rc=%s)", _rc)

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _msg: mqtt.MQTTMessage,
    ) -> None:
        """Received message."""
        try:
            topic = _msg.topic
            logger.debug("Received message on %s", topic)

            # Parse inverter state for battery tracking
            if topic == f"{self.config.forward_prefix}/state":
                try:
                    state = json.loads(_msg.payload.decode())
                    self._latest_battery_state = CommandMapper.extract_battery_state(state)
                except json.JSONDecodeError:
                    pass
                return

            # Handle governance command topics
            if topic.startswith(f"{self.config.subscribe_prefix}/cmd/"):
                cmd = topic.split("/")[-1]
                payload: dict[str, Any] = {}
                if _msg.payload:
                    try:
                        payload = json.loads(_msg.payload.decode())
                    except json.JSONDecodeError:
                        payload = {"value": _msg.payload.decode()}

                command = InverterCommand(
                    command=cmd,
                    payload=payload,
                    topic=topic,
                    timestamp=datetime.now(),
                )
                task = asyncio.create_task(self._process_command(command))
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)

        except Exception as e:  # pylint: disable=broad-except
            logger.exception("Governance MQTT message error: %s", e)

    async def _process_command(self, command: InverterCommand) -> None:
        """Process intercepted command through policy engine."""
        inverter_action = CommandMapper.map_command(command.command)
        if not inverter_action:
            logger.debug(
                "Unknown command: %s, forwarding without evaluation",
                command.command,
            )
            self._forward_command(command)
            return

        battery_state = await self._get_battery_state()
        context = {"command": command.command, "payload": command.payload}
        result = self.policy_engine.evaluate(inverter_action, battery_state, context)

        self._log_evaluation(inverter_action, battery_state, result, context)

        if result.allowed and not result.approval_required:
            logger.info("Command %s allowed by policy", command.command)
            self._forward_command(command)
        elif result.approval_required and result.approval_request:
            logger.warning(
                "Command %s requires approval: %s",
                command.command,
                result.approval_request.id,
            )
            self._send_approval_response(command, result.approval_request)
        else:
            logger.warning("Command %s denied by policy: %s", command.command, result.reason)
            self._send_denial_response(command, result.reason)

    async def _get_battery_state(self) -> "BatteryState":
        """Get current battery state from cache or D-Bus."""
        battery_state_dict = self._latest_battery_state.copy()
        if not battery_state_dict and self.dbus_client:
            try:
                state = await self.dbus_client.get_battery_state()
                if state:
                    battery_state_dict = state.model_dump()
            except Exception as e:  # pylint: disable=broad-except
                logger.warning("Failed to get battery state from D-Bus: %s", e)

        return (
            BatteryState(**battery_state_dict)
            if battery_state_dict
            else BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="idle")
        )

    def _log_evaluation(
        self,
        inverter_action: InverterAction,
        battery_state: "BatteryState",
        result: PolicyEvaluationResult,
        context: dict[str, Any],
    ) -> None:
        """Log policy evaluation result."""
        if not self.event_logger:
            return

        default_policy = self.policy_engine.default_policy
        log_entry = {
            "event_type": "command_interception",
            "policy_id": default_policy.id if default_policy else "unknown",
            "inverter_action": inverter_action.value,
            "battery_soc": battery_state.soc,
            "battery_status": battery_state.status,
            "allowed": result.allowed,
            "policy_action": result.action.value,
            "approval_required": result.approval_required,
            "approval_request_id": result.approval_request.id if result.approval_request else None,
            "details": {"command": context["command"], "payload": context["payload"]},
            "metadata": context,
        }
        self.event_logger.log_event(log_entry)

    def _forward_command(self, command: InverterCommand) -> None:
        """Forward approved command to inverter-control topic."""
        if not self._client or not self._connected:
            logger.error("MQTT not connected, cannot forward command")
            return

        topic = f"{self.config.forward_prefix}/cmd/{command.command}"
        payload = json.dumps(command.payload)
        self._client.publish(topic, payload, qos=1)
        logger.info("Forwarded command to %s", topic)

    def _send_approval_response(self, command: InverterCommand, request: ApprovalRequest) -> None:
        """Send approval request response."""
        if not self._client or not self._connected:
            return

        topic = f"{self.config.subscribe_prefix}/response/approval_required"
        reason = f"Policy requires approval: {request.metadata.get('rule_name', 'unknown rule')}"
        response = {
            "original_command": command.command,
            "original_payload": command.payload,
            "request_id": request.id,
            "expires_at": request.expires_at.isoformat(),
            "required_roles": request.approval_roles,
            "reason": reason,
        }
        self._client.publish(topic, json.dumps(response), qos=1)

    def _send_denial_response(self, command: InverterCommand, reason: str | None) -> None:
        """Send denial response."""
        if not self._client or not self._connected:
            return

        topic = f"{self.config.subscribe_prefix}/response/denied"
        response = {
            "original_command": command.command,
            "original_payload": command.payload,
            "reason": reason or "Denied by policy",
        }
        self._client.publish(topic, json.dumps(response), qos=1)

    def update_battery_state(self, battery_state_dict: dict[str, Any]) -> None:
        """Update cached battery state from external source (e.g., D-Bus monitor)."""
        self._latest_battery_state = battery_state_dict


@dataclass
class GovernanceDaemonConfig:
    """Configuration for GovernanceMQTTDaemon."""

    broker: str = "localhost"
    port: int = 1883
    subscribe_prefix: str = "governance"
    forward_prefix: str = "inverter"
    poll_interval: float = 5.0


class GovernanceMQTTDaemon:
    """Complete governance daemon with MQTT listener and D-Bus monitoring."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        dbus_client: VenusDBusClient,
        event_logger: EventLogger,
        config: GovernanceDaemonConfig | None = None,
    ):
        self.policy_engine = policy_engine
        self.dbus_client = dbus_client
        self.event_logger = event_logger
        self.config = config or GovernanceDaemonConfig()

        self.mqtt_listener = MQTTListener(
            policy_engine=policy_engine,
            event_logger=event_logger,
            dbus_client=dbus_client,
            config=MQTTListenerConfig(
                broker=self.config.broker,
                port=self.config.port,
                subscribe_prefix=self.config.subscribe_prefix,
                forward_prefix=self.config.forward_prefix,
            ),
        )

        self._dbus_monitor_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the governance daemon."""
        self._running = True

        # Connect to D-Bus
        await self.dbus_client.connect()

        # Connect to MQTT
        self.mqtt_listener.connect()

        # Start D-Bus monitoring loop
        self._dbus_monitor_task = asyncio.create_task(self._monitor_loop())

        logger.info("Governance daemon started")

    async def stop(self) -> None:
        """Stop the governance daemon."""
        self._running = False
        self.mqtt_listener.disconnect()

        if self._dbus_monitor_task:
            self._dbus_monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dbus_monitor_task

        await self.dbus_client.close()
        logger.info("Governance daemon stopped")

    async def _monitor_loop(self) -> None:
        """Monitor D-Bus for battery state changes and evaluate policies."""
        try:
            while self._running:
                try:
                    await self._check_battery_state()
                    await asyncio.sleep(self.config.poll_interval)
                except Exception as e:  # pylint: disable=broad-except
                    logger.exception("Monitor loop error: %s", e)
                    await asyncio.sleep(self.config.poll_interval)
        except asyncio.CancelledError:
            logger.debug("Monitor loop cancelled")
            raise

    async def _check_battery_state(self) -> None:
        """Check battery state and evaluate discharge/charge policies."""
        battery_state = await self.dbus_client.get_battery_state()
        if not battery_state:
            return

        # Update latest state for MQTT listener
        self.mqtt_listener.update_battery_state(battery_state.model_dump())

        # Check discharge rules
        if battery_state.status == "discharging":
            result = self.policy_engine.evaluate(
                InverterAction.DISCHARGE,
                battery_state,
            )
            self._handle_policy_result("discharge", result, battery_state)

        # Check charge rules
        if battery_state.status == "charging":
            result = self.policy_engine.evaluate(
                InverterAction.CHARGE,
                battery_state,
            )
            self._handle_policy_result("charge", result, battery_state)

        # Check critical SOC
        if battery_state.soc <= 10:
            result = self.policy_engine.evaluate(
                InverterAction.DISCHARGE,
                battery_state,
            )
            if result.action.value == "deny":
                logger.critical("Critical SOC emergency stop triggered: %s%%", battery_state.soc)

    def _handle_policy_result(
        self, action: str, result: PolicyEvaluationResult, battery_state: BatteryState
    ) -> None:
        """Handle policy evaluation result for monitoring."""
        if not result.allowed and result.approval_required:
            logger.warning(
                "%s requires approval: %s, request_id=%s",
                action.capitalize(),
                result.reason,
                result.approval_request.id if result.approval_request else "N/A",
            )
            if self.event_logger:
                default_policy = self.policy_engine.default_policy
                self.event_logger.log_event(
                    {
                        "event_type": "policy_monitor",
                        "policy_id": default_policy.id if default_policy else "unknown",
                        "inverter_action": action,
                        "battery_soc": battery_state.soc,
                        "battery_status": battery_state.status,
                        "allowed": result.allowed,
                        "policy_action": result.action.value,
                        "approval_required": result.approval_required,
                        "approval_request_id": result.approval_request.id
                        if result.approval_request
                        else None,
                        "details": {"monitor": True, "action": action},
                    }
                )
