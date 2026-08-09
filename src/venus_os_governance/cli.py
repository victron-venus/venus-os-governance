"""CLI for Venus OS Governance."""

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import click
import yaml  # type: ignore[import-untyped]
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .dbus_integration import DbusMonitor, VenusDBusClient
from .engine import PolicyEngine
from .event_logger import EventLogger
from .models import (
    ApprovalDecision,
    BatteryState,
    InverterAction,
    Policy,
    PolicyAction,
    PolicyRule,
    SOCThreshold,
)

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class EvaluateParams:
    """Parameters for policy evaluation."""

    action: str
    soc: float
    voltage: float = 48.0
    current: float = 0.0
    power: float = 0.0
    status: str = "idle"


def _evaluate_policy(params: EvaluateParams) -> None:
    """Evaluate policy with given parameters."""
    engine = PolicyEngine()
    engine.load_policies()

    battery_state = BatteryState(
        soc=params.soc,
        voltage=params.voltage,
        current=params.current,
        power=params.power,
        status=params.status,  # type: ignore[arg-type]
    )

    result = engine.evaluate(InverterAction(params.action), battery_state)

    console.print(Panel("[bold]Policy Evaluation Result[/bold]", expand=False))
    console.print(f"Action: {params.action}")
    console.print(f"Battery SOC: {params.soc}%")
    console.print(f"Battery Status: {params.status}")
    console.print(f"Allowed: {'[green]YES[/green]' if result.allowed else '[red]NO[/red]'}")
    console.print(f"Policy Action: {result.action.value}")
    console.print(f"Reason: {result.reason or 'N/A'}")

    if result.matched_rules:
        table = Table(title="Matched Rules")
        table.add_column("Rule ID", style="cyan")
        table.add_column("Name", style="white")
        table.add_column("Action", style="yellow")
        table.add_column("Priority", style="magenta")
        for rule in result.matched_rules:
            table.add_row(rule.id, rule.name, rule.action.value, str(rule.priority))
        console.print(table)

    if result.approval_required and result.approval_request:
        console.print(
            Panel(
                f"[bold]Approval Required[/bold]\n"
                f"Request ID: {result.approval_request.id}\n"
                f"Timeout: {result.approval_request.expires_at}\n"
                f"Roles: {', '.join(result.approval_request.approval_roles)}",
                title="Approval Request",
                border_style="yellow",
            )
        )

    if result.alerts:
        for alert in result.alerts:
            console.print(f"[yellow]ALERT:[/yellow] {alert}")


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, config: str | None) -> None:
    """Venus OS Governance - Policy engine with approval gates."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["config_path"] = config

    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize default configuration and policies."""
    _ = ctx
    config_dir = Path("config")
    policies_dir = config_dir / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)

    policy = Policy(
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
                soc_threshold=SOCThreshold(
                    min_soc=20,
                    max_soc=100,
                    critical_min_soc=10,
                    warn_soc=30,
                ),
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
                soc_threshold=SOCThreshold(
                    min_soc=20,
                    max_soc=100,
                    critical_min_soc=10,
                    warn_soc=30,
                ),
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
                soc_threshold=SOCThreshold(
                    min_soc=20,
                    max_soc=100,
                    critical_min_soc=10,
                    warn_soc=30,
                ),
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

    policy_file = policies_dir / "default-safety.yaml"
    with policy_file.open("w") as f:
        yaml.dump(policy.model_dump(mode="json"), f, sort_keys=False)

    console.print(f"[green]Created default policy:[/green] {policy_file}")
    console.print(f"[green]Config directory:[/green] {config_dir}")


@cli.command()
@click.option("--action", type=click.Choice([a.value for a in InverterAction]), required=True)
@click.option("--soc", type=float, required=True)
@click.option("--voltage", type=float, default=48.0)
@click.option("--current", type=float, default=0.0)
@click.option("--power", type=float, default=0.0)
@click.option(
    "--status",
    type=click.Choice(["charging", "discharging", "idle", "full", "empty"]),
    default="idle",
)
@click.pass_context
def evaluate(
    ctx: click.Context,
    action: str,
    soc: float,
    voltage: float,
    current: float,
    power: float,
    status: str,
) -> None:
    """Evaluate a policy for a given action and battery state."""
    _ = ctx
    params = EvaluateParams(
        action=action, soc=soc, voltage=voltage, current=current, power=power, status=status
    )
    _evaluate_policy(params)


@cli.command()
@click.pass_context
def pending(ctx: click.Context) -> None:
    """List pending approval requests."""
    _ = ctx
    engine = PolicyEngine()
    engine.load_policies()

    requests = engine.get_pending_approvals()

    if not requests:
        console.print("[yellow]No pending approval requests[/yellow]")
        return

    table = Table(title="Pending Approval Requests")
    table.add_column("Request ID", style="cyan")
    table.add_column("Policy", style="white")
    table.add_column("Rule", style="white")
    table.add_column("Action", style="yellow")
    table.add_column("SOC", style="magenta")
    table.add_column("Expires", style="red")
    table.add_column("Roles", style="green")

    for req in requests:
        table.add_row(
            req.id[:8] + "...",
            req.policy_id,
            req.rule_id,
            req.inverter_action.value,
            f"{req.battery_state.soc:.1f}%",
            req.expires_at.strftime("%H:%M:%S"),
            ", ".join(req.approval_roles),
        )

    console.print(table)


@cli.command()
@click.argument("request_id")
@click.option("--decision", type=click.Choice(["approve", "deny"]), required=True)
@click.option("--by", default="cli-user", help="Who is deciding")
@click.option("--reason", default="", help="Reason for decision")
@click.pass_context
def decide(
    ctx: click.Context,
    request_id: str,
    decision: str,
    by: str,
    reason: str,
) -> None:
    """Decide on an approval request."""
    _ = ctx
    engine = PolicyEngine()
    engine.load_policies()

    approval_decision = ApprovalDecision(
        request_id=request_id,
        decision=decision,  # type: ignore[arg-type]
        decided_by=by,
        reason=reason if reason else None,
    )

    success = engine.handle_approval_decision(approval_decision)

    if success:
        console.print(f"[green]Decision recorded: {decision} by {by}[/green]")
    else:
        console.print(f"[red]Failed: Request {request_id} not found or already decided[/red]")
        sys.exit(1)


@cli.command()
@click.option("--host", default="localhost", help="MQTT host for event logger")
@click.option("--port", default=1883, help="MQTT port")
@click.option("--interval", default=5.0, help="Poll interval in seconds")
@click.pass_context
def monitor(ctx: click.Context, host: str, port: int, interval: float) -> None:
    """Start D-Bus monitoring with policy evaluation."""
    _ = ctx

    async def run_monitor() -> None:
        engine = PolicyEngine()
        engine.load_policies()

        _ = EventLogger(mqtt_host=host, mqtt_port=port)
        dbus_client = VenusDBusClient()
        await dbus_client.connect()

        monitor = DbusMonitor(engine, dbus_client, poll_interval=interval)

        console.print("[green]Starting D-Bus monitor...[/green]")
        console.print("Press Ctrl+C to stop")

        try:
            await monitor.start()
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            console.print("\n[yellow]Stopping...[/yellow]")
        finally:
            await monitor.stop()
            await dbus_client.close()

    asyncio.run(run_monitor())


@cli.command()
@click.pass_context
def list_policies(ctx: click.Context) -> None:
    """List all loaded policies."""
    _ = ctx
    engine = PolicyEngine()
    engine.load_policies()

    policies = engine.list_policies()

    if not policies:
        console.print("[yellow]No policies loaded[/yellow]")
        return

    for policy in policies:
        console.print(
            Panel(
                f"[bold]{policy.name}[/bold] ({policy.id})\n"
                f"Version: {policy.version}\n"
                f"Enabled: {policy.enabled}\n"
                f"Rules: {len(policy.rules)}\n"
                f"Default Action: {policy.default_action.value}",
                title=f"Policy: {policy.id}",
                border_style="blue",
            )
        )

        if policy.rules:
            table = Table(title="Rules")
            table.add_column("ID", style="cyan")
            table.add_column("Name", style="white")
            table.add_column("Action", style="yellow")
            table.add_column("Inverter Action", style="magenta")
            table.add_column("Priority", style="green")
            table.add_column("Enabled", style="red")
            for rule in policy.rules:
                table.add_row(
                    rule.id,
                    rule.name,
                    rule.action.value,
                    rule.inverter_action.value if rule.inverter_action else "ANY",
                    str(rule.priority),
                    "✓" if rule.enabled else "✗",
                )
            console.print(table)


@cli.command()
@click.option("--db-path", default="/var/lib/dbus-event-log/events.db", help="Event database path")
@click.option("--limit", default=50, help="Number of events to show")
@click.option(
    "--action",
    type=click.Choice([a.value for a in InverterAction]),
    help="Filter by action",
)
@click.pass_context
def events(
    ctx: click.Context,
    db_path: str,
    limit: int,
    action: str | None,
) -> None:
    """Query governance events from event logger."""
    _ = ctx
    event_logger = EventLogger(db_path=db_path)
    events_list = event_logger.query_events(action=action, limit=limit)

    if not events_list:
        console.print("[yellow]No events found[/yellow]")
        return

    table = Table(title=f"Governance Events (last {limit})")
    table.add_column("Timestamp", style="cyan")
    table.add_column("Type", style="white")
    table.add_column("Policy", style="magenta")
    table.add_column("Rule", style="magenta")
    table.add_column("Action", style="yellow")
    table.add_column("SOC", style="green")
    table.add_column("Allowed", style="red")
    table.add_column("Policy Action", style="blue")

    for event in events_list:
        table.add_row(
            event["timestamp"][:19] if event["timestamp"] else "N/A",
            event["event_type"][:20],
            event["policy_id"] or "N/A",
            event["rule_id"] or "N/A",
            event["inverter_action"] or "N/A",
            f"{event['battery_soc']:.1f}%" if event["battery_soc"] else "N/A",
            "✓" if event["allowed"] else "✗",
            event["policy_action"] or "N/A",
        )

    console.print(table)


if __name__ == "__main__":
    cli()
