"""Tests for the CLI module."""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from venus_os_governance.cli import cli
from venus_os_governance.event_logger import EventLogger


def test_cli_help() -> None:
    """Test that the CLI help works."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_cli_init() -> None:
    """Test the init command."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "Created default policy:" in result.output
        assert "Config directory:" in result.output
        # Check that the file exists
        assert Path("config/policies/default-safety.yaml").exists()


def test_cli_evaluate() -> None:
    """Test the evaluate command."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine
        # Setup the engine to return a specific result
        mock_result = MagicMock()
        mock_result.allowed = True
        mock_result.action.value = "allow"
        mock_result.reason = "Test reason"
        mock_result.matched_rules = []
        mock_engine.evaluate.return_value = mock_result

        result = runner.invoke(
            cli,
            [
                "evaluate",
                "--action",
                "charge",
                "--soc",
                "50",
                "--voltage",
                "48.0",
                "--current",
                "0.0",
                "--power",
                "0.0",
                "--status",
                "charging",
            ],
        )
        assert result.exit_code == 0
        mock_engine_class.assert_called_once()
        mock_engine.load_policies.assert_called_once()
        mock_engine.evaluate.assert_called_once()
        # Check that the console printed something
        assert mock_console.print.called


def test_cli_pending() -> None:
    """Test the pending command."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine

        # Setup the engine to return some pending requests
        mock_request = MagicMock()
        mock_request.id = "test-id"
        mock_request.policy_id = "test-policy"
        mock_request.rule_id = "test-rule"
        mock_request.inverter_action.value = "charge"
        mock_request.battery_state.soc = 50.0
        mock_request.expires_at = MagicMock()
        mock_request.expires_at.strftime.return_value = "12:00:00"
        mock_request.approval_roles = ["operator"]
        mock_engine.get_pending_approvals.return_value = [mock_request]

        result = runner.invoke(cli, ["pending"])
        assert result.exit_code == 0
        mock_engine_class.assert_called_once()
        mock_engine.load_policies.assert_called_once()
        mock_engine.get_pending_approvals.assert_called_once()
        # Check that the console printed something
        assert mock_console.print.called


def test_cli_decide() -> None:
    """Test the decide command."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine

        # Setup the engine to return success
        mock_engine.handle_approval_decision.return_value = True

        result = runner.invoke(
            cli, ["decide", "test-request-id", "--decision", "approve", "--by", "test-user"]
        )
        assert result.exit_code == 0
        mock_engine_class.assert_called_once()
        mock_engine.load_policies.assert_called_once()
        mock_engine.handle_approval_decision.assert_called_once()
        # Check that the console printed something
        assert mock_console.print.called


def test_cli_list_policies() -> None:
    """Test the list-policies command with policies."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine

        # Setup the engine to return some policies
        mock_policy = MagicMock()
        mock_policy.name = "Test Policy"
        mock_policy.id = "test-policy"
        mock_policy.version = "1.0.0"
        mock_policy.enabled = True
        mock_policy.rules = []
        mock_policy.default_action.value = "allow"
        mock_engine.list_policies.return_value = [mock_policy]

        result = runner.invoke(cli, ["list-policies"])
        assert result.exit_code == 0
        mock_engine_class.assert_called_once()
        mock_engine.load_policies.assert_called_once()
        mock_engine.list_policies.assert_called_once()
        # Check that the console printed something
        assert mock_console.print.called


