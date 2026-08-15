"""Tests for the engine module."""

import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from venus_os_governance.engine import (
    ApprovalManager,
    ApprovalRequestParams,
    PolicyEngine,
)
from venus_os_governance.models import (
    ApprovalDecision,
    BatteryState,
    InverterAction,
    Policy,
    PolicyAction,
    PolicyEvaluationResult,
    PolicyRule,
    SOCThreshold,
)


def test_approval_manager_decide_missing_request() -> None:
    """Test deciding on a non-existent request returns False."""
    manager = ApprovalManager()
    decision = ApprovalDecision(
        request_id="non-existent",
        decision="approve",
        decided_by="test",
    )
    assert manager.decide("non-existent", decision) is False


def test_approval_manager_decide_callback_exception(caplog) -> None:
    """Test that exceptions in decision callbacks are logged."""
    manager = ApprovalManager()
    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    params = ApprovalRequestParams(
        policy_id="test",
        rule_id="test",
        inverter_action=InverterAction.CHARGE,
        battery_state=battery_state,
    )
    request = manager.create_request(params)

    def failing_callback(req, dec):
        raise ValueError("Callback failed")

    manager.on_decision(failing_callback)

    decision = ApprovalDecision(
        request_id=request.id,
        decision="approve",
        decided_by="test",
    )

    with caplog.at_level(logging.ERROR):
        manager.decide(request.id, decision)

    assert "Approval callback error" in caplog.text


def test_approval_manager_get_pending() -> None:
    """Test get_pending returns list of pending requests."""
    manager = ApprovalManager()
    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    params = ApprovalRequestParams(
        policy_id="test",
        rule_id="test",
        inverter_action=InverterAction.CHARGE,
        battery_state=battery_state,
    )
    manager.create_request(params)
    manager.create_request(params)
    pending = manager.get_pending()
    assert len(pending) == 2


def test_approval_manager_get_request() -> None:
    """Test get_request returns the correct request or None."""
    manager = ApprovalManager()
    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    params = ApprovalRequestParams(
        policy_id="test",
        rule_id="test",
        inverter_action=InverterAction.CHARGE,
        battery_state=battery_state,
    )
    request = manager.create_request(params)
    assert manager.get_request(request.id) == request
    assert manager.get_request("non-existent") is None


def test_approval_manager_cleanup_expired() -> None:
    """Test cleanup_expired removes and returns expired requests."""
    manager = ApprovalManager()
    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    params = ApprovalRequestParams(
        policy_id="test",
        rule_id="test",
        inverter_action=InverterAction.CHARGE,
        battery_state=battery_state,
        timeout_seconds=-1,  # Expired immediately
    )
    request = manager.create_request(params)
    assert request.id in manager._pending

    escaped = manager.cleanup_expired()
    assert len(escaped) == 1
    assert escaped[0].id == request.id
    assert request.id not in manager._pending
    assert request.status == "expired"


def test_approval_manager_on_decision() -> None:
    """Test on_decision registers a callback."""
    manager = ApprovalManager()
    callback = MagicMock()
    manager.on_decision(callback)
    assert callback in manager._decision_callbacks


def test_policy_engine_no_policy_loaded() -> None:
    """Test evaluate when no policy is loaded returns default allow."""
    engine = PolicyEngine(policy_dir="/non/existent/dir")
    # Override the _default_policy to None to simulate no policy loaded
    engine.default_policy = None
    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    result = engine.evaluate(InverterAction.CHARGE, battery_state)
    assert result.allowed is True
    assert result.action == PolicyAction.ALLOW
    assert result.reason == "No policy loaded"


