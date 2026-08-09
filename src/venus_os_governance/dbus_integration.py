"""D-Bus integration for Venus OS Governance."""

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from dbus_next import BusType, Message
from dbus_next.aio import MessageBus

if TYPE_CHECKING:
    from dbus_next.aio import MessageBus as MessageBusT
    from dbus_next.aio import ProxyObject
else:
    MessageBusT = Any
    ProxyObject = Any

from .engine import PolicyEngine
from .models import BatteryState, InverterAction

logger = logging.getLogger(__name__)


class VenusDBusClient:
    """Client for interacting with Venus OS D-Bus services."""

    # Victron D-Bus service names
    VE_BOARD = "com.victronenergy.veboard"
    SETTINGS = "com.victronenergy.settings"
    BATTERY_PREFIX = "com.victronenergy.battery."
    SOLAR_CHARGER_PREFIX = "com.victronenergy.solarcharger."
    INVERTER_PREFIX = "com.victronenergy.vebus."
    SYSTEM_CALC = "com.victronenergy.system"
    DVCC = "com.victronenergy.dvcc"
    GRID_METER_PREFIX = "com.victronenergy.grid."

    def __init__(self, bus: MessageBusT | None = None):
        self.bus: MessageBusT | None = bus
        self._proxy_objects: dict[str, Any] = {}
        self._battery_path: str | None = None

    async def connect(self) -> None:
        """Connect to system bus."""
        if self.bus is None:
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        logger.info("Connected to D-Bus system bus")

    async def discover_battery(self) -> str | None:
        """Discover primary battery service."""
        if not self.bus:
            await self.connect()

        # List all services to find battery
        if not self.bus:
            return None
        try:
            reply = await self.bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="ListNames",
                    signature="",
                    body=[],
                )
            )
            if reply and reply.body:
                services = reply.body[0]
                for service in services:
                    if service.startswith(self.BATTERY_PREFIX):
                        self._battery_path = service
                        logger.info(f"Found battery service: {service}")
                        return service  # type: ignore[no-any-return]
        except Exception as e:
            logger.exception(f"Failed to discover battery: {e}")
        return None

    async def get_battery_state(self) -> BatteryState | None:
        """Get current battery state from D-Bus."""
        if not self.bus:
            await self.connect()

        if not self._battery_path:
            await self.discover_battery()

        if not self._battery_path:
            logger.warning("No battery service found")
            return None

        try:
            if not self.bus:
                return None
            proxy = await self.bus.get_proxy_object(  # type: ignore[misc]
                self._battery_path,
                "/",
                introspection=None,  # type: ignore[arg-type]
            )
            battery_iface = proxy.get_interface("com.victronenergy.Battery")

            # Get battery properties
            soc = await battery_iface.call_get_soc()
            voltage = await battery_iface.call_get_voltage()
            current = await battery_iface.call_get_current()
            power = await battery_iface.call_get_power()
            status = await battery_iface.call_get_status()

            # Temperature might be on a different interface
            temperature = None
            with contextlib.suppress(Exception):
                temperature = await battery_iface.call_get_temperature()

            # DVCC settings
            dvcc_enabled = False
            max_charge_current = None
            max_discharge_current = None
            try:
                dvcc_proxy = await self.bus.get_proxy_object(  # type: ignore[misc]
                    self.DVCC,
                    "/",
                    introspection=None,  # type: ignore[arg-type]
                )
                dvcc_iface = dvcc_proxy.get_interface("com.victronenergy.DVCC")
                dvcc_enabled = await dvcc_iface.call_get_enabled()
                max_charge_current = await dvcc_iface.call_get_max_charge_current()
                max_discharge_current = await dvcc_iface.call_get_max_discharge_current()
            except Exception:
                pass

            return BatteryState(
                soc=float(soc),
                voltage=float(voltage),
                current=float(current),
                power=float(power),
                temperature=float(temperature) if temperature else None,
                status=str(status).lower(),  # type: ignore[arg-type]
                dvcc_enabled=bool(dvcc_enabled),
                max_charge_current=float(max_charge_current) if max_charge_current else None,
                max_discharge_current=float(max_discharge_current)
                if max_discharge_current
                else None,
            )
        except Exception as e:
            logger.exception(f"Failed to get battery state: {e}")
            return None

    async def set_inverter_external_control(self, enabled: bool) -> bool:
        """Enable/disable external control on inverter."""
        if not self.bus:
            await self.connect()

        try:
            if not self.bus:
                return False
            proxy = await self.bus.get_proxy_object(  # type: ignore[misc]
                self.INVERTER_PREFIX + "ttyO1",
                "/",
                introspection=None,  # type: ignore[arg-type]
            )
            inverter_iface = proxy.get_interface("com.victronenergy.VEBus")
            await inverter_iface.call_set_external_control(enabled)
            logger.info(f"Set inverter external control: {enabled}")
            return True
        except Exception as e:
            logger.exception(f"Failed to set external control: {e}")
            return False

    async def set_power_setpoint(self, power: int) -> bool:
        """Set inverter power setpoint (watts, positive = discharge to grid)."""
        if not self.bus:
            await self.connect()

        try:
            if not self.bus:
                return False
            proxy = await self.bus.get_proxy_object(  # type: ignore[misc]
                self.INVERTER_PREFIX + "ttyO1",
                "/",
                introspection=None,  # type: ignore[arg-type]
            )
            inverter_iface = proxy.get_interface("com.victronenergy.VEBus")
            await inverter_iface.call_set_power_setpoint(power)
            logger.info(f"Set power setpoint: {power}W")
            return True
        except Exception as e:
            logger.exception(f"Failed to set power setpoint: {e}")
            return False

    async def set_soc_limit(self, min_soc: int, max_soc: int) -> bool:
        """Set battery SOC limits via DVCC."""
        if not self.bus:
            await self.connect()

        try:
            if not self.bus:
                return False
            proxy = await self.bus.get_proxy_object(  # type: ignore[misc]
                self.DVCC,
                "/",
                introspection=None,  # type: ignore[arg-type]
            )
            dvcc_iface = proxy.get_interface("com.victronenergy.DVCC")
            await dvcc_iface.call_set_soc_limit(min_soc, max_soc)
            logger.info(f"Set SOC limits: min={min_soc}%, max={max_soc}%")
            return True
        except Exception as e:
            logger.exception(f"Failed to set SOC limits: {e}")
            return False

    async def close(self) -> None:
        """Close D-Bus connection."""
        if self.bus:
            self.bus.disconnect()
            self.bus = None


