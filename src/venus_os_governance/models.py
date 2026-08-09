"""Core models for Venus OS Governance policy engine."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class PolicyAction(StrEnum):
    """Allowed policy actions."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    LOG_ONLY = "log_only"
    ALERT = "alert"


class InverterAction(StrEnum):
    """Inverter actions that can be controlled."""

    CHARGE = "charge"
    DISCHARGE = "discharge"
    GRID_FEED_IN = "grid_feed_in"
    GRID_FEED_IN_LIMIT = "grid_feed_in_limit"
    AC_CHARGE = "ac_charge"
    AC_DISCHARGE = "ac_discharge"
    EXTERNAL_CONTROL = "external_control"
    SET_POWER_SETPOINT = "set_power_setpoint"
    SET_SOC_LIMIT = "set_soc_limit"


class SOCThreshold(BaseModel):
    """State of Charge threshold configuration."""

    min_soc: int = Field(default=20, ge=0, le=100, description="Minimum SOC percentage")
    max_soc: int = Field(default=100, ge=0, le=100, description="Maximum SOC percentage")
    critical_min_soc: int = Field(
        default=10, ge=0, le=100, description="Critical minimum SOC - emergency stop"
    )
    warn_soc: int = Field(default=30, ge=0, le=100, description="Warning SOC level")

    @field_validator("max_soc")
    @classmethod
    def max_soc_greater_than_min(cls, v: int, info: Any) -> int:
        if "min_soc" in info.data and v <= info.data["min_soc"]:
            raise ValueError("max_soc must be greater than min_soc")
        return v

    @field_validator("critical_min_soc")
    @classmethod
    def critical_less_than_min(cls, v: int, info: Any) -> int:
        if "min_soc" in info.data and v >= info.data["min_soc"]:
            raise ValueError("critical_min_soc must be less than min_soc")
        return v


class BatteryState(BaseModel):
    """Current battery state from D-Bus."""

    soc: float = Field(ge=0, le=100)
    voltage: float
    current: float
    power: float
    temperature: float | None = None
    status: Literal["charging", "discharging", "idle", "full", "empty"]
    timestamp: datetime = Field(default_factory=datetime.now)
    dvcc_enabled: bool = False
    max_charge_current: float | None = None
    max_discharge_current: float | None = None


class PolicyRule(BaseModel):
    """A single policy rule with conditions and actions."""

    id: str
    name: str
    description: str
    enabled: bool = True
    priority: int = Field(default=100, ge=0, description="Lower = higher priority")

    # Conditions
    inverter_action: InverterAction | None = None
    soc_threshold: SOCThreshold | None = None
    soc_condition: Literal["critical", "min", "max"] | None = Field(
        default=None,
        description="Which SOC threshold to check: critical (emergency), min (lower limit), max (upper limit)",
    )
    battery_state_conditions: dict[str, Any] = Field(default_factory=dict)
    time_conditions: dict[str, Any] = Field(default_factory=dict)
    custom_condition: str | None = Field(default=None, description="JMESPath or Python expression")

    # Actions
    action: PolicyAction = PolicyAction.LOG_ONLY
    approval_required: bool = False
    approval_roles: list[str] = Field(default_factory=list)
    approval_timeout_seconds: int = Field(default=300, ge=10)
    alert_channels: list[str] = Field(default_factory=list)
    log_details: dict[str, Any] = Field(default_factory=dict)

    # Metadata
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Policy(BaseModel):
    """Complete policy with multiple rules."""

    id: str
    name: str
    description: str
    version: str = "1.0.0"
    enabled: bool = True
    rules: list[PolicyRule] = Field(default_factory=list)
    default_action: PolicyAction = PolicyAction.ALLOW
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def get_applicable_rules(self, action: InverterAction) -> list[PolicyRule]:
        """Get rules applicable to an action, sorted by priority."""
        applicable = [
            r
            for r in self.rules
            if r.enabled and (r.inverter_action is None or r.inverter_action == action)
        ]
        return sorted(applicable, key=lambda r: r.priority)


class ApprovalRequest(BaseModel):
    """Approval request for policy actions requiring human/integeration approval."""

    id: str
    policy_id: str
    rule_id: str
    inverter_action: InverterAction
    battery_state: BatteryState
    requested_at: datetime = Field(default_factory=datetime.now)
    expires_at: datetime
    status: Literal["pending", "approved", "denied", "expired", "cancelled"] = "pending"
    requested_by: str = "system"
    approved_by: str | None = None
    approved_at: datetime | None = None
    reason: str | None = None
    approval_roles: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    """Decision on an approval request."""

    request_id: str
    decision: Literal["approve", "deny"]
    decided_by: str
    decided_at: datetime = Field(default_factory=datetime.now)
    reason: str | None = None


class PolicyEvaluationResult(BaseModel):
    """Result of evaluating a policy against a request."""

    allowed: bool
    action: PolicyAction
    matched_rules: list[PolicyRule] = Field(default_factory=list)
    approval_required: bool = False
    approval_request: ApprovalRequest | None = None
    alerts: list[str] = Field(default_factory=list)
    log_entries: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