def test_policy_engine_no_applicable_rules() -> None:
    """Test evaluate when no rules apply returns default action."""
    engine = PolicyEngine()
    # Create a policy with no rules for the given action
    from venus_os_governance.models import Policy, PolicyRule

    policy = Policy(
        id="test",
        name="Test Policy",
        description="Test",
        rules=[
            PolicyRule(
                id="test-rule",
                name="Test Rule",
                description="Test",
                enabled=True,
                priority=10,
                inverter_action=InverterAction.DISCHARGE,  # Not CHARGE
                action=PolicyAction.ALLOW,
                soc_threshold=SOCThreshold(
                    min_soc=20, max_soc=100, critical_min_soc=10, warn_soc=30
                ),
            )
        ],
        default_action=PolicyAction.DENY,
    )
    engine.policies[policy.id] = policy
    engine.default_policy = policy

    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    result = engine.evaluate(InverterAction.CHARGE, battery_state)
    assert result.allowed is False  # default_action is DENY
    assert result.action == PolicyAction.DENY
    assert result.reason == "No applicable rules"


def test_policy_engine_evaluate_with_alerts() -> None:
    """Test evaluate adds alerts when rule action is ALERT."""
    engine = PolicyEngine()
    # Create a policy with an ALERT rule
    from venus_os_governance.models import PolicyRule

    policy = Policy(
        id="test",
        name="Test Policy",
        description="Test",
        rules=[
            PolicyRule(
                id="alert-rule",
                name="Alert Rule",
                description="Test alert",
                enabled=True,
                priority=10,
                inverter_action=InverterAction.CHARGE,
                action=PolicyAction.ALERT,  # This should trigger an alert
            )
        ],
        default_action=PolicyAction.ALLOW,
    )
    engine.policies[policy.id] = policy
    engine.default_policy = policy

    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    result = engine.evaluate(InverterAction.CHARGE, battery_state)
    assert len(result.alerts) == 1
    assert "Alert Rule" in result.alerts[0]


def test_policy_engine_evaluate_with_event_logger() -> None:
    """Test evaluate logs to event logger when configured."""
    event_logger = MagicMock()
    engine = PolicyEngine(event_logger=event_logger)
    # Create a policy that will produce log entries (LOG_ONLY rule)
    from venus_os_governance.models import PolicyRule

    policy = Policy(
        id="test",
        name="Test Policy",
        description="Test",
        rules=[
            PolicyRule(
                id="log-rule",
                name="Log Rule",
                description="Test log",
                enabled=True,
                priority=10,
                inverter_action=InverterAction.CHARGE,
                action=PolicyAction.LOG_ONLY,
                log_details={"message": "Test details"},
            )
        ],
        default_action=PolicyAction.ALLOW,
    )
    engine.policies[policy.id] = policy
    engine.default_policy = policy

    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    engine.evaluate(InverterAction.CHARGE, battery_state)
    assert event_logger.log_event.called
    # Check that the log event was called with the expected data
    args, _ = event_logger.log_event.call_args
    logged_event = args[0]
    assert logged_event["rule_id"] == "log-rule"
    assert logged_event["rule_name"] == "Log Rule"
    assert logged_event["action"] == "log_only"
    assert logged_event["details"] == {"message": "Test details"}


def test_policy_engine_evaluate_rule_custom_condition_exception() -> None:
    """Test _evaluate_rule handles exceptions in custom_condition."""
    engine = PolicyEngine()
    from venus_os_governance.models import BatteryState

    rule = PolicyRule(
        id="test",
        name="Test",
        description="Test",
        enabled=True,
        priority=10,
        inverter_action=InverterAction.CHARGE,
        action=PolicyAction.ALLOW,
        custom_condition="raise ValueError",  # This will cause an exception
    )
    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    # Should return True (the default) when custom_condition fails
    assert engine._evaluate_rule(rule, battery_state, InverterAction.CHARGE, {}) is True


