from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..agent_client import AgentClient, AgentHTTPError
from ..dependencies import get_db
from ..models import Agent
from ..schemas import CellularScanRequest

router = APIRouter(prefix="/api/cellular", tags=["cellular"])


def _get_agent(db: Session, agent_id: int) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


@router.post("/{agent_id}/start")
def cellular_start(agent_id: int, payload: CellularScanRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.cellular_start_scan(payload.dict())
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/{agent_id}/stop")
def cellular_stop(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.cellular_stop_scan()
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{agent_id}/status")
def cellular_status(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.cellular_status()
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{agent_id}/results")
def cellular_results(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.cellular_results()
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
