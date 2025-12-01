from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dependencies import get_db
from ..events import event_bus
from ..models import Agent, PushPayload
from ..schemas import PushIngestRequest, PushRead

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("", response_model=PushRead, status_code=status.HTTP_202_ACCEPTED)
def ingest_payload(payload: PushIngestRequest, request: Request, db: Session = Depends(get_db)):
    if not payload.agent_id and not payload.agent_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="agent_id or agent_name is required")

    agent = None
    if payload.agent_id:
        agent = db.get(Agent, payload.agent_id)
    if not agent and payload.agent_name:
        agent = db.execute(select(Agent).where(Agent.name == payload.agent_name)).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Simple shared-secret check (optional)
    if agent.api_key:
        api_key = request.headers.get("X-API-Key")
        if api_key != agent.api_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    push_row = PushPayload(
        agent_id=agent.id,
        scan_type=payload.scan_type,
        interface=payload.interface,
        source="push",
        received_at=payload.received_at or datetime.utcnow(),
    )
    push_row.set_payload(payload.payload)
    db.add(push_row)
    db.flush()

    event_bus.publish(
        "ingest.received",
        {
            "push_id": push_row.id,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "scan_type": payload.scan_type.value,
            "interface": payload.interface,
            "received_at": push_row.received_at.isoformat(),
            "payload": payload.payload,
        },
    )

    return push_row
