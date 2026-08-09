"""Policy engine for Venus OS Governance."""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from .models import (
    ApprovalDecision,
    ApprovalRequest,
    BatteryState,
    InverterAction,
    Policy,
    PolicyAction,
    PolicyEvaluationResult,
    PolicyRule,
    SOCThreshold,
)

logger = logging.getLogger(__name__)


@dataclass
class ApprovalRequestParams:
    """Parameters for creating an approval request."""

    policy_id: str
    rule_id: str
    inverter_action: InverterAction
    battery_state: BatteryState
    timeout_seconds: int = 300
    requested_by: str = "system"
    metadata: dict[str, Any] | None = None


class ApprovalManager:
    """Manages approval requests and decisions."""

    def __init__(self) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self._decision_callbacks: list[Callable[[ApprovalRequest, ApprovalDecision], None]] = []

    def create_request(self, params: ApprovalRequestParams) -> ApprovalRequest:
        """Create a new approval request."""
        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            policy_id=params.policy_id,
            rule_id=params.rule_id,
            inverter_action=params.inverter_action,
            battery_state=params.battery_state,
            expires_at=datetime.now() + timedelta(seconds=params.timeout_seconds),
            requested_by=params.requested_by,
            metadata=params.metadata or {},
        )
        self._pending[request.id] = request
        logger.info(f"Created approval request {request.id} for {params.inverter_action.value}")
        return request

    def decide(self, request_id: str, decision: ApprovalDecision) -> bool:
        """Record a decision on an approval request."""
        if request_id not in self._pending:
            return False

        request = self._pending[request_id]
        request.status = "approved" if decision.decision == "approve" else "denied"
        request.approved_by = decision.decided_by
        request.approved_at = decision.decided_at
        request.reason = decision.reason

        del self._pending[request_id]
        self._history.append(request)

        for callback in self._decision_callbacks:
            try:
                callback(request, decision)
            except Exception as e:
                logger.exception(f"Approval callback error: {e}")

        logger.info(f"Approval {request_id} decided: {decision.decision} by {decision.decided_by}")
        return True

    def get_pending(self) -> list[ApprovalRequest]:
        """Get all pending approval requests."""
        return list(self._pending.values())

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """Get a specific approval request."""
        return self._pending.get(request_id)

    def cleanup_expired(self) -> list[ApprovalRequest]:
        """Remove and return expired requests."""
        now = datetime.now()
        expired = [req for req in self._pending.values() if req.expires_at < now]
        for req in expired:
            req.status = "expired"
            del self._pending[req.id]
            self._history.append(req)
        return expired

    def on_decision(self, callback: Callable[[ApprovalRequest, ApprovalDecision], None]) -> None:
        """Register a callback for approval decisions."""
        self._decision_callbacks.append(callback)


