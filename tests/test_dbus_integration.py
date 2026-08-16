"""Tests for D-Bus integration module."""

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from _pytest.logging import LogCaptureFixture

from venus_os_governance.dbus_integration import DbusMonitor, VenusDBusClient
from venus_os_governance.engine import PolicyEngine
from venus_os_governance.models import BatteryState


class TestVenusDBusClient:
    """Test VenusDBusClient class."""

    @pytest.fixture
    def client(self) -> VenusDBusClient:
        """Create a client instance."""
        return VenusDBusClient()

    @pytest.mark.asyncio
    async def test_connect(self, client: VenusDBusClient) -> None:
        """Test connecting to D-Bus system bus."""
        with patch("venus_os_governance.dbus_integration.MessageBusT") as mock_bus_class:
            mock_bus = AsyncMock()
            mock_bus_class.return_value = mock_bus
            mock_bus.connect = AsyncMock(return_value=mock_bus)

            await client.connect()

            mock_bus_class.assert_called_once()
            mock_bus.connect.assert_awaited_once()
            assert client.bus == mock_bus

    @pytest.mark.asyncio
    async def test_connect_already_connected(self, client: VenusDBusClient) -> None:
        """Test connect when already connected."""
        mock_bus = AsyncMock()
        client.bus = mock_bus

        with patch("venus_os_governance.dbus_integration.MessageBusT") as mock_bus_class:
            await client.connect()
            mock_bus_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_discover_battery_found(self, client: VenusDBusClient) -> None:
        """Test discovering battery service when one exists."""
        mock_bus = AsyncMock()
        mock_reply = MagicMock()
        mock_reply.body = [["com.victronenergy.battery.ttyS0", "other.service"]]
        mock_bus.call = AsyncMock(return_value=mock_reply)
        client.bus = mock_bus

        with patch("venus_os_governance.dbus_integration.Message"):
            result = await client.discover_battery()

        assert result == "com.victronenergy.battery.ttyS0"
        assert client._battery_path == "com.victronenergy.battery.ttyS0"

    @pytest.mark.asyncio
    async def test_discover_battery_not_found(self, client: VenusDBusClient) -> None:
        """Test discovering battery service when none exists."""
        mock_bus = AsyncMock()
        mock_reply = MagicMock()
        mock_reply.body = [["other.service", "another.service"]]
        mock_bus.call = AsyncMock(return_value=mock_reply)
        client.bus = mock_bus

        with patch("venus_os_governance.dbus_integration.Message"):
            result = await client.discover_battery()

        assert result is None
        assert client._battery_path is None

    @pytest.mark.asyncio
    async def test_discover_battery_no_bus(self, client: VenusDBusClient) -> None:
        """Test discovering battery when bus is None."""
        client.bus = None

        with patch("venus_os_governance.dbus_integration.MessageBusT") as mock_bus_class:
            mock_bus = AsyncMock()
            mock_bus_class.return_value = mock_bus
            mock_bus.connect = AsyncMock(return_value=mock_bus)

            mock_reply = MagicMock()
            mock_reply.body = [["com.victronenergy.battery.ttyS0"]]
            mock_bus.call = AsyncMock(return_value=mock_reply)

            with patch("venus_os_governance.dbus_integration.Message"):
                result = await client.discover_battery()

            assert result == "com.victronenergy.battery.ttyS0"
            assert client.bus == mock_bus

    @pytest.mark.asyncio
    async def test_discover_battery_exception(
        self, client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test discover_battery handles exceptions."""
        mock_bus = AsyncMock()
        mock_bus.call = AsyncMock(side_effect=RuntimeError("DBus error"))
        client.bus = mock_bus

        with (
            patch("venus_os_governance.dbus_integration.Message"),
            caplog.at_level(logging.ERROR),
        ):
            result = await client.discover_battery()

        assert result is None
        assert "Failed to discover battery" in caplog.text

    @pytest.mark.asyncio
    async def test_get_battery_state_success(self, client: VenusDBusClient) -> None:
        """Test getting battery state successfully."""
        mock_bus = AsyncMock()
        client.bus = mock_bus

        # Mock proxy object
        mock_proxy = AsyncMock()
        mock_iface = AsyncMock()
        mock_iface.call_get_soc = AsyncMock(return_value=50)
        mock_iface.call_get_voltage = AsyncMock(return_value=48.0)
        mock_iface.call_get_current = AsyncMock(return_value=10.0)
        mock_iface.call_get_power = AsyncMock(return_value=480.0)
        mock_iface.call_get_status = AsyncMock(return_value="charging")
        mock_iface.call_get_temperature = AsyncMock(return_value=25.0)
        mock_proxy.get_interface = MagicMock(return_value=mock_iface)

        # Mock DVCC proxy
        mock_dvcc_proxy = AsyncMock()
        mock_dvcc_iface = AsyncMock()
        mock_dvcc_iface.call_get_enabled = AsyncMock(return_value=True)
        mock_dvcc_iface.call_get_max_charge_current = AsyncMock(return_value=100)
        mock_dvcc_iface.call_get_max_discharge_current = AsyncMock(return_value=100)
        mock_dvcc_proxy.get_interface = MagicMock(return_value=mock_dvcc_iface)

        mock_bus.get_proxy_object = AsyncMock(side_effect=[mock_proxy, mock_dvcc_proxy])

        # Set battery path
        client._battery_path = "com.victronenergy.battery.ttyS0"

        with patch("venus_os_governance.dbus_integration.MessageBusT"):
            result = await client.get_battery_state()

        assert result is not None
        assert result.soc == 50.0
        assert result.voltage == 48.0
        assert result.current == 10.0
        assert result.power == 480.0
        assert result.status == "charging"
        assert result.temperature == 25.0
        assert result.dvcc_enabled is True
        assert result.max_charge_current == 100.0
        assert result.max_discharge_current == 100.0

    @pytest.mark.asyncio
    async def test_get_battery_state_no_temperature(self, client: VenusDBusClient) -> None:
        """Test getting battery state when temperature is not available."""
        mock_bus = AsyncMock()
        client.bus = mock_bus

        mock_proxy = AsyncMock()
        mock_iface = AsyncMock()
        mock_iface.call_get_soc = AsyncMock(return_value=50)
        mock_iface.call_get_voltage = AsyncMock(return_value=48.0)
        mock_iface.call_get_current = AsyncMock(return_value=10.0)
        mock_iface.call_get_power = AsyncMock(return_value=480.0)
        mock_iface.call_get_status = AsyncMock(return_value="charging")
        mock_iface.call_get_temperature = AsyncMock(side_effect=RuntimeError("Not available"))
        mock_proxy.get_interface = MagicMock(return_value=mock_iface)

        # Mock DVCC proxy
        mock_dvcc_proxy = AsyncMock()
        mock_dvcc_iface = AsyncMock()
        mock_dvcc_iface.call_get_enabled = AsyncMock(return_value=False)
        mock_dvcc_iface.call_get_max_charge_current = AsyncMock(return_value=None)
        mock_dvcc_iface.call_get_max_discharge_current = AsyncMock(return_value=None)
        mock_dvcc_proxy.get_interface = MagicMock(return_value=mock_dvcc_iface)

        mock_bus.get_proxy_object = AsyncMock(side_effect=[mock_proxy, mock_dvcc_proxy])
        client._battery_path = "com.victronenergy.battery.ttyS0"

        with patch("venus_os_governance.dbus_integration.MessageBusT"):
            result = await client.get_battery_state()

        assert result is not None
        assert result.temperature is None
        assert result.dvcc_enabled is False
        assert result.max_charge_current is None
        assert result.max_discharge_current is None

    @pytest.mark.asyncio
    async def test_get_battery_state_no_battery_path(self, client: VenusDBusClient) -> None:
        """Test get_battery_state when no battery path is set."""
        client.bus = None
        client._battery_path = None

        with (
            patch.object(client, "connect", new_callable=AsyncMock),
            patch.object(client, "discover_battery", new_callable=AsyncMock, return_value=None),
        ):
            result = await client.get_battery_state()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_battery_state_exception(
        self, client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test get_battery_state handles exceptions."""
        mock_bus = AsyncMock()
        mock_bus.get_proxy_object = AsyncMock(side_effect=RuntimeError("Proxy error"))
        client.bus = mock_bus
        client._battery_path = "com.victronenergy.battery.ttyS0"

        with (
            patch("venus_os_governance.dbus_integration.MessageBusT"),
            caplog.at_level(logging.ERROR),
        ):
            result = await client.get_battery_state()

        assert result is None
        assert "Failed to get battery state" in caplog.text

    @pytest.mark.asyncio
    async def test_set_inverter_external_control_success(self, client: VenusDBusClient) -> None:
        """Test setting inverter external control successfully."""
        mock_bus = AsyncMock()
        client.bus = mock_bus

        mock_proxy = AsyncMock()
        mock_iface = AsyncMock()
        mock_iface.call_set_external_control = AsyncMock(return_value=None)
        mock_proxy.get_interface = MagicMock(return_value=mock_iface)
        mock_bus.get_proxy_object = AsyncMock(return_value=mock_proxy)

        with patch("venus_os_governance.dbus_integration.MessageBusT"):
            result = await client.set_inverter_external_control(True)

        assert result is True
        mock_iface.call_set_external_control.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_set_inverter_external_control_exception(
        self, client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test set_inverter_external_control handles exceptions."""
        mock_bus = AsyncMock()
        client.bus = mock_bus
        mock_bus.get_proxy_object = AsyncMock(side_effect=RuntimeError("Control error"))

        with (
            patch("venus_os_governance.dbus_integration.MessageBusT"),
            caplog.at_level(logging.ERROR),
        ):
            result = await client.set_inverter_external_control(True)

        assert result is False
        assert "Failed to set external control" in caplog.text

    @pytest.mark.asyncio
    async def test_set_power_setpoint_success(self, client: VenusDBusClient) -> None:
        """Test setting power setpoint successfully."""
        mock_bus = AsyncMock()
        client.bus = mock_bus

        mock_proxy = AsyncMock()
        mock_iface = AsyncMock()
        mock_iface.call_set_power_setpoint = AsyncMock(return_value=None)
        mock_proxy.get_interface = MagicMock(return_value=mock_iface)
        mock_bus.get_proxy_object = AsyncMock(return_value=mock_proxy)

        with patch("venus_os_governance.dbus_integration.MessageBusT"):
            result = await client.set_power_setpoint(1000)

        assert result is True
        mock_iface.call_set_power_setpoint.assert_awaited_once_with(1000)

    @pytest.mark.asyncio
    async def test_set_power_setpoint_exception(
        self, client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test set_power_setpoint handles exceptions."""
        mock_bus = AsyncMock()
        client.bus = mock_bus
        mock_bus.get_proxy_object = AsyncMock(side_effect=RuntimeError("Power error"))

        with (
            patch("venus_os_governance.dbus_integration.MessageBusT"),
            caplog.at_level(logging.ERROR),
        ):
            result = await client.set_power_setpoint(1000)

        assert result is False
        assert "Failed to set power setpoint" in caplog.text

    @pytest.mark.asyncio
    async def test_set_soc_limit_success(self, client: VenusDBusClient) -> None:
        """Test setting SOC limits successfully."""
        mock_bus = AsyncMock()
        client.bus = mock_bus

        mock_proxy = AsyncMock()
        mock_iface = AsyncMock()
        mock_iface.call_set_soc_limit = AsyncMock(return_value=None)
        mock_proxy.get_interface = MagicMock(return_value=mock_iface)
        mock_bus.get_proxy_object = AsyncMock(return_value=mock_proxy)

        with patch("venus_os_governance.dbus_integration.MessageBusT"):
            result = await client.set_soc_limit(20, 80)

        assert result is True
        mock_iface.call_set_soc_limit.assert_awaited_once_with(20, 80)

    @pytest.mark.asyncio
    async def test_set_soc_limit_exception(
        self, client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test set_soc_limit handles exceptions."""
        mock_bus = AsyncMock()
        client.bus = mock_bus
        mock_bus.get_proxy_object = AsyncMock(side_effect=RuntimeError("SOC error"))

        with (
            patch("venus_os_governance.dbus_integration.MessageBusT"),
            caplog.at_level(logging.ERROR),
        ):
            result = await client.set_soc_limit(20, 80)

        assert result is False
        assert "Failed to set SOC limits" in caplog.text

    @pytest.mark.asyncio
    async def test_close(self, client: VenusDBusClient) -> None:
        """Test closing D-Bus connection."""
        mock_bus = AsyncMock()
        client.bus = mock_bus

        await client.close()

        mock_bus.disconnect.assert_called_once()
        assert client.bus is None

    @pytest.mark.asyncio
    async def test_close_no_bus(self, client: VenusDBusClient) -> None:
        """Test closing when no bus is connected."""
        client.bus = None
        await client.close()


class TestDbusMonitor:
    """Test DbusMonitor class."""

    @pytest.fixture
    def engine(self) -> PolicyEngine:
        """Create a policy engine."""
        return PolicyEngine()

    @pytest.fixture
    def dbus_client(self) -> VenusDBusClient:
        """Create a D-Bus client mock."""
        return MagicMock(spec=VenusDBusClient)

    @pytest.fixture
    def monitor(self, engine: PolicyEngine, dbus_client: VenusDBusClient) -> DbusMonitor:
        """Create a DbusMonitor instance."""
        return DbusMonitor(engine, dbus_client, poll_interval=0.1)

    @pytest.mark.asyncio
    async def test_start(self, monitor: DbusMonitor) -> None:
        """Test starting the monitor."""
        await monitor.start()
        assert monitor._running is True
        assert monitor._task is not None
        assert not monitor._task.done()

    @pytest.mark.asyncio
    async def test_stop(self, monitor: DbusMonitor) -> None:
        """Test stopping the monitor."""
        await monitor.start()
        assert monitor._running is True

        await monitor.stop()
        assert monitor._running is False
        assert monitor._task is not None  # type: ignore[unreachable]

    @pytest.mark.asyncio
    async def test_stop_not_running(self, monitor: DbusMonitor) -> None:
        """Test stopping when not running."""
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_check_battery_state_discharge_requires_approval(
        self, monitor: DbusMonitor, dbus_client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test _check_battery_state when discharge requires approval."""
        battery_state = BatteryState(
            soc=15.0, voltage=48.0, current=-10.0, power=-480.0, status="discharging"
        )
        dbus_client.get_battery_state = AsyncMock(return_value=battery_state)

        with caplog.at_level(logging.WARNING):
            await monitor._check_battery_state()

        assert "Discharge requires approval" in caplog.text

    @pytest.mark.asyncio
    async def test_check_battery_state_charge_requires_approval(
        self, monitor: DbusMonitor, dbus_client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test _check_battery_state when charge requires approval."""
        from venus_os_governance.models import (
            InverterAction,
            Policy,
            PolicyAction,
            PolicyRule,
            SOCThreshold,
        )

        policy = Policy(
            id="test",
            name="Test",
            description="Test",
            rules=[
                PolicyRule(
                    id="charge-approval",
                    name="Charge Approval",
                    description="Test",
                    enabled=True,
                    priority=10,
                    inverter_action=InverterAction.CHARGE,
                    soc_threshold=SOCThreshold(
                        min_soc=20, max_soc=100, critical_min_soc=10, warn_soc=30
                    ),
                    soc_condition="max",
                    action=PolicyAction.REQUIRE_APPROVAL,
                    approval_required=True,
                    approval_roles=["operator"],
                )
            ],
            default_action=PolicyAction.ALLOW,
        )
        monitor.policy_engine.policies[policy.id] = policy
        monitor.policy_engine.default_policy = policy

        battery_state = BatteryState(
            soc=100.0, voltage=48.0, current=10.0, power=480.0, status="charging"
        )
        dbus_client.get_battery_state = AsyncMock(return_value=battery_state)

        with caplog.at_level(logging.WARNING):
            await monitor._check_battery_state()

        assert "Charge requires approval" in caplog.text

    @pytest.mark.asyncio
    async def test_check_battery_state_soc_low_warnings(
        self, monitor: DbusMonitor, dbus_client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test _check_battery_state logs warning at low SOC."""
        battery_state = BatteryState(
            soc=25.0, voltage=48.0, current=-10.0, power=-480.0, status="discharging"
        )
        dbus_client.get_battery_state = AsyncMock(return_value=battery_state)

        with caplog.at_level(logging.WARNING):
            await monitor._check_battery_state()

        assert "Battery SOC low: 25.0%" in caplog.text

    @pytest.mark.asyncio
    async def test_check_battery_state_critical_soc(
        self, monitor: DbusMonitor, dbus_client: VenusDBusClient
    ) -> None:
        """Test _check_battery_state evaluates discharge at critical SOC."""
        battery_state = BatteryState(
            soc=15.0, voltage=48.0, current=-10.0, power=-480.0, status="discharging"
        )
        dbus_client.get_battery_state = AsyncMock(return_value=battery_state)

        await monitor._check_battery_state()

    @pytest.mark.asyncio
    async def test_check_battery_state_no_battery(
        self, monitor: DbusMonitor, dbus_client: VenusDBusClient
    ) -> None:
        """Test _check_battery_state when no battery state available."""
        dbus_client.get_battery_state = AsyncMock(return_value=None)

        await monitor._check_battery_state()

    @pytest.mark.asyncio
    async def test_monitor_loop_cancellation(
        self, monitor: DbusMonitor, dbus_client: VenusDBusClient
    ) -> None:
        """Test monitor loop handles cancellation."""
        dbus_client.get_battery_state = AsyncMock(return_value=None)
        await monitor.start()

        monitor._task.cancel()
        try:
            await monitor._task
        except asyncio.CancelledError:
            pass

        await monitor.stop()

    @pytest.mark.asyncio
    async def test_monitor_loop_exception_handling(
        self, monitor: DbusMonitor, dbus_client: VenusDBusClient, caplog: "LogCaptureFixture"
    ) -> None:
        """Test monitor loop handles exceptions and continues."""
        call_count = 0

        async def flaky_get_state() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("DBus error")

        dbus_client.get_battery_state = flaky_get_state
        monitor.poll_interval = 0.01

        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()

        assert "Monitor loop error" in caplog.text