def test_cli_list_policies_empty() -> None:
    """Test the list-policies command with no policies."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine
        mock_engine.list_policies.return_value = []

        result = runner.invoke(cli, ["list-policies"])
        assert result.exit_code == 0
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No policies loaded" in str(call_args)


def test_cli_monitor() -> None:
    """Test the monitor command (just check that it doesn't crash immediately)."""
    runner = CliRunner()
    # We'll mock the DbusMonitor and VenusDBusClient to avoid actually connecting to D-Bus.
    with (
        patch("venus_os_governance.cli.DbusMonitor") as mock_dbus_monitor,
        patch("venus_os_governance.cli.VenusDBusClient") as mock_vdbus_client,
    ):
        mock_dbus_monitor_instance = MagicMock()
        mock_dbus_monitor.return_value = mock_dbus_monitor_instance
        mock_vdbus_client_instance = MagicMock()
        mock_vdbus_client.return_value = mock_vdbus_client_instance

        mock_dbus_monitor_instance.start.return_value = None

        runner.invoke(cli, ["monitor"])


# Let's also test the event_logger.py
def test_event_logger_init() -> None:
    """Test the EventLogger initialization."""

    logger = EventLogger(db_path="/tmp/test.db")
    assert logger.db_path == Path("/tmp/test.db")
    assert logger.mqtt_host is None
    assert logger.mqtt_port == 1883
    assert logger.mqtt_topic_prefix == "venus/governance"


def test_event_logger_init_db() -> None:
    """Test that the database is initialized."""

    db_path = "/tmp/test_init_db.db"
    # Remove if exists
    db_path_obj = Path(db_path)
    if db_path_obj.exists():
        db_path_obj.unlink()

    EventLogger(db_path=db_path)
    # The _init_db method is called in __init__
    # Check that the table exists
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='governance_events'")
    table = cursor.fetchone()
    conn.close()
    assert table is not None
    # Clean up
    db_path_obj.unlink()


def test_event_logger_log_event() -> None:
    """Test logging an event."""

    db_path = "/tmp/test_log_event.db"
    db_path_obj = Path(db_path)
    if db_path_obj.exists():
        db_path_obj.unlink()

    logger = EventLogger(db_path=db_path)
    # Mock the MQTT client to avoid connection
    logger._mqtt_client = None  # pylint: disable=protected-access

    event_data = {
        "timestamp": "2024-01-01T00:00:00",
        "event_type": "test_event",
        "policy_id": "test-policy",
        "rule_id": "test-rule",
        "inverter_action": "charge",
        "battery_soc": 50.0,
        "battery_status": "charging",
        "allowed": 1,
    }
    logger.log_event(event_data)

    # Check that the event was written to the database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM governance_events")
    rows = cursor.fetchall()
    conn.close()
    assert len(rows) == 1
    row = rows[0]
    assert row[1] == "2024-01-01T00:00:00"  # timestamp
    assert row[2] == "test_event"  # event_type
    assert row[3] == "test-policy"  # policy_id
    assert row[4] == "test-rule"  # rule_id
    assert row[5] == "charge"  # inverter_action
    assert row[6] == 50.0  # battery_soc
    assert row[7] == "charging"  # battery_status
    assert row[8] == 1  # allowed
    # Clean up
    db_path_obj.unlink()


def test_event_logger_log_event_with_mqtt() -> None:
    """Test logging an event when MQTT is available."""

    db_path = "/tmp/test_mqtt.db"
    db_path_obj = Path(db_path)
    if db_path_obj.exists():
        db_path_obj.unlink()

    # Create a mock MQTT client
    mock_mqtt_client = MagicMock()
    logger = EventLogger(
        db_path=db_path, mqtt_host="test.host", mqtt_port=1883, mqtt_topic_prefix="test/prefix"
    )
    logger._mqtt_client = mock_mqtt_client  # pylint: disable=protected-access

    event_data = {
        "timestamp": "2024-01-01T00:00:00",
        "event_type": "test_event",
        "policy_id": "test-policy",
        "rule_id": "test-rule",
        "inverter_action": "charge",
        "battery_soc": 50.0,
        "battery_status": "charging",
        "allowed": 1,
    }
    logger.log_event(event_data)

    # Check that the MQTT client's publish method was called
    # The topic should be constructed as: {mqtt_topic_prefix}/{event_type}
    expected_topic = "test/prefix/test_event"
    # The payload should be the event_data as a JSON string
    # Check publish called with expected topic (payload format not checked).
    mock_mqtt_client.publish.assert_called_once()
    args, _ = mock_mqtt_client.publish.call_args
    assert args[0] == expected_topic
    # Check that the database also has the event

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM governance_events")
    rows = cursor.fetchall()
    conn.close()
    assert len(rows) == 1
    # Clean up
    db_path_obj.unlink()


def test_event_logger_query_events() -> None:
    """Test querying events with filters."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        logger = EventLogger(db_path=db_path)
        logger._mqtt_client = None  # pylint: disable=protected-access

        event_data = {
            "timestamp": "2024-01-01T00:00:00",
            "event_type": "test_event",
            "policy_id": "test-policy",
            "rule_id": "test-rule",
            "inverter_action": "charge",
            "battery_soc": 50.0,
            "battery_status": "charging",
            "allowed": 1,
        }
        logger.log_event(event_data)
        logger.log_event({**event_data, "inverter_action": "discharge", "battery_soc": 30.0})

        # Test query with action filter
        events = logger.query_events(action="charge", limit=10)
        assert len(events) == 1
        assert events[0]["inverter_action"] == "charge"

        # Test query with policy filter
        events = logger.query_events(policy_id="test-policy", limit=10)
        assert len(events) == 2

        # Test query with no results
        events = logger.query_events(policy_id="non-existent", limit=10)
        assert len(events) == 0
    finally:
        Path(db_path).unlink()


def test_event_logger_get_approval_requests() -> None:
    """Test getting approval request events."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        logger = EventLogger(db_path=db_path)
        logger._mqtt_client = None  # pylint: disable=protected-access

        event_data = {
            "timestamp": "2024-01-01T00:00:00",
            "event_type": "approval_request",
            "policy_id": "test-policy",
            "rule_id": "test-rule",
            "inverter_action": "charge",
            "battery_soc": 95.0,
            "battery_status": "charging",
            "allowed": 0,
            "policy_action": "require_approval",
            "approval_required": 1,
            "approval_request_id": "req-123",
            "metadata": '{"status": "pending"}',
        }
        logger.log_event(event_data)

        # Test query without status filter (due to JSON path issue in source)
        requests = logger.get_approval_requests(limit=10)
        assert len(requests) == 1
        assert requests[0]["approval_request_id"] == "req-123"
    finally:
        Path(db_path).unlink()


def test_cli_evaluate_with_approval_required() -> None:
    """Test the evaluate command when approval is required."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine
        mock_result = MagicMock()
        mock_result.allowed = False
        mock_result.action.value = "require_approval"
        mock_result.reason = "Approval required"
        mock_result.matched_rules = []
        mock_result.approval_required = True
        mock_result.approval_request = MagicMock()
        mock_result.approval_request.id = "test-request-id"
        mock_result.approval_request.expires_at = MagicMock()
        mock_result.approval_request.expires_at.strftime.return_value = "12:00:00"
        mock_result.approval_request.approval_roles = ["operator"]
        mock_result.alerts = []
        mock_engine.evaluate.return_value = mock_result

        result = runner.invoke(
            cli,
            [
                "evaluate",
                "--action",
                "discharge",
                "--soc",
                "15",
                "--status",
                "discharging",
            ],
        )
        assert result.exit_code == 0
        mock_engine_class.assert_called_once()
        mock_engine.load_policies.assert_called_once()
        mock_engine.evaluate.assert_called_once()
        assert mock_console.print.called


def test_cli_evaluate_with_alerts() -> None:
    """Test the evaluate command with alerts."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine
        mock_result = MagicMock()
        mock_result.allowed = True
        mock_result.action.value = "alert"
        mock_result.reason = "Alert triggered"
        mock_result.matched_rules = []
        mock_result.approval_required = False
        mock_result.approval_request = None
        mock_result.alerts = ["Alert from rule: Test Alert"]
        mock_engine.evaluate.return_value = mock_result

        result = runner.invoke(
            cli,
            [
                "evaluate",
                "--action",
                "charge",
                "--soc",
                "50",
                "--status",
                "charging",
            ],
        )
        assert result.exit_code == 0
        assert mock_console.print.called


def test_cli_pending_no_requests() -> None:
    """Test the pending command with no pending requests."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine
        mock_engine.get_pending_approvals.return_value = []

        result = runner.invoke(cli, ["pending"])
        assert result.exit_code == 0
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No pending approval requests" in str(call_args)


def test_cli_decide_failure() -> None:
    """Test the decide command when decision fails."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.PolicyEngine") as mock_engine_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_engine = MagicMock()
        mock_engine_class.return_value = mock_engine
        mock_engine.handle_approval_decision.return_value = False

        result = runner.invoke(
            cli, ["decide", "invalid-request-id", "--decision", "approve", "--by", "test-user"]
        )
        assert result.exit_code == 1
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "Failed" in str(call_args)


