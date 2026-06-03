"""
Zombie Tracker — Torch Ops
Detects stalled ecommerce shipments and surfaces them as actionable alerts.
"""

import logging
from typing import Optional
from fastapi import FastAPI, HTTPException
from database import init_db, get_all_alerts, save_ops_event, get_ops_events, resolve_hold, get_open_holds, get_hold_dashboard, update_hold, delete_hold
from models import ShipmentAlert, DashboardResponse, DashboardKPIs, DashboardQueueItem, OpsEvent, OpsEventCreate, HoldResolve, HoldUpdate
from fastapi.middleware.cors import CORSMiddleware
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Zombie Tracker",
    description="Detects zombie shipments — packages marked shipped but not moving.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    logger.info("Zombie Tracker started. Database initialised.")


@app.get("/health")
def health():
    return {"status": "ok", "service": "zombie_tracker"}


@app.get("/alerts", response_model=list[ShipmentAlert])
def alerts():
    """
    Return all shipment alerts from SQLite, ordered by risk severity.
    CRITICAL → HIGH → MEDIUM → LOW, then days stalled descending.
    """
    try:
        results = get_all_alerts()
        logger.info("GET /alerts — returned %d shipments", len(results))
        return results
    except Exception as e:
        logger.error("GET /alerts failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve alerts.")


@app.get("/alerts/summary")
def alerts_summary():
    """
    Ops-friendly snapshot. Answers: how bad is it right now?
    Returns counts by risk bucket and the top 3 shipments needing action.
    """
    try:
        all_alerts = get_all_alerts()

        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for a in all_alerts:
            counts[a.risk_score.value] += 1

        needs_action = [
            {
                "case_id":         a.case_id,
                "tracking_number": a.tracking_number,
                "carrier":         a.carrier,
                "customer_email":  a.customer_email,
                "days_stalled":    a.days_stalled,
                "risk_score":      a.risk_score.value.upper(),
                "action":          a.risk_reason,
            }
            for a in all_alerts
            if a.risk_score.value in ("critical", "high")
        ][:3]

        return {
            "total_shipments":   len(all_alerts),
            "zombies_detected":  sum(1 for a in all_alerts if a.is_zombie),
            "exceptions_active": sum(1 for a in all_alerts if a.has_exception),
            "by_risk":           counts,
            "escalate_now":      needs_action,
        }
    except Exception as e:
        logger.error("GET /alerts/summary failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve summary.")


@app.get("/dashboard", response_model=DashboardResponse)
def dashboard():
    """
    Dashboard-ready payload.
    Returns KPI cards, top 5 priority queue, and risk distribution.
    """
    try:
        all_alerts = get_all_alerts()

        risk_distribution = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for a in all_alerts:
            risk_distribution[a.risk_score.value] += 1

        kpis = DashboardKPIs(
            total_shipments   = len(all_alerts),
            zombies_detected  = sum(1 for a in all_alerts if a.is_zombie),
            exceptions_active = sum(1 for a in all_alerts if a.has_exception),
            critical_alerts   = risk_distribution["critical"],
        )

        top_priority_queue = [
            DashboardQueueItem(
                case_id         = a.case_id,
                tracking_number = a.tracking_number,
                carrier         = a.carrier,
                customer_email  = a.customer_email,
                days_stalled    = a.days_stalled,
                risk_score      = a.risk_score.value.upper(),
                risk_reason     = a.risk_reason,
            )
            for a in all_alerts
            if a.risk_score.value in ("critical", "high")
        ][:5]

        logger.info("GET /dashboard — %d total, %d critical", len(all_alerts), kpis.critical_alerts)

        return DashboardResponse(
            kpis               = kpis,
            top_priority_queue = top_priority_queue,
            risk_distribution  = risk_distribution,
        )
    except Exception as e:
        logger.error("GET /dashboard failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve dashboard data.")   