class DbusMonitor:
    """Monitor D-Bus for battery/inverter state changes."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        dbus_client: VenusDBusClient,
        poll_interval: float = 5.0,
    ):
        self.policy_engine = policy_engine
        self.dbus_client = dbus_client
        self.poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start monitoring."""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("D-Bus monitor started")

    async def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("D-Bus monitor stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                await self._check_battery_state()
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Monitor loop error: {e}")
                await asyncio.sleep(self.poll_interval)

    async def _check_battery_state(self) -> None:
        """Check battery state and evaluate policies."""
        battery_state = await self.dbus_client.get_battery_state()
        if not battery_state:
            return

        # Check discharge rules
        if battery_state.status == "discharging":
            result = self.policy_engine.evaluate(
                InverterAction.DISCHARGE,
                battery_state,
            )
            if not result.allowed and result.approval_required:
                logger.warning(
                    f"Discharge requires approval: {result.reason}, "
                    f"request_id={result.approval_request.id if result.approval_request else 'N/A'}"
                )

        # Check charge rules
        if battery_state.status == "charging":
            result = self.policy_engine.evaluate(
                InverterAction.CHARGE,
                battery_state,
            )
            if not result.allowed and result.approval_required:
                logger.warning(f"Charge requires approval: {result.reason}")

        # Check SOC warnings
        if battery_state.soc <= 30:
            logger.warning(f"Battery SOC low: {battery_state.soc}%")
        if battery_state.soc <= 20:
            self.policy_engine.evaluate(
                InverterAction.DISCHARGE,
                battery_state,
            )