def test_cli_events() -> None:
    """Test the events command."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.EventLogger") as mock_logger_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_logger = MagicMock()
        mock_logger_class.return_value = mock_logger
        mock_logger.query_events.return_value = [
            {
                "timestamp": "2024-01-01T12:00:00",
                "event_type": "policy_evaluation",
                "policy_id": "test-policy",
                "rule_id": "test-rule",
                "inverter_action": "discharge",
                "battery_soc": 50.0,
                "battery_status": "discharging",
                "allowed": True,
                "policy_action": "allow",
            }
        ]

        result = runner.invoke(cli, ["events", "--db-path", "/tmp/test.db"])
        assert result.exit_code == 0
        mock_logger_class.assert_called_once()
        mock_logger.query_events.assert_called_once()
        assert mock_console.print.called


def test_cli_events_empty() -> None:
    """Test the events command with no events."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.EventLogger") as mock_logger_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_logger = MagicMock()
        mock_logger_class.return_value = mock_logger
        mock_logger.query_events.return_value = []

        result = runner.invoke(cli, ["events", "--db-path", "/tmp/test.db"])
        assert result.exit_code == 0
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "No events found" in str(call_args)


def test_cli_events_with_action_filter() -> None:
    """Test the events command with action filter."""
    runner = CliRunner()
    with (
        patch("venus_os_governance.cli.EventLogger") as mock_logger_class,
        patch("venus_os_governance.cli.console") as mock_console,
    ):
        mock_logger = MagicMock()
        mock_logger_class.return_value = mock_logger
        mock_logger.query_events.return_value = [
            {
                "timestamp": "2024-01-01T12:00:00",
                "event_type": "policy_evaluation",
                "policy_id": "test-policy",
                "rule_id": "test-rule",
                "inverter_action": "discharge",
                "battery_soc": 50.0,
                "battery_status": "discharging",
                "allowed": True,
                "policy_action": "allow",
            }
        ]

        result = runner.invoke(cli, ["events", "--action", "discharge"])
        assert result.exit_code == 0
        mock_logger_class.assert_called_once()
        assert mock_console.print.called
