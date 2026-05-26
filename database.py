"""
database.py — Zombie Tracker
SQLite persistence layer. No ORM — plain Python sqlite3.
Run directly to verify: python database.py
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import (
    CarrierStatus, RiskScore, ShipmentAlert,
    OpsEvent, OpsEventCreate, OpsEventType,
    HoldStatus, HoldOutcome, HoldResolve,
)

logger = logging.getLogger(__name__)

# --- Config ---

DB_PATH = Path("data/zombie_tracker.db")


# --- Connection ---

def get_connection() -> sqlite3.Connection:
    """
    Open and return a SQLite connection.
    Row factory set so results come back as dicts, not plain tuples.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# --- Schema ---

def init_db() -> None:
    """
    Create all tables if they do not already exist.
    Safe to call on every app startup.
    """
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id                INTEGER  PRIMARY KEY AUTOINCREMENT,
                tracking_number   TEXT     NOT NULL UNIQUE,
                carrier           TEXT     NOT NULL,
                status            TEXT     NOT NULL,
                last_scan_at      TEXT     NOT NULL,
                customer_email    TEXT     NOT NULL,
                days_stalled      INTEGER  NOT NULL DEFAULT 0,
                is_zombie         INTEGER  NOT NULL DEFAULT 0,
                has_exception     INTEGER  NOT NULL DEFAULT 0,
                risk_score        TEXT     NOT NULL DEFAULT 'low',
                risk_reason       TEXT     NOT NULL DEFAULT '',
                created_at        TEXT     NOT NULL DEFAULT (datetime('now')),
                updated_at        TEXT     NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        logger.info("Database initialised at %s", DB_PATH)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ops_events (
                id           INTEGER  PRIMARY KEY AUTOINCREMENT,
                order_id     TEXT     NOT NULL,
                event_type   TEXT     NOT NULL,
                note         TEXT     NOT NULL DEFAULT '',
                created_by   TEXT     NOT NULL,
                hold_status  TEXT,
                outcome      TEXT,
                resolved_by  TEXT,
                resolved_at  TEXT,
                created_at   TEXT     NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        logger.info("ops_events table ready.")

    finally:
        conn.close()


# --- Write: Shipments ---

def save_shipment_alert(alert: ShipmentAlert) -> None:
    """
    Insert or update a ShipmentAlert record.
    Uses UPSERT so re-running seed_data.py will not create duplicates.
    """
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO shipments (
                tracking_number,
                carrier,
                status,
                last_scan_at,
                customer_email,
                days_stalled,
                is_zombie,
                has_exception,
                risk_score,
                risk_reason,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tracking_number) DO UPDATE SET
                carrier        = excluded.carrier,
                status         = excluded.status,
                last_scan_at   = excluded.last_scan_at,
                customer_email = excluded.customer_email,
                days_stalled   = excluded.days_stalled,
                is_zombie      = excluded.is_zombie,
                has_exception  = excluded.has_exception,
                risk_score     = excluded.risk_score,
                risk_reason    = excluded.risk_reason,
                updated_at     = datetime('now')
        """, (
            alert.tracking_number,
            alert.carrier,
            alert.status.value,
            alert.last_scan_at.isoformat(),
            alert.customer_email,
            alert.days_stalled,
            int(alert.is_zombie),
            int(alert.has_exception),
            alert.risk_score.value,
            alert.risk_reason,
        ))
        conn.commit()
        logger.info("Saved alert for %s (risk=%s)", alert.tracking_number, alert.risk_score)
    finally:
        conn.close()


# --- Read: Shipments ---

def get_all_alerts() -> list:
    """
    Return all shipment records as ShipmentAlert objects,
    ordered by risk severity then days stalled descending.
    """
    conn = get_connection()
    try:
        cursor = conn.execute("""
            SELECT
                tracking_number,
                carrier,
                status,
                last_scan_at,
                customer_email,
                days_stalled,
                is_zombie,
                has_exception,
                risk_score,
                risk_reason
            FROM shipments
            ORDER BY
                CASE risk_score
                    WHEN 'critical' THEN 1
                    WHEN 'high'     THEN 2
                    WHEN 'medium'   THEN 3
                    WHEN 'low'      THEN 4
                    ELSE                 5
                END ASC,
                days_stalled DESC
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()

    raw = []
    for row in rows:
        raw.append(ShipmentAlert(
            case_id         = "",
            tracking_number = row["tracking_number"],
            carrier         = row["carrier"],
            status          = CarrierStatus(row["status"]),
            last_scan_at    = datetime.fromisoformat(row["last_scan_at"]),
            customer_email  = row["customer_email"],
            days_stalled    = row["days_stalled"],
            is_zombie       = bool(row["is_zombie"]),
            has_exception   = bool(row["has_exception"]),
            risk_score      = RiskScore(row["risk_score"]),
            risk_reason     = row["risk_reason"],
        ))

    alerts = []
    for i, alert in enumerate(raw, start=1):
        alerts.append(alert.model_copy(update={"case_id": f"ZOM-{i:03d}"}))

    return alerts


# --- Internal helper ---

def _row_to_ops_event(row: sqlite3.Row) -> OpsEvent:
    """Convert a raw SQLite row into an OpsEvent model."""
    return OpsEvent(
        id          = row["id"],
        order_id    = row["order_id"],
        event_type  = OpsEventType(row["event_type"]),
        note        = row["note"],
        created_by  = row["created_by"],
        hold_status = HoldStatus(row["hold_status"]) if row["hold_status"] else None,
        outcome     = HoldOutcome(row["outcome"]) if row["outcome"] else None,
        resolved_by = row["resolved_by"],
        resolved_at = datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        created_at  = datetime.fromisoformat(row["created_at"]),
    )


# --- Write: Ops Events ---

def save_ops_event(event: OpsEventCreate) -> OpsEvent:
    """
    Insert a new ops event.
    If event_type is HOLD, automatically sets hold_status to OPEN.
    """
    hold_status = HoldStatus.OPEN.value if event.event_type == OpsEventType.HOLD else None

    conn = get_connection()
    try:
        cursor = conn.execute("""
            INSERT INTO ops_events (order_id, event_type, note, created_by, hold_status)
            VALUES (?, ?, ?, ?, ?)
        """, (
            event.order_id,
            event.event_type.value,
            event.note,
            event.created_by,
            hold_status,
        ))
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ops_events WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        logger.info("Saved ops event %s for order %s", event.event_type, event.order_id)
        return _row_to_ops_event(row)
    finally:
        conn.close()


def resolve_hold(event_id: int, resolution: HoldResolve) -> Optional[OpsEvent]:
    """
    Resolve an open hold by setting outcome, resolved_by, and resolved_at.
    Returns None if the event_id does not exist or is not an open hold.
    """
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE ops_events
            SET
                hold_status = ?,
                outcome     = ?,
                resolved_by = ?,
                resolved_at = datetime('now'),
                note        = CASE WHEN ? IS NOT NULL THEN ? ELSE note END
            WHERE id = ? AND hold_status = 'open'
        """, (
            HoldStatus.RESOLVED.value,
            resolution.outcome.value,
            resolution.resolved_by,
            resolution.note,
            resolution.note,
            event_id,
        ))
        conn.commit()

        row = conn.execute(
            "SELECT * FROM ops_events WHERE id = ?", (event_id,)
        ).fetchone()
        if not row:
            return None
        logger.info("Resolved hold %d — outcome: %s", event_id, resolution.outcome)
        return _row_to_ops_event(row)
    finally:
        conn.close()


# --- Read: Ops Events ---

def get_ops_events(order_id: Optional[str] = None) -> list:
    """
    Return all ops events, optionally filtered by order_id.
    Ordered by most recent first.
    """
    conn = get_connection()
    try:
        if order_id:
            rows = conn.execute(
                "SELECT * FROM ops_events WHERE order_id = ? ORDER BY created_at DESC",
                (order_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ops_events ORDER BY created_at DESC"
            ).fetchall()
    finally:
        conn.close()

    return [_row_to_ops_event(row) for row in rows]


def get_open_holds() -> list:
    """Return all holds currently in OPEN status, oldest first."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM ops_events
            WHERE hold_status = 'open'
            ORDER BY created_at ASC
        """).fetchall()
    finally:
        conn.close()

    return [_row_to_ops_event(row) for row in rows]


# --- Quick verify (run directly) ---

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    init_db()
    alerts = get_all_alerts()

    if not alerts:
        print("\n  No records yet — run seed_data.py first.\n")
    else:
        print(f"\n  {len(alerts)} shipment(s) in database:\n")
        for a in alerts:
            print(f"  [{a.risk_score.value.upper():8}] {a.tracking_number} | "
                  f"{a.carrier} | {a.days_stalled}d stalled | {a.customer_email}")
        print()

def get_hold_dashboard() -> dict:
    """
    Return dashboard metrics for hold operations.
    """
    conn = get_connection()

    try:
        open_holds = conn.execute("""
            SELECT COUNT(*) AS count
            FROM ops_events
            WHERE hold_status = 'open'
        """).fetchone()["count"]

        cancelled_today = conn.execute("""
            SELECT COUNT(*) AS count
            FROM ops_events
            WHERE outcome = 'cancelled'
              AND date(resolved_at) = date('now')
        """).fetchone()["count"]

        released_today = conn.execute("""
            SELECT COUNT(*) AS count
            FROM ops_events
            WHERE outcome = 'released'
              AND date(resolved_at) = date('now')
        """).fetchone()["count"]

        address_updates_today = conn.execute("""
            SELECT COUNT(*) AS count
            FROM ops_events
            WHERE outcome = 'address_updated'
              AND date(resolved_at) = date('now')
        """).fetchone()["count"]

        oldest = conn.execute("""
            SELECT order_id
            FROM ops_events
            WHERE hold_status = 'open'
            ORDER BY created_at ASC
            LIMIT 1
        """).fetchone()

        return {
            "open_holds": open_holds,
            "cancelled_today": cancelled_today,
            "released_today": released_today,
            "address_updates_today": address_updates_today,
            "oldest_open_hold": oldest["order_id"] if oldest else None,
        }

    finally:
        conn.close()