"""
models.py — Zombie Tracker
Pydantic schemas for shipment data validation and API I/O.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel


# --- Enums ---

class CarrierStatus(str, Enum):
    PRE_TRANSIT      = "pre_transit"
    IN_TRANSIT       = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED        = "delivered"
    EXCEPTION        = "exception"
    UNKNOWN          = "unknown"


class RiskScore(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


# --- Core Shipment Model ---

class Shipment(BaseModel):
    tracking_number: str
    carrier:         str
    status:          CarrierStatus
    last_scan_at:    datetime
    customer_email:  str

    class Config:
        from_attributes = True


# --- Alert Output Model ---

class ShipmentAlert(BaseModel):
    case_id:         str
    tracking_number: str
    carrier:         str
    status:          CarrierStatus
    last_scan_at:    datetime
    customer_email:  str
    days_stalled:    int
    is_zombie:       bool
    has_exception:   bool
    risk_score:      RiskScore
    risk_reason:     str


# --- Ingest Response ---

class IngestResponse(BaseModel):
    message:         str
    tracking_number: str
    risk_score:      RiskScore


# --- Ops Events ---

class OpsEventType(str, Enum):
    """The action being recorded on an order."""
    HOLD            = "hold"
    CANCELLED       = "cancelled"
    RELEASED        = "released"
    ADDRESS_UPDATED = "address_updated"


class HoldStatus(str, Enum):
    """Lifecycle state of a hold."""
    OPEN     = "open"      # Hold placed, not yet resolved
    RESOLVED = "resolved"  # Hold closed with a final outcome


class HoldOutcome(str, Enum):
    """Final resolution of a hold — only set when HoldStatus = RESOLVED."""
    CANCELLED       = "cancelled"
    RELEASED        = "released"
    ADDRESS_UPDATED = "address_updated"


class OpsEvent(BaseModel):
    """Structured record of an internal ops team event tied to an order."""
    id:           Optional[int] = None
    order_id:     str
    event_type:   OpsEventType
    note:         str
    created_by:   str
    hold_status:  Optional[HoldStatus] = None   # Set when event_type = HOLD
    outcome:      Optional[HoldOutcome] = None  # Set when hold is resolved
    resolved_by:  Optional[str] = None          # Who resolved it
    resolved_at:  Optional[datetime] = None     # When it was resolved
    created_at:   Optional[datetime] = None


class OpsEventCreate(BaseModel):
    """Input schema for POST /ops-events to open a new hold or log an action."""
    order_id:   str
    event_type: OpsEventType
    note:       str
    created_by: str


class HoldResolve(BaseModel):
    """Input schema for PATCH /ops-events/{id}/resolve."""
    outcome:     HoldOutcome
    resolved_by: str
    note:        Optional[str] = None

# --- Dashboard Response ---

class DashboardKPIs(BaseModel):
    total_shipments:   int
    zombies_detected:  int
    exceptions_active: int
    critical_alerts:   int


class DashboardQueueItem(BaseModel):
    case_id:         str
    tracking_number: str
    carrier:         str
    customer_email:  str
    days_stalled:    int
    risk_score:      str
    risk_reason:     str


class DashboardResponse(BaseModel):
    kpis:               DashboardKPIs
    top_priority_queue: list[DashboardQueueItem]
    risk_distribution:  dict[str, int]