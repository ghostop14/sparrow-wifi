from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..agent_client import AgentClient, AgentHTTPError
from ..dependencies import get_db
from ..models import Agent
from ..schemas import FalconCrackRequest, FalconDeauthRequest, FalconMonitorRequest, FalconScanRequest
from ..services import refresh_agent_metadata

router = APIRouter(prefix="/api/falcon", tags=["falcon"])


def _get_agent(db: Session, agent_id: int) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


@router.post("/{agent_id}/monitor/start")
def falcon_monitor_start(agent_id: int, payload: FalconMonitorRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        response = client.falcon_start_monitor(payload.interface)
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    monitor_map = agent.monitor_map
    alias = response.get('interface') or response.get('monitorinterface') or response.get('monitorInterface')
    if not alias:
        try:
            iface_dict = client.get_interfaces().get('interfaces', {})
            alias = next((name for name in iface_dict.keys() if name != payload.interface and name.endswith('mon')), None)
        except AgentHTTPError:
            alias = None
    if alias:
        monitor_map[payload.interface] = alias
    agent.monitor_map = monitor_map
    refresh_agent_metadata(db, agent)
    db.add(agent)
    db.flush()
    if alias:
        enriched = dict(response)
        enriched['interface'] = alias
        return enriched
    return response


@router.post("/{agent_id}/monitor/stop")
def falcon_monitor_stop(agent_id: int, payload: FalconMonitorRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        response = client.falcon_stop_monitor(payload.interface)
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    monitor_map = agent.monitor_map
    managed_iface = payload.interface
    removed_alias = None
    if payload.interface in monitor_map:
        removed_alias = monitor_map.pop(payload.interface)
    else:
        for managed, alias in list(monitor_map.items()):
            if alias == payload.interface:
                removed_alias = alias
                managed_iface = managed
                break
    # Force-clear all monitor mappings to avoid stale aliases reappearing
    agent.monitor_map = {}
    db.add(agent)
    db.commit()
    enriched = dict(response)
    enriched['interface'] = removed_alias or payload.interface
    enriched['managed'] = managed_iface
    return enriched


@router.post("/{agent_id}/scan/start")
def falcon_scan_start(agent_id: int, payload: FalconScanRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        response = client.falcon_start_scan(payload.interface)
        return response
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/{agent_id}/scan/stop")
def falcon_scan_stop(agent_id: int, payload: FalconScanRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.falcon_stop_scan(payload.interface)
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{agent_id}/scan/status")
def falcon_scan_status(agent_id: int, interface: str, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.falcon_scan_running(interface)
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{agent_id}/scan/results")
def falcon_scan_results(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    try:
        return client.falcon_get_results()
    except AgentHTTPError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/{agent_id}/deauth")
def falcon_deauth(agent_id: int, payload: FalconDeauthRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    return client.falcon_deauth(payload.dict())


@router.post("/{agent_id}/deauth/stop")
def falcon_stop_deauth(agent_id: int, payload: FalconDeauthRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    return client.falcon_stop_deauth(payload.dict())


@router.post("/{agent_id}/deauth/stopall")
def falcon_stop_all_deauths(agent_id: int, payload: FalconMonitorRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    return client.falcon_stop_all_deauths(payload.interface)


@router.get("/{agent_id}/deauths")
def falcon_get_deauths(agent_id: int, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    return client.falcon_get_deauths()


@router.post("/{agent_id}/crack")
def falcon_start_crack(agent_id: int, payload: FalconCrackRequest, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    client = AgentClient(agent)
    return client.falcon_start_crack(payload.dict())
