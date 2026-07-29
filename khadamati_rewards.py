"""Reward campaigns and loyalty progress for Khadamati.

The service deliberately builds on the existing ``campaigns`` table. Campaign
rules remain JSON for backwards compatibility, while eligibility and loyalty
transactions use dedicated tables with uniqueness constraints.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
import sqlite3
from typing import Any

from khadamati_domain import DomainError


REWARD_TYPES = {"draw", "gift", "coupon", "bonus_points", "custom"}
AUDIENCES = {"user", "provider", "company", "all"}
CAMPAIGN_STATUSES = {
    "draft",
    "scheduled",
    "active",
    "paused",
    "completed",
    "cancelled",
}
USER_METRICS = {"completed_requests"}
PROVIDER_METRICS = {
    "completed_jobs",
    "accepted_requests",
    "response_speed",
    "rating",
}
TERMINAL_REQUEST_STATES = ("closed", "completed", "archived")


def install_reward_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS campaign_eligibility(
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL,
          subject_kind TEXT NOT NULL,
          subject_id TEXT NOT NULL,
          progress_value REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending_review',
          eligible_at TEXT DEFAULT '',
          reviewed_at TEXT DEFAULT '',
          reviewed_by TEXT DEFAULT '',
          note TEXT DEFAULT '',
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(campaign_id,subject_kind,subject_id)
        );
        CREATE TABLE IF NOT EXISTS loyalty_transactions(
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          points INTEGER NOT NULL,
          reason TEXT NOT NULL,
          source_key TEXT NOT NULL UNIQUE,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_campaign_eligibility_review
          ON campaign_eligibility(status,eligible_at);
        CREATE INDEX IF NOT EXISTS idx_campaign_eligibility_subject
          ON campaign_eligibility(subject_kind,subject_id,campaign_id);
        CREATE INDEX IF NOT EXISTS idx_loyalty_user
          ON loyalty_transactions(user_id,created_at);
        """
    )
    # Reconstruct legitimate historic points from server-owned records. The
    # source key constraints make this migration idempotent.
    con.execute(
        """
        INSERT OR IGNORE INTO loyalty_transactions(
          id,user_id,points,reason,source_key,created_at
        )
        SELECT 'loy-completed-' || id,user_id,10,'completed_request',
               'completed:' || id,COALESCE(updated_at,created_at)
        FROM customer_requests
        WHERE COALESCE(user_id,'')!=''
          AND status IN ('closed','completed','archived')
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO loyalty_transactions(
          id,user_id,points,reason,source_key,created_at
        )
        SELECT 'loy-review-' || id,user_id,5,'verified_review',
               'review:' || request_id,created_at
        FROM reviews
        WHERE COALESCE(user_id,'')!='' AND COALESCE(request_id,'')!=''
          AND approved=1 AND COALESCE(deleted_at,'')=''
        """
    )


def record_loyalty_transaction(
    con: sqlite3.Connection,
    user_id: str,
    points: int,
    reason: str,
    source_key: str,
    *,
    created_at: str = "",
) -> bool:
    if not user_id or not source_key or int(points) == 0:
        return False
    transaction_id = "loy-" + source_key.replace(":", "-")[:100]
    result = con.execute(
        """
        INSERT OR IGNORE INTO loyalty_transactions(
          id,user_id,points,reason,source_key,created_at
        ) VALUES(?,?,?,?,?,COALESCE(NULLIF(?,''),CURRENT_TIMESTAMP))
        """,
        (
            transaction_id,
            str(user_id)[:120],
            int(points),
            str(reason)[:120],
            str(source_key)[:180],
            str(created_at)[:40],
        ),
    )
    return result.rowcount == 1


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _clean(value: Any, limit: int = 180) -> str:
    return str(value or "").strip()[:limit]


