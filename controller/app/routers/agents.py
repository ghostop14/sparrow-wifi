from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent_client import AgentClient, AgentHTTPError
from ..dependencies import get_db
from ..models import Agent
from ..schemas import AgentCreate, AgentRead
from ..services import create_agent, refresh_agent_metadata

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=list[AgentRead])
def list_agents(db: Session = Depends(get_db)):
    agents = db.execute(select(Agent)).scalars().all()
    for agent in agents:
        if (
            not agent.interfaces_json
            or not agent.monitor_map_json
            or not agent.capabilities_raw
            or not agent.gps_json
        ):
            refresh_agent_metadata(db, agent)
    return agents


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def register_agent(agent_in: AgentCreate, db: Session = Depends(get_db)):
    agent = create_agent(db, agent_in)
    return agent


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not agent.interfaces_json or not agent.monitor_map_json or not agent.gps_json:
        refresh_agent_metadata(db, agent)
    return agent


@router.get("/{agent_id}/interfaces")
def get_agent_interfaces(agent_id: int, db: Session = Depends(get_db)):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    client = AgentClient(agent)
    try:
        interfaces = client.get_interfaces()
    except AgentHTTPError as err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(err)) from err
    return interfaces


@router.get("/{agent_id}/status")
def get_agent_status(agent_id: int, db: Session = Depends(get_db)):
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    client = AgentClient(agent)
    try:
        wifi = client.get_interfaces()
        bt = client.bluetooth_running()
    except AgentHTTPError as err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(err)) from err
    return {"interfaces": wifi, "bluetooth": bt}