def test_policy_engine_log_to_event_logger() -> None:
    """Test _log_to_event_logger logs entries to event logger."""
    event_logger = MagicMock()
    engine = PolicyEngine(event_logger=event_logger)
    from venus_os_governance.models import BatteryState, InverterAction, PolicyEvaluationResult

    result = PolicyEvaluationResult(
        allowed=True,
        action=PolicyAction.ALLOW,
        log_entries=[
            {
                "rule_id": "rule1",
                "rule_name": "Rule 1",
                "action": "allow",
                "timestamp": "2024-01-01T00:00:00",
                "battery_soc": 50.0,
                "details": "test",
            }
        ],
    )
    action = InverterAction.CHARGE
    battery_state = BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging")
    engine._log_to_event_logger(result, action, battery_state)
    assert event_logger.log_event.called
    args, _ = event_logger.log_event.call_args
    logged_event = args[0]
    assert logged_event["policy_evaluation"] is True
    assert logged_event["inverter_action"] == "charge"
    assert logged_event["battery_soc"] == 50.0
    assert logged_event["battery_status"] == "charging"
    assert logged_event["allowed"]
    assert logged_event["policy_action"] == "allow"


def test_policy_engine_log_to_event_logger_logger_none() -> None:
    """Test _log_to_event_logger returns early when event_logger is None."""
    engine = PolicyEngine(event_logger=None)
    # This should not raise; just return
    result = PolicyEvaluationResult(
        allowed=True,
        action=PolicyAction.ALLOW,
        log_entries=[
            {
                "rule_id": "r1",
                "rule_name": "Rule1",
                "action": "allow",
                "timestamp": "now",
                "battery_soc": 50.0,
                "details": {},
            }
        ],
    )
    # Should not raise any exception
    engine._log_to_event_logger(
        result,
        InverterAction.CHARGE,
        BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging"),
    )


def test_policy_engine_log_to_event_logger_exception(caplog) -> None:
    """Test _log_to_event_logger logs exception when event_logger fails."""
    event_logger = MagicMock()
    event_logger.log_event.side_effect = RuntimeError("DB error")
    engine = PolicyEngine(event_logger=event_logger)
    result = PolicyEvaluationResult(
        allowed=True,
        action=PolicyAction.ALLOW,
        log_entries=[
            {
                "rule_id": "r1",
                "rule_name": "Rule1",
                "action": "allow",
                "timestamp": "now",
                "battery_soc": 50.0,
                "details": {},
            }
        ],
    )
    with caplog.at_level(logging.ERROR):
        engine._log_to_event_logger(
            result,
            InverterAction.CHARGE,
            BatteryState(soc=50.0, voltage=48.0, current=0.0, power=0.0, status="charging"),
        )
    assert "Failed to log to event logger: DB error" in caplog.text


def test_policy_engine_handle_approval_decision() -> None:
    """Test handle_approval_decision delegates to approval manager."""
    approval_manager = MagicMock()
    approval_manager.decide.return_value = True
    engine = PolicyEngine(approval_manager=approval_manager)
    decision = ApprovalDecision(
        request_id="test",
        decision="approve",
        decided_by="test",
    )
    assert engine.handle_approval_decision(decision) is True
    approval_manager.decide.assert_called_once_with("test", decision)


def test_policy_engine_get_pending_approvals() -> None:
    """Test get_pending_approvals calls cleanup_expired and get_pending."""
    approval_manager = MagicMock()
    approval_manager.get_pending.return_value = [MagicMock(), MagicMock()]
    engine = PolicyEngine(approval_manager=approval_manager)
    pending = engine.get_pending_approvals()
    approval_manager.cleanup_expired.assert_called_once()
    approval_manager.get_pending.assert_called_once()
    assert len(pending) == 2


def test_policy_engine_get_policy() -> None:
    """Test get_policy returns the correct policy or None."""

    policy = Policy(
        id="test", name="Test", description="Test", rules=[], default_action=PolicyAction.ALLOW
    )
    engine = PolicyEngine()
    engine.policies["test"] = policy
    assert engine.get_policy("test") == policy
    assert engine.get_policy("non-existent") is None