def _boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _number(value: Any, *, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise DomainError("invalid_campaign_target", 400) from error
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise DomainError("invalid_campaign_target", 400)
    return result


def _parse_datetime(value: Any, *, required: bool = False) -> datetime | None:
    text = _clean(value, 50)
    if not text:
        if required:
            raise DomainError("campaign_date_required", 400)
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise DomainError("invalid_campaign_date", 400) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _row_value(row: sqlite3.Row | dict[str, Any], key: str, default: Any = "") -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


class RewardCampaignService:
    def __init__(self, con: sqlite3.Connection, *, now: datetime | None = None):
        self.con = con
        self.now = (now or datetime.now(UTC)).astimezone(UTC)

    def save(self, campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        campaign_id = _clean(campaign_id, 120)
        if not campaign_id:
            raise DomainError("campaign_id_required", 400)
        name_ar = _clean(payload.get("nameAr"), 160)
        name_en = _clean(payload.get("nameEn"), 160)
        if not name_ar or not name_en:
            raise DomainError("campaign_name_required", 400)
        audience = _clean(payload.get("audience") or "user", 20)
        if audience not in AUDIENCES:
            raise DomainError("invalid_campaign_audience", 400)
        reward_type = _clean(payload.get("rewardType") or "custom", 30)
        if reward_type not in REWARD_TYPES:
            raise DomainError("invalid_reward_type", 400)
        status = _clean(payload.get("status") or "draft", 30)
        if status not in CAMPAIGN_STATUSES:
            raise DomainError("invalid_campaign_status", 400)
        metric_default = "completed_requests" if audience == "user" else "completed_jobs"
        metric = _clean(payload.get("metric") or metric_default, 40)
        allowed_metrics = USER_METRICS if audience == "user" else PROVIDER_METRICS
        if audience == "all":
            allowed_metrics = USER_METRICS | PROVIDER_METRICS
        if metric not in allowed_metrics:
            raise DomainError("invalid_campaign_metric", 400)
        target_max = 5 if metric == "rating" else 100_000
        target = _number(payload.get("target", 1), minimum=0.1, maximum=target_max)
        if metric in {"completed_requests", "completed_jobs", "accepted_requests"}:
            target = float(max(1, int(target)))
        starts = _parse_datetime(payload.get("startsAt"))
        ends = _parse_datetime(payload.get("endsAt"))
        if starts and ends and ends <= starts:
            raise DomainError("campaign_end_must_follow_start", 400)
        if status == "scheduled" and not starts:
            raise DomainError("campaign_start_required", 400)
        rules = {
            "descriptionAr": _clean(payload.get("descriptionAr"), 500),
            "descriptionEn": _clean(payload.get("descriptionEn"), 500),
            "rewardType": reward_type,
            "rewardLabelAr": _clean(payload.get("rewardLabelAr"), 180),
            "rewardLabelEn": _clean(payload.get("rewardLabelEn"), 180),
            "audience": audience,
            "target": target,
            "metric": metric,
            "countdownEnabled": _boolean(payload.get("countdownEnabled"), True),
            "imagePath": _clean(payload.get("imagePath"), 500),
            "cycleMode": (
                "repeat" if payload.get("cycleMode") == "repeat" else "cap"
            ),
        }
        budget = _number(
            payload.get("budget", 0), minimum=0, maximum=1_000_000_000
        )
        self.con.execute(
            """
            INSERT INTO campaigns(
              id,name_ar,name_en,kind,starts_at,ends_at,budget,status,rules
            ) VALUES(?,?,?,'reward',?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name_ar=excluded.name_ar,name_en=excluded.name_en,
              kind='reward',starts_at=excluded.starts_at,ends_at=excluded.ends_at,
              budget=excluded.budget,status=excluded.status,rules=excluded.rules,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                campaign_id,
                name_ar,
                name_en,
                _iso(starts),
                _iso(ends),
                budget,
                status,
                _json_dump(rules),
            ),
        )
        return self.get(campaign_id)

    def update_status(self, campaign_id: str, status: str) -> dict[str, Any]:
        if status not in CAMPAIGN_STATUSES:
            raise DomainError("invalid_campaign_status", 400)
        result = self.con.execute(
            """
            UPDATE campaigns SET status=?,updated_at=CURRENT_TIMESTAMP
            WHERE id=? AND kind='reward'
            """,
            (status, campaign_id),
        )
        if result.rowcount != 1:
            raise DomainError("campaign_not_found", 404)
        return self.get(campaign_id)

    def get(self, campaign_id: str) -> dict[str, Any]:
        row = self.con.execute(
            "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        if not row:
            raise DomainError("campaign_not_found", 404)
        return self._serialize(row)

    def list_admin(self) -> list[dict[str, Any]]:
        return [
            self._serialize(row, include_admin=True)
            for row in self.con.execute(
                """
                SELECT * FROM campaigns WHERE kind='reward'
                ORDER BY
                  CASE status WHEN 'active' THEN 0 WHEN 'scheduled' THEN 1
                    WHEN 'paused' THEN 2 WHEN 'draft' THEN 3 ELSE 4 END,
                  updated_at DESC,created_at DESC
                """
            )
        ]

    def for_subject(
        self, subject_kind: str, subject_id: str
    ) -> list[dict[str, Any]]:
        if subject_kind not in {"user", "provider", "company"} or not subject_id:
            return []
        result: list[dict[str, Any]] = []
        for row in self.con.execute(
            """
            SELECT * FROM campaigns
            WHERE kind='reward' AND status IN ('active','scheduled')
            ORDER BY starts_at,created_at
            """
        ):
            item = self._serialize(
                row, subject_kind=subject_kind, subject_id=subject_id
            )
            if item["effectiveStatus"] != "active":
                continue
            if not self._audience_matches(item["audience"], subject_kind):
                continue
            self._remember_eligibility(item, subject_kind, subject_id)
            result.append(item)
        return result

    def eligibility_queue(self) -> list[dict[str, Any]]:
        rows = self.con.execute(
            """
            SELECT ce.*,c.name_ar,c.name_en,c.rules
            FROM campaign_eligibility ce
            JOIN campaigns c ON c.id=ce.campaign_id
            ORDER BY CASE ce.status WHEN 'pending_review' THEN 0 ELSE 1 END,
                     ce.eligible_at DESC,ce.updated_at DESC
            LIMIT 500
            """
        )
        return [
            {
                "id": row["id"],
                "campaignId": row["campaign_id"],
                "campaignNameAr": row["name_ar"],
                "campaignNameEn": row["name_en"],
                "subjectKind": row["subject_kind"],
                "subjectId": row["subject_id"],
                "progressValue": float(row["progress_value"] or 0),
                "status": row["status"],
                "eligibleAt": row["eligible_at"],
                "reviewedAt": row["reviewed_at"],
                "reviewedBy": row["reviewed_by"],
                "note": row["note"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def review_eligibility(
        self,
        eligibility_id: str,
        *,
        status: str,
        reviewed_by: str,
        note: str = "",
    ) -> dict[str, Any]:
        if status not in {"pending_review", "approved", "rejected", "fulfilled"}:
            raise DomainError("invalid_eligibility_status", 400)
        result = self.con.execute(
            """
            UPDATE campaign_eligibility SET status=?,reviewed_by=?,note=?,
              reviewed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (status, _clean(reviewed_by, 120), _clean(note, 500), eligibility_id),
        )
        if result.rowcount != 1:
            raise DomainError("eligibility_not_found", 404)
        row = self.con.execute(
            "SELECT * FROM campaign_eligibility WHERE id=?", (eligibility_id,)
        ).fetchone()
        return dict(row)

    def _serialize(
        self,
        row: sqlite3.Row | dict[str, Any],
        *,
        include_admin: bool = False,
        subject_kind: str = "",
        subject_id: str = "",
    ) -> dict[str, Any]:
        rules = _json_object(_row_value(row, "rules", "{}"))
        starts = _parse_datetime(_row_value(row, "starts_at", ""))
        ends = _parse_datetime(_row_value(row, "ends_at", ""))
        stored_status = str(_row_value(row, "status", "draft") or "draft")
        effective_status = stored_status
        if stored_status in {"active", "scheduled"}:
            if starts and self.now < starts:
                effective_status = "scheduled"
            elif ends and self.now >= ends:
                effective_status = "completed"
            else:
                effective_status = "active"
        audience = str(rules.get("audience") or "user")
        metric = str(
            rules.get("metric")
            or ("completed_requests" if audience == "user" else "completed_jobs")
        )
        target = float(rules.get("target") or 1)
        countdown_seconds = (
            max(0, int((ends - self.now).total_seconds())) if ends else None
        )
        item = {
            "id": _row_value(row, "id"),
            "nameAr": _row_value(row, "name_ar"),
            "nameEn": _row_value(row, "name_en"),
            "kind": _row_value(row, "kind", "reward"),
            "descriptionAr": _clean(rules.get("descriptionAr"), 500),
            "descriptionEn": _clean(rules.get("descriptionEn"), 500),
            "rewardType": str(rules.get("rewardType") or "custom"),
            "rewardLabelAr": _clean(rules.get("rewardLabelAr"), 180),
            "rewardLabelEn": _clean(rules.get("rewardLabelEn"), 180),
            "audience": audience,
            "metric": metric,
            "target": target,
            "countdownEnabled": _boolean(
                rules.get("countdownEnabled"), True
            ),
            "imagePath": _clean(rules.get("imagePath"), 500),
            "cycleMode": (
                "repeat" if rules.get("cycleMode") == "repeat" else "cap"
            ),
            "startsAt": _iso(starts),
            "endsAt": _iso(ends),
            "status": stored_status,
            "effectiveStatus": effective_status,
            "countdownSeconds": countdown_seconds,
            "serverTime": self.now.isoformat(),
            "createdAt": _row_value(row, "created_at"),
            "updatedAt": _row_value(row, "updated_at"),
        }
        if subject_kind and subject_id:
            item.update(
                self._progress(subject_kind, subject_id, metric, target)
            )
        if include_admin:
            counts = self.con.execute(
                """
                SELECT COUNT(*) total,
                  SUM(CASE WHEN status='pending_review' THEN 1 ELSE 0 END) pending
                FROM campaign_eligibility WHERE campaign_id=?
                """,
                (item["id"],),
            ).fetchone()
            item["budget"] = float(_row_value(row, "budget", 0) or 0)
            item["eligibilityCount"] = int(counts["total"] or 0)
            item["pendingEligibilityCount"] = int(counts["pending"] or 0)
            item["rules"] = rules
        return item

    def _progress(
        self, subject_kind: str, subject_id: str, metric: str, target: float
    ) -> dict[str, Any]:
        value = self._metric_value(subject_kind, subject_id, metric)
        if metric == "response_speed":
            eligible = value > 0 and value <= target
            percent = min(100.0, target / value * 100.0) if value > 0 else 0.0
            remaining = max(0.0, value - target)
        else:
            eligible = value >= target
            percent = min(100.0, value / max(target, 0.1) * 100.0)
            remaining = max(0.0, target - value)
        return {
            "progress": round(value, 2),
            "remaining": round(remaining, 2),
            "percent": round(percent, 1),
            "eligible": eligible,
        }

    def _metric_value(
        self, subject_kind: str, subject_id: str, metric: str
    ) -> float:
        if subject_kind == "user":
            if metric != "completed_requests":
                return 0
            row = self.con.execute(
                """
                SELECT COUNT(*) n FROM customer_requests
                WHERE user_id=? AND status IN ('closed','completed','archived')
                """,
                (subject_id,),
            ).fetchone()
            return float(row["n"] or 0)
        if metric == "completed_jobs":
            row = self.con.execute(
                """
                SELECT COUNT(*) n FROM customer_requests
                WHERE accepted_provider_id=?
                  AND status IN ('closed','completed','archived')
                """,
                (subject_id,),
            ).fetchone()
            return float(row["n"] or 0)
        if metric == "accepted_requests":
            row = self.con.execute(
                """
                SELECT COUNT(*) n FROM customer_requests
                WHERE accepted_provider_id=? AND status NOT IN
                  ('cancelled','deleted','matching','unavailable')
                """,
                (subject_id,),
            ).fetchone()
            return float(row["n"] or 0)
        row = self.con.execute(
            "SELECT rating,response_minutes FROM providers WHERE id=?",
            (subject_id,),
        ).fetchone()
        if not row:
            return 0
        if metric == "rating":
            return float(row["rating"] or 0)
        if metric == "response_speed":
            return float(row["response_minutes"] or 0)
        return 0

    @staticmethod
    def _audience_matches(audience: str, subject_kind: str) -> bool:
        if audience == "all":
            return True
        return audience == subject_kind

    def _remember_eligibility(
        self, campaign: dict[str, Any], subject_kind: str, subject_id: str
    ) -> None:
        if not campaign.get("eligible"):
            return
        eligibility_id = (
            f"elig-{campaign['id']}-{subject_kind}-{subject_id}"[:180]
        )
        self.con.execute(
            """
            INSERT INTO campaign_eligibility(
              id,campaign_id,subject_kind,subject_id,progress_value,
              status,eligible_at
            ) VALUES(?,?,?,?,?,'pending_review',?)
            ON CONFLICT(campaign_id,subject_kind,subject_id) DO UPDATE SET
              progress_value=excluded.progress_value,
              eligible_at=CASE
                WHEN campaign_eligibility.eligible_at='' THEN excluded.eligible_at
                ELSE campaign_eligibility.eligible_at END,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                eligibility_id,
                campaign["id"],
                subject_kind,
                subject_id,
                float(campaign.get("progress") or 0),
                self.now.isoformat(),
            ),
        )


def loyalty_summary(
    con: sqlite3.Connection,
    user_id: str,
    *,
    target: int = 8,
    cycle_mode: str = "cap",
) -> dict[str, Any]:
    target = max(1, min(100_000, int(target or 1)))
    completed = int(
        con.execute(
            """
            SELECT COUNT(*) n FROM customer_requests
            WHERE user_id=? AND status IN ('closed','completed','archived')
            """,
            (user_id,),
        ).fetchone()["n"]
        or 0
    )
    points = int(
        con.execute(
            "SELECT COALESCE(SUM(points),0) n FROM loyalty_transactions WHERE user_id=?",
            (user_id,),
        ).fetchone()["n"]
        or 0
    )
    if cycle_mode == "repeat":
        progress = completed % target
        cycles = completed // target
    else:
        progress = min(completed, target)
        cycles = 1 if completed >= target else 0
        cycle_mode = "cap"
    return {
        "points": max(0, points),
        "completedRequests": completed,
        "targetRequests": target,
        "progress": progress,
        "remaining": max(0, target - progress),
        "percent": round(min(100, progress / target * 100), 1),
        "cycleMode": cycle_mode,
        "completedCycles": cycles,
    }