@app.post("/ops-events", response_model=OpsEvent, status_code=201)
def create_ops_event(event: OpsEventCreate):
    """
    Log a new internal ops event for an order.
    Replaces iMessage group chat entries with structured records.

    Example body:
    {
        "order_id": "58291",
        "event_type": "hold",
        "note": "Waiting for customer to confirm address",
        "created_by": "sarah.ops"
    }
    """
    try:
        saved = save_ops_event(event)
        logger.info("POST /ops-events — %s logged for order %s", event.event_type, event.order_id)
        return saved
    except Exception as e:
        logger.error("POST /ops-events failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save ops event.")


@app.get("/ops-events", response_model=list[OpsEvent])
def list_ops_events(order_id: Optional[str] = None): 
    """
    Return all ops events, most recent first.
    Optional query param: ?order_id=58291 to filter by order.
    """
    try:
        events = get_ops_events(order_id=order_id)
        logger.info("GET /ops-events — returned %d events", len(events))
        return events
    except Exception as e:
        logger.error("GET /ops-events failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve ops events.")


@app.get("/ops-events/open-holds", response_model=list[OpsEvent])
def list_open_holds():
    """
    Return all holds currently in OPEN status, oldest first.
    Mirrors the physical hold folder — shows what is still waiting to ship.
    """
    try:
        holds = get_open_holds()
        logger.info("GET /ops-events/open-holds — %d open holds", len(holds))
        return holds
    except Exception as e:
        logger.error("GET /ops-events/open-holds failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to retrieve open holds.")


@app.get("/ops-events/dashboard")
def ops_dashboard():
    """
    Hold operations dashboard.
    Shows current hold queue + today's outcomes.
    """
    try:
        dashboard = get_hold_dashboard()
        logger.info("GET /ops-events/dashboard")
        return dashboard
    except Exception as e:
        logger.error("GET /ops-events/dashboard failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve hold dashboard."
        )


@app.patch("/ops-events/{event_id}/resolve", response_model=OpsEvent)
def resolve_ops_event(event_id: int, resolution: HoldResolve):
    """
    Resolve an open hold with a final outcome.

    Example body:
    {
        "outcome": "released",
        "resolved_by": "sam.ops",
        "note": "Customer confirmed address, ok to ship"
    }

    Outcomes: cancelled | released | address_updated
    """
    try:
        updated = resolve_hold(event_id, resolution)
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"No open hold found with id {event_id}."
            )
        logger.info("PATCH /ops-events/%d/resolve — outcome: %s", event_id, resolution.outcome)
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error("PATCH /ops-events/%d/resolve failed: %s", event_id, e)
        raise HTTPException(status_code=500, detail="Failed to resolve hold.")


@app.patch("/ops-events/{event_id}", response_model=OpsEvent)
def edit_ops_event(event_id: int, updates: HoldUpdate):
    """
    Edit an existing ops event. Only fields included in the body are updated.

    Example body (all fields optional — send only what you want to change):
    {
        "note": "Customer confirmed new address",
        "created_by": "sam.ops"
    }
    """
    try:
        updated = update_hold(event_id, updates)
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"No ops event found with id {event_id}."
            )
        logger.info("PATCH /ops-events/%d — event updated", event_id)
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error("PATCH /ops-events/%d failed: %s", event_id, e)
        raise HTTPException(status_code=500, detail="Failed to update ops event.")


@app.delete("/ops-events/{event_id}")
def delete_ops_event(event_id: int):
    """
    Permanently delete an ops event by id.
    Returns a confirmation message on success, 404 if not found.
    """
    try:
        deleted = delete_hold(event_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"No ops event found with id {event_id}."
            )
        logger.info("DELETE /ops-events/%d — event deleted", event_id)
        return {"message": f"Ops event {event_id} deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("DELETE /ops-events/%d failed: %s", event_id, e)
        raise HTTPException(status_code=500, detail="Failed to delete ops event.")