def test_policy_engine_loads_policies_from_directory(tmp_path) -> None:
    """Test that engine loads policies from a directory containing YAML files."""
    # Create a temporary policy YAML file
    policy_yaml = (
        "id: test-policy\n"
        "name: Test Policy\n"
        "description: A test policy\n"
        'version: "1.0.0"\n'
        "enabled: true\n"
        "rules:\n"
        "  - id: test-rule\n"
        "    name: Test Rule\n"
        "    description: A test rule for charging\n"
        "    enabled: true\n"
        "    priority: 10\n"
        "    inverter_action: charge\n"
        "    action: allow\n"
        "default_action: allow\n"
    )
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    policy_file = policy_dir / "test.yaml"
    policy_file.write_text(policy_yaml.strip())
    # Debug
    print(f"Policy dir: {policy_dir}")
    print(f"Policy file exists: {policy_file.exists()}")
    print(f"Policy file content: {policy_file.read_text()}")
    print(f"Files in policy dir: {list(policy_dir.iterdir())}")

    engine = PolicyEngine(policy_dir=policy_dir)
    # load_policies is called in __init__
    print(f"Engine policies after init: {engine.policies}")
    print(f"Engine default policy: {engine.default_policy}")
    assert len(engine.policies) == 1
    assert "test-policy" in engine.policies
    policy = engine.policies["test-policy"]
    assert policy.id == "test-policy"
    assert policy.name == "Test Policy"
    assert len(policy.rules) == 1
    assert policy.rules[0].id == "test-rule"
    assert engine.default_policy is not None
    assert engine.default_policy.id == "test-policy"


def test_policy_engine_load_policies_warning_when_no_yaml(tmp_path, caplog) -> None:
    """Test that engine logs warning when policy directory exists but contains no YAML files."""
    # Create an empty directory
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    # No YAML files

    with caplog.at_level(logging.WARNING):
        engine = PolicyEngine(policy_dir=policy_dir)
        # load_policies is called in __init__
        # Should log warning about existence but no policies? Actually, warning
        # only if directory does not exist.
        # Let's check: In load_policies, if not
        # self.policy_dir.exists(): logger.warning(...)
        # If directory exists but no YAML files, it will not log warning,
        # but will call _create_default_policies because self._default_policy
        # remains None. So we expect that _create_default_policies is called,
        # which logs an info message.
        # We'll check that default policy is created.
    # Engine should have created default policies because no policies loaded.
    assert len(engine.policies) >= 1
    # At least one default policy should be present
    assert any(p.id == "default-safety" for p in engine.policies.values())
    assert engine.default_policy is not None
    assert engine.default_policy.id == "default-safety"


def test_policy_engine_load_policies_directory_does_not_exist(caplog) -> None:
    """Test that engine logs warning when policy directory does not
    exist and creates default policies."""
    with caplog.at_level(logging.WARNING):
        engine = PolicyEngine(policy_dir="/non/existent/dir")
        # load_policies called in __init__
        expected = "Policy directory /non/existent/dir does not exist"
        assert expected in caplog.text
    # Should have created default policies
    assert len(engine.policies) >= 1
    assert any(p.id == "default-safety" for p in engine.policies.values())
    assert engine.default_policy is not None
    assert engine.default_policy.id == "default-safety"


def test_policy_engine_load_policies_yaml_exception(caplog) -> None:
    """Test that engine logs exception when YAML is malformed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        policy_dir = Path(tmp_dir) / "policies"
        policy_dir.mkdir()
        bad_file = policy_dir / "bad.yaml"
        bad_file.write_text("invalid: [unclosed quote")
        # Only the bad file - no valid policies to load
        with caplog.at_level(logging.ERROR):
            engine = PolicyEngine(policy_dir=policy_dir)
            # load_policies called in __init__
            assert "Failed to load policy" in caplog.text
            assert "bad.yaml" in caplog.text
        # Since no valid policies were loaded, default policies should be created
        assert len(engine.policies) >= 1
        assert any(p.id == "default-safety" for p in engine.policies.values())
        assert engine.default_policy is not None
        assert engine.default_policy.id == "default-safety"
