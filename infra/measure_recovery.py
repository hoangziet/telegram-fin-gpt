"""Measure and evaluate achieved RPO and RTO from restore rehearsal timestamps."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any


def parse_utc_timestamp(value: str, label: str) -> datetime:
    """Parse an ISO 8601 string and convert to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is not a valid ISO 8601 timestamp.") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone offset.")
    return parsed.astimezone(timezone.utc)


def format_duration(seconds: int) -> str:
    """Format an integer duration in seconds into human-readable text."""
    if seconds < 0:
        return f"-{format_duration(-seconds)}"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def evaluate_recovery_metrics(
    *,
    backup_completed_at: str,
    incident_occurred_at: str,
    restore_requested_at: str,
    restore_verified_at: str,
    target_rpo_minutes: int | None = None,
    target_rto_minutes: int | None = None,
) -> dict[str, Any]:
    """Calculate measured RPO and RTO and verify whether targets were satisfied."""
    t_backup = parse_utc_timestamp(backup_completed_at, "Backup completed time")
    t_incident = parse_utc_timestamp(incident_occurred_at, "Incident time")
    t_request = parse_utc_timestamp(restore_requested_at, "Restore requested time")
    t_verified = parse_utc_timestamp(restore_verified_at, "Restore verified time")

    if t_incident < t_backup:
        raise ValueError("Incident time cannot precede backup completed time.")
    if t_verified < t_request:
        raise ValueError("Restore verified time cannot precede restore requested time.")

    rpo_seconds = int((t_incident - t_backup).total_seconds())
    rto_seconds = int((t_verified - t_request).total_seconds())

    rpo_met: bool | None = None
    if target_rpo_minutes is not None:
        if target_rpo_minutes <= 0:
            raise ValueError("Target RPO must be greater than zero minutes.")
        rpo_met = rpo_seconds <= (target_rpo_minutes * 60)

    rto_met: bool | None = None
    if target_rto_minutes is not None:
        if target_rto_minutes <= 0:
            raise ValueError("Target RTO must be greater than zero minutes.")
        rto_met = rto_seconds <= (target_rto_minutes * 60)

    return {
        "backup_completed_at": t_backup.isoformat(),
        "incident_occurred_at": t_incident.isoformat(),
        "restore_requested_at": t_request.isoformat(),
        "restore_verified_at": t_verified.isoformat(),
        "measured_rpo_seconds": rpo_seconds,
        "measured_rpo_human": format_duration(rpo_seconds),
        "target_rpo_minutes": target_rpo_minutes,
        "rpo_met": rpo_met,
        "measured_rto_seconds": rto_seconds,
        "measured_rto_human": format_duration(rto_seconds),
        "target_rto_minutes": target_rto_minutes,
        "rto_met": rto_met,
        "recovery_targets_met": (
            (rpo_met is not False and rto_met is not False)
            if (target_rpo_minutes is not None or target_rto_minutes is not None)
            else None
        ),
    }


def format_markdown_summary(metrics: dict[str, Any]) -> str:
    """Format evaluated recovery metrics into sanitized Markdown for run summaries."""
    lines = [
        "### Recovery metrics evaluation (RPO / RTO)",
        "",
        f"- **Backup timestamp (UTC)**: `{metrics['backup_completed_at']}`",
        f"- **Incident / Cutoff timestamp (UTC)**: `{metrics['incident_occurred_at']}`",
        f"- **Restore requested (UTC)**: `{metrics['restore_requested_at']}`",
        f"- **Verification completed (UTC)**: `{metrics['restore_verified_at']}`",
        f"- **Measured data loss (RPO)**: {metrics['measured_rpo_human']} ({metrics['measured_rpo_seconds']}s)",
    ]
    if metrics["target_rpo_minutes"] is not None:
        status = "PASSED" if metrics["rpo_met"] else "EXCEEDED"
        lines.append(f"  - Target: {metrics['target_rpo_minutes']}m | Result: **{status}**")

    lines.append(
        f"- **Measured restore downtime (RTO)**: {metrics['measured_rto_human']} ({metrics['measured_rto_seconds']}s)"
    )
    if metrics["target_rto_minutes"] is not None:
        status = "PASSED" if metrics["rto_met"] else "EXCEEDED"
        lines.append(f"  - Target: {metrics['target_rto_minutes']}m | Result: **{status}**")

    return "\n".join(lines)


def main() -> int:
    """CLI entrypoint for computing and displaying RPO/RTO metrics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-time", required=True, help="Backup completion time (ISO 8601 UTC)")
    parser.add_argument("--incident-time", required=True, help="Disaster or cut-off time (ISO 8601 UTC)")
    parser.add_argument("--request-time", required=True, help="Restore request initiation time (ISO 8601 UTC)")
    parser.add_argument("--verify-time", required=True, help="Restore verification completion time (ISO 8601 UTC)")
    parser.add_argument("--target-rpo-minutes", type=int, default=None, help="Target maximum allowable RPO")
    parser.add_argument("--target-rto-minutes", type=int, default=None, help="Target maximum allowable RTO")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of Markdown")

    args = parser.parse_args()
    try:
        metrics = evaluate_recovery_metrics(
            backup_completed_at=args.backup_time,
            incident_occurred_at=args.incident_time,
            restore_requested_at=args.request_time,
            restore_verified_at=args.verify_time,
            target_rpo_minutes=args.target_rpo_minutes,
            target_rto_minutes=args.target_rto_minutes,
        )
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        print(format_markdown_summary(metrics))

    if metrics["recovery_targets_met"] is False:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
