from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..agent_client import AgentClient, AgentHTTPError
from ..dependencies import get_db
from ..models import Agent

router = APIRouter(prefix="/api/spectrum", tags=["spectrum"])


def _get_agent(db: Session, agent_id: int) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.post("/{agent_id}/start")
def start_scan(agent_id: int, band: str = Query("24", pattern="^(24|5)$"), db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.hackrf_start(band)
    except AgentHTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{agent_id}/stop")
def stop_scan(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.hackrf_stop()
    except AgentHTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{agent_id}/status")
def scan_status(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.hackrf_status()
    except AgentHTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{agent_id}/channels")
def channel_data(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.hackrf_channel_data()
    except AgentHTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
