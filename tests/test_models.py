"""Tests for Venus OS Governance."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/Users/vmedvedev/victron/venus-os-governance/src")

from venus_os_governance.engine import ApprovalManager, PolicyEngine
from venus_os_governance.models import (
    BatteryState,
    InverterAction,
    Policy,
    PolicyAction,
    PolicyRule,
    SOCThreshold,
)


class TestModels:
    """Test model validation."""

    def test_soc_threshold_valid(self) -> None:
        """Test valid SOC threshold creation."""
        threshold = SOCThreshold(
            min_soc=20,
            max_soc=100,
            critical_min_soc=10,
            warn_soc=30,
        )
        assert threshold.min_soc == 20
        assert threshold.max_soc == 100

    def test_soc_threshold_invalid_max_less_than_min(self) -> None:
        """Test invalid max_soc less than min_soc."""
        with pytest.raises(ValueError, match="max_soc must be greater than min_soc"):
            SOCThreshold(min_soc=50, max_soc=30)

    def test_soc_threshold_invalid_critical_greater_than_min(self) -> None:
        """Test invalid critical_min_soc greater than min_soc."""
        with pytest.raises(ValueError, match="critical_min_soc must be less than min_soc"):
            SOCThreshold(min_soc=20, critical_min_soc=30)

    def test_battery_state_valid(self) -> None:
        """Test valid battery state creation."""
        state = BatteryState(
            soc=50.0,
            voltage=48.0,
            current=10.0,
            power=480.0,
            status="charging",
        )
        assert state.soc == 50.0
        assert state.status == "charging"

    def test_battery_state_invalid_soc(self) -> None:
        """Test invalid SOC value."""
        with pytest.raises(ValueError, match="Input should be less than or equal to 100"):
            BatteryState(
                soc=150.0,
                voltage=48.0,
                current=10.0,
                power=480.0,
                status="charging",
            )

    def test_policy_rule_creation(self) -> None:
        """Test policy rule creation."""
        rule = PolicyRule(
            id="test-rule",
            name="Test Rule",
            description="A test rule",
            enabled=True,
            priority=10,
            inverter_action=InverterAction.DISCHARGE,
            action=PolicyAction.REQUIRE_APPROVAL,
            approval_required=True,
            approval_roles=["operator"],
            approval_timeout_seconds=300,
        )
        assert rule.id == "test-rule"
        assert rule.action == PolicyAction.REQUIRE_APPROVAL

    def test_policy_creation(self) -> None:
        """Test policy creation."""
        rule = PolicyRule(
            id="test-rule",
            name="Test Rule",
            description="A test rule",
            action=PolicyAction.LOG_ONLY,
        )
        policy = Policy(
            id="test-policy",
            name="Test Policy",
            description="A test policy",
            rules=[rule],
            default_action=PolicyAction.ALLOW,
        )
        assert policy.id == "test-policy"
        assert len(policy.rules) == 1


class TestApprovalManager:
    """Test approval manager."""

    def test_create_request(self) -> None:
        """Test creating approval request."""
        manager = ApprovalManager()
        battery_state = BatteryState(
            soc=15.0,
            voltage=48.0,
            current=-10.0,
            power=-480.0,
            status="discharging",
        )
        from venus_os_governance.engine import ApprovalRequestParams

        params = ApprovalRequestParams(
            policy_id="test-policy",
            rule_id="test-rule",
            inverter_action=InverterAction.DISCHARGE,
            battery_state=battery_state,
            timeout_seconds=300,
        )
        request = manager.create_request(params)
        assert request.id is not None
        assert request.policy_id == "test-policy"
        assert request.rule_id == "test-rule"
        assert request.status == "pending"

    def test_decide_approve(self) -> None:
        """Test approving a request."""
        manager = ApprovalManager()
        battery_state = BatteryState(
            soc=15.0,
            voltage=48.0,
            current=-10.0,
            power=-480.0,
            status="discharging",
        )
        from venus_os_governance.engine import ApprovalRequestParams

        params = ApprovalRequestParams(
            policy_id="test-policy",
            rule_id="test-rule",
            inverter_action=InverterAction.DISCHARGE,
            battery_state=battery_state,
        )
        request = manager.create_request(params)
        from venus_os_governance.models import ApprovalDecision

        decision = ApprovalDecision(
            request_id=request.id,
            decision="approve",
            decided_by="test-user",
        )
        result = manager.decide(request.id, decision)
        assert result is True
        assert request.status == "approved"

    def test_decide_deny(self) -> None:
        """Test denying a request."""
        manager = ApprovalManager()
        battery_state = BatteryState(
            soc=15.0,
            voltage=48.0,
            current=-10.0,
            power=-480.0,
            status="discharging",
        )
        from venus_os_governance.engine import ApprovalRequestParams

        params = ApprovalRequestParams(
            policy_id="test-policy",
            rule_id="test-rule",
            inverter_action=InverterAction.DISCHARGE,
            battery_state=battery_state,
        )
        request = manager.create_request(params)
        from venus_os_governance.models import ApprovalDecision

        decision = ApprovalDecision(
            request_id=request.id,
            decision="deny",
            decided_by="test-user",
        )
        result = manager.decide(request.id, decision)
        assert result is True
        assert request.status == "denied"


class TestPolicyEngine:
    """Test policy engine."""

    def test_default_policy_creation(self) -> None:
        """Test default safety policy creation."""
        engine = PolicyEngine()
        engine.load_policies()
        policies = engine.list_policies()
        assert len(policies) == 1
        assert policies[0].id == "default-safety"
        assert len(policies[0].rules) == 6

    def test_evaluate_discharge_below_20_requires_approval(self) -> None:
        """Test discharge below 20% SOC requires approval."""
        engine = PolicyEngine()
        engine.load_policies()

        battery_state = BatteryState(
            soc=15.0,
            voltage=48.0,
            current=-10.0,
            power=-480.0,
            status="discharging",
        )
        result = engine.evaluate(InverterAction.DISCHARGE, battery_state)
        assert result.allowed is False
        assert result.approval_required is True
        assert result.action == PolicyAction.REQUIRE_APPROVAL

    def test_evaluate_discharge_above_20_allowed(self) -> None:
        """Test discharge above 20% SOC is allowed."""
        engine = PolicyEngine()
        engine.load_policies()

        battery_state = BatteryState(
            soc=50.0,
            voltage=48.0,
            current=-10.0,
            power=-480.0,
            status="discharging",
        )
        result = engine.evaluate(InverterAction.DISCHARGE, battery_state)
        assert result.allowed is True
        assert result.action == PolicyAction.LOG_ONLY

    def test_evaluate_critical_soc_denied(self) -> None:
        """Test discharge at critical SOC (10%) is denied."""
        engine = PolicyEngine()
        engine.load_policies()

        battery_state = BatteryState(
            soc=5.0,
            voltage=48.0,
            current=-10.0,
            power=-480.0,
            status="discharging",
        )
        result = engine.evaluate(InverterAction.DISCHARGE, battery_state)
        assert result.allowed is False
        assert result.action == PolicyAction.DENY

    def test_evaluate_charge_always_allowed(self) -> None:
        """Test charge is allowed (unless above 100%)."""
        engine = PolicyEngine()
        engine.load_policies()

        battery_state = BatteryState(
            soc=80.0,
            voltage=48.0,
            current=10.0,
            power=480.0,
            status="charging",
        )
        result = engine.evaluate(InverterAction.CHARGE, battery_state)
        assert result.allowed is True
        assert result.action == PolicyAction.LOG_ONLY


class TestEventLogger:
    """Test event logger."""

    def test_log_event(self, tmp_path: Path) -> None:
        """Test logging an event."""
        from venus_os_governance.event_logger import EventLogger

        db_path = tmp_path / "test.db"
        logger = EventLogger(db_path=str(db_path))

        event = {
            "event_type": "policy_evaluation",
            "policy_id": "test-policy",
            "rule_id": "test-rule",
            "inverter_action": "discharge",
            "battery_soc": 15.0,
            "battery_status": "discharging",
            "allowed": False,
            "policy_action": "require_approval",
            "approval_required": True,
            "approval_request_id": "test-request-id",
        }
        logger.log_event(event)

        events = logger.query_events(limit=1)
        assert len(events) == 1
        assert events[0]["policy_id"] == "test-policy"
        assert events[0]["battery_soc"] == 15.0