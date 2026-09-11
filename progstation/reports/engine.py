"""Production reporting (SRS section 13).

Aggregates the production log into daily, weekly and monthly summaries with
PASS/FAIL counts, yield, operator statistics and per-project breakdowns.
Aggregation happens in SQL so a shift report stays fast on a log with hundreds
of thousands of rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..db.database import Database

PERIODS = ("daily", "weekly", "monthly")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def period_bounds(
    period: str, reference: Optional[date] = None, *, day_start_hour: int = 0
) -> Tuple[str, str, str]:
    """Return ``(start_iso, end_iso, label)`` for a reporting period.

    ``day_start_hour`` shifts the day boundary for sites whose first shift
    begins before midnight-relative reporting would be useful.
    """
    reference = reference or date.today()
    if period == "daily":
        start_day, end_day, label = reference, reference + timedelta(days=1), reference.isoformat()
    elif period == "weekly":
        start_day = reference - timedelta(days=reference.weekday())
        end_day = start_day + timedelta(days=7)
        label = f"Week {start_day.isocalendar().week} ({start_day.isoformat()})"
    elif period == "monthly":
        start_day = reference.replace(day=1)
        end_day = (start_day + timedelta(days=32)).replace(day=1)
        label = start_day.strftime("%Y-%m")
    else:
        raise ValueError(f"unknown period '{period}' (expected one of {PERIODS})")

    offset = timedelta(hours=day_start_hour)
    start = datetime.combine(start_day, time.min, tzinfo=timezone.utc) + offset
    end = datetime.combine(end_day, time.min, tzinfo=timezone.utc) + offset
    return _iso(start), _iso(end), label


@dataclass
class Summary:
    label: str
    start: str
    end: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    first_pass_yield: float = 0.0
    avg_cycle_ms: int = 0
    by_operator: List[Dict[str, Any]] = field(default_factory=list)
    by_project: List[Dict[str, Any]] = field(default_factory=list)
    by_error: List[Dict[str, Any]] = field(default_factory=list)
    by_day: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def yield_pct(self) -> float:
        return round(100.0 * self.passed / self.total, 2) if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = dict(vars(self))
        data["yield_pct"] = self.yield_pct
        return data


class ReportEngine:
    def __init__(self, db: Database, *, day_start_hour: int = 0):
        self.db = db
        self.day_start_hour = day_start_hour

    # ------------------------------------------------------------------ core
    def summary(
        self,
        period: str = "daily",
        reference: Optional[date] = None,
        *,
        project_id: Optional[int] = None,
        operator: Optional[str] = None,
    ) -> Summary:
        start, end, label = period_bounds(
            period, reference, day_start_hour=self.day_start_hour
        )
        return self.summary_between(
            start, end, label=label, project_id=project_id, operator=operator
        )

    def summary_between(
        self,
        start: str,
        end: str,
        *,
        label: str = "",
        project_id: Optional[int] = None,
        operator: Optional[str] = None,
    ) -> Summary:
        where = ["Timestamp >= ?", "Timestamp < ?"]
        params: List[Any] = [start, end]
        if project_id is not None:
            where.append("ProjectId = ?")
            params.append(project_id)
        if operator:
            where.append("Operator = ?")
            params.append(operator)
        clause = " WHERE " + " AND ".join(where)

        totals = self.db.query_one(
            "SELECT COUNT(*) AS total,"
            " SUM(Result = 'PASS') AS passed,"
            " SUM(Result = 'FAIL') AS failed,"
            " AVG(DurationMs) AS avg_ms"
            f" FROM ProductionLog{clause}",
            params,
        )
        summary = Summary(label=label or f"{start} .. {end}", start=start, end=end)
        if totals and totals["total"]:
            summary.total = int(totals["total"])
            summary.passed = int(totals["passed"] or 0)
            summary.failed = int(totals["failed"] or 0)
            summary.avg_cycle_ms = int(totals["avg_ms"] or 0)
            summary.first_pass_yield = summary.yield_pct

        summary.by_operator = self._group(
            "Operator", clause, params, extra_label="operator"
        )
        summary.by_project = self._group(
            "ProjectName", clause, params, extra_label="project"
        )
        summary.by_error = [
            dict(row)
            for row in self.db.query(
                "SELECT ErrorCode AS error_code, Error AS message, COUNT(*) AS count"
                f" FROM ProductionLog{clause} AND Result = 'FAIL' AND ErrorCode <> ''"
                " GROUP BY ErrorCode ORDER BY count DESC",
                params,
            )
        ]
        summary.by_day = [
            dict(row)
            for row in self.db.query(
                "SELECT substr(Timestamp, 1, 10) AS day, COUNT(*) AS total,"
                " SUM(Result = 'PASS') AS passed, SUM(Result = 'FAIL') AS failed"
                f" FROM ProductionLog{clause} GROUP BY day ORDER BY day",
                params,
            )
        ]
        for row in summary.by_day:
            row["yield_pct"] = (
                round(100.0 * (row["passed"] or 0) / row["total"], 2) if row["total"] else 0.0
            )
        return summary

    def _group(self, column: str, clause: str, params: List[Any], *, extra_label: str) -> List[Dict[str, Any]]:
        rows = self.db.query(
            f"SELECT {column} AS name, COUNT(*) AS total,"
            " SUM(Result = 'PASS') AS passed, SUM(Result = 'FAIL') AS failed,"
            " AVG(DurationMs) AS avg_ms"
            f" FROM ProductionLog{clause} GROUP BY {column} ORDER BY total DESC",
            params,
        )
        out = []
        for row in rows:
            total = int(row["total"])
            passed = int(row["passed"] or 0)
            out.append(
                {
                    extra_label: row["name"] or "(unassigned)",
                    "total": total,
                    "passed": passed,
                    "failed": int(row["failed"] or 0),
                    "yield_pct": round(100.0 * passed / total, 2) if total else 0.0,
                    "avg_cycle_ms": int(row["avg_ms"] or 0),
                }
            )
        return out

    # ------------------------------------------------------------- shortcuts
    def daily(self, reference: Optional[date] = None, **kw) -> Summary:
        return self.summary("daily", reference, **kw)

    def weekly(self, reference: Optional[date] = None, **kw) -> Summary:
        return self.summary("weekly", reference, **kw)

    def monthly(self, reference: Optional[date] = None, **kw) -> Summary:
        return self.summary("monthly", reference, **kw)

    def as_text(self, summary: Summary) -> str:
        """Plain-text rendering, used by the CLI and the on-screen report view."""
        lines = [
            f"Production summary - {summary.label}",
            f"  window       : {summary.start} .. {summary.end}",
            f"  programmed   : {summary.total}",
            f"  PASS / FAIL  : {summary.passed} / {summary.failed}",
            f"  yield        : {summary.yield_pct:.2f} %",
            f"  avg cycle    : {summary.avg_cycle_ms / 1000.0:.2f} s",
        ]
        if summary.by_project:
            lines.append("  by project:")
            for row in summary.by_project:
                lines.append(
                    f"    {row['project']:<24} {row['passed']:>6} pass"
                    f" {row['failed']:>6} fail  {row['yield_pct']:>6.2f} %"
                )
        if summary.by_operator:
            lines.append("  by operator:")
            for row in summary.by_operator:
                lines.append(
                    f"    {row['operator']:<24} {row['passed']:>6} pass"
                    f" {row['failed']:>6} fail  {row['yield_pct']:>6.2f} %"
                )
        if summary.by_error:
            lines.append("  failures:")
            for row in summary.by_error:
                lines.append(f"    {row['error_code']:<24} {row['count']:>6}")
        return "\n".join(lines)