class PolicyEngine:
    """Core policy evaluation engine."""

    def __init__(
        self,
        policy_dir: Path | None = None,
        approval_manager: ApprovalManager | None = None,
        event_logger: Any | None = None,
    ):
        self.policy_dir = policy_dir or Path("config/policies")
        self.approval_manager = approval_manager or ApprovalManager()
        self.event_logger = event_logger
        self.policies: dict[str, Policy] = {}
        self._default_policy: Policy | None = None

    def load_policies(self) -> None:
        """Load all policies from policy directory."""
        if not self.policy_dir.exists():
            logger.warning(f"Policy directory {self.policy_dir} does not exist")
            self._create_default_policies()
            return

        for policy_file in self.policy_dir.glob("*.yaml"):
            try:
                with open(policy_file) as f:
                    data = yaml.safe_load(f)
                policy = Policy(**data)
                self.policies[policy.id] = policy
                if self._default_policy is None and policy.enabled:
                    self._default_policy = policy
                logger.info(f"Loaded policy: {policy.id} ({policy.name})")
            except Exception as e:
                logger.exception(f"Failed to load policy {policy_file}: {e}")

        if not self._default_policy:
            self._create_default_policies()

    def _create_default_policies(self) -> None:
        """Create default safety policies."""
        soc_threshold = SOCThreshold(
            min_soc=20,
            max_soc=100,
            critical_min_soc=10,
            warn_soc=30,
        )

        default_policy = Policy(
            id="default-safety",
            name="Default Safety Policy",
            description="Default safety policies for Venus OS inverter control",
            rules=[
                PolicyRule(
                    id="no-discharge-below-20",
                    name="No Discharge Below 20% SOC",
                    description="Prevent battery discharge below 20% SOC without explicit approval",
                    enabled=True,
                    priority=10,
                    inverter_action=InverterAction.DISCHARGE,
                    soc_threshold=soc_threshold,
                    soc_condition="min",
                    action=PolicyAction.REQUIRE_APPROVAL,
                    approval_required=True,
                    approval_roles=["operator", "admin"],
                    approval_timeout_seconds=300,
                    tags=["safety", "soc-limit"],
                ),
                PolicyRule(
                    id="no-charge-above-100",
                    name="No Charge Above 100% SOC",
                    description="Prevent battery charge above 100% SOC",
                    enabled=True,
                    priority=10,
                    inverter_action=InverterAction.CHARGE,
                    soc_threshold=soc_threshold,
                    soc_condition="max",
                    action=PolicyAction.DENY,
                    tags=["safety", "soc-limit"],
                ),
                PolicyRule(
                    id="critical-soc-emergency-stop",
                    name="Critical SOC Emergency Stop",
                    description="Emergency stop all discharge at critical SOC (10%)",
                    enabled=True,
                    priority=1,
                    inverter_action=InverterAction.DISCHARGE,
                    soc_threshold=soc_threshold,
                    soc_condition="critical",
                    action=PolicyAction.DENY,
                    tags=["safety", "emergency"],
                ),
                PolicyRule(
                    id="grid-feed-in-limit",
                    name="Grid Feed-in Limit",
                    description="Require approval for grid feed-in above configured limit",
                    enabled=True,
                    priority=50,
                    inverter_action=InverterAction.GRID_FEED_IN,
                    action=PolicyAction.REQUIRE_APPROVAL,
                    approval_required=True,
                    approval_roles=["operator"],
                    approval_timeout_seconds=600,
                    tags=["grid", "limit"],
                ),
                PolicyRule(
                    id="external-control-gate",
                    name="External Control Gate",
                    description="All external control changes require approval",
                    enabled=True,
                    priority=20,
                    inverter_action=InverterAction.EXTERNAL_CONTROL,
                    action=PolicyAction.REQUIRE_APPROVAL,
                    approval_required=True,
                    approval_roles=["admin"],
                    approval_timeout_seconds=600,
                    tags=["external-control", "security"],
                ),
                PolicyRule(
                    id="log-all-actions",
                    name="Log All Actions",
                    description="Log all inverter actions for audit trail",
                    enabled=True,
                    priority=200,
                    action=PolicyAction.LOG_ONLY,
                    tags=["audit", "logging"],
                ),
            ],
            default_action=PolicyAction.ALLOW,
        )

        self.policies[default_policy.id] = default_policy
        self._default_policy = default_policy
        logger.info("Created default safety policy")

    def evaluate(
        self,
        action: InverterAction,
        battery_state: BatteryState,
        context: dict[str, Any] | None = None,
    ) -> PolicyEvaluationResult:
        """Evaluate an action against loaded policies."""
        if not self._default_policy:
            return PolicyEvaluationResult(
                allowed=True,
                action=PolicyAction.ALLOW,
                reason="No policy loaded",
            )

        policy = self._default_policy
        applicable_rules = policy.get_applicable_rules(action)

        if not applicable_rules:
            return PolicyEvaluationResult(
                allowed=policy.default_action == PolicyAction.ALLOW,
                action=policy.default_action,
                reason="No applicable rules",
            )

        result = PolicyEvaluationResult(
            allowed=policy.default_action == PolicyAction.ALLOW,
            action=policy.default_action,
            metadata=context or {},
        )

        approval_requested = False
        terminal_action_set = False

        for rule in applicable_rules:
            rule_result = self._evaluate_rule(rule, battery_state, action, context)
            if rule_result:
                result.matched_rules.append(rule)

                if rule.action == PolicyAction.DENY:
                    result.allowed = False
                    result.action = PolicyAction.DENY
                    result.reason = f"Denied by rule: {rule.name}"
                    terminal_action_set = True

                if rule.action == PolicyAction.REQUIRE_APPROVAL and not terminal_action_set:
                    if not approval_requested:
                        approval_req = self.approval_manager.create_request(
                            ApprovalRequestParams(
                                policy_id=policy.id,
                                rule_id=rule.id,
                                inverter_action=action,
                                battery_state=battery_state,
                                timeout_seconds=rule.approval_timeout_seconds,
                                metadata={"rule_name": rule.name, **(context or {})},
                            )
                        )
                        result.approval_required = True
                        result.approval_request = approval_req
                        result.allowed = False
                        result.action = PolicyAction.REQUIRE_APPROVAL
                        result.reason = f"Approval required by rule: {rule.name}"
                        approval_requested = True
                        terminal_action_set = True

                elif rule.action == PolicyAction.ALERT:
                    result.alerts.append(f"Alert from rule: {rule.name}")

                # Update action for non-terminal rules (LOG_ONLY, ALERT)
                if not terminal_action_set and rule.action not in (
                    PolicyAction.DENY,
                    PolicyAction.REQUIRE_APPROVAL,
                ):
                    result.action = rule.action

                if rule.action == PolicyAction.LOG_ONLY or rule.log_details:
                    result.log_entries.append(
                        {
                            "rule_id": rule.id,
                            "rule_name": rule.name,
                            "action": rule.action.value,
                            "timestamp": datetime.now().isoformat(),
                            "battery_soc": battery_state.soc,
                            "details": rule.log_details,
                        }
                    )

        if self.event_logger and result.log_entries:
            self._log_to_event_logger(result, action, battery_state)

        return result

    def _evaluate_rule(
        self,
        rule: PolicyRule,
        battery_state: BatteryState,
        action: InverterAction,
        context: dict[str, Any] | None,
    ) -> bool:
        """Evaluate a single rule against battery state."""
        if rule.soc_threshold and rule.soc_condition:
            soc = battery_state.soc
            threshold = rule.soc_threshold

            if action == InverterAction.DISCHARGE:
                if rule.soc_condition == "critical":
                    return soc <= threshold.critical_min_soc
                if rule.soc_condition == "min":
                    return soc <= threshold.min_soc
            if action == InverterAction.CHARGE and rule.soc_condition == "max":
                return soc >= threshold.max_soc

        if rule.custom_condition:
            try:
                # Simple eval context - in production use a safe evaluator
                eval_context = {
                    "battery": battery_state.model_dump(),
                    "action": action.value,
                    "context": context or {},
                }
                return bool(eval(rule.custom_condition, {"__builtins__": {}}, eval_context))
            except Exception as e:
                logger.warning(f"Custom condition eval failed for rule {rule.id}: {e}")

        return True

    def _log_to_event_logger(
        self,
        result: PolicyEvaluationResult,
        action: InverterAction,
        battery_state: BatteryState,
    ) -> None:
        """Log evaluation result to event logger."""
        if not self.event_logger:
            return
        try:
            for entry in result.log_entries:
                entry.update(
                    {
                        "policy_evaluation": True,
                        "inverter_action": action.value,
                        "battery_soc": battery_state.soc,
                        "battery_status": battery_state.status,
                        "allowed": result.allowed,
                        "policy_action": result.action.value,
                    }
                )
                self.event_logger.log_event(entry)
        except Exception as e:
            logger.exception(f"Failed to log to event logger: {e}")

    def handle_approval_decision(self, decision: ApprovalDecision) -> bool:
        """Handle an approval decision."""
        return self.approval_manager.decide(decision.request_id, decision)

    def get_pending_approvals(self) -> list[ApprovalRequest]:
        """Get all pending approval requests."""
        self.approval_manager.cleanup_expired()
        return self.approval_manager.get_pending()

    def get_policy(self, policy_id: str) -> Policy | None:
        """Get a policy by ID."""
        return self.policies.get(policy_id)

    def list_policies(self) -> list[Policy]:
        """List all loaded policies."""
        return list(self.policies.values())
