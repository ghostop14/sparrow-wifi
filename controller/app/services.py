from __future__ import annotations

from datetime import datetime
from typing import Dict

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent_client import AgentClient, execute_scan
from .database import get_session
from .events import event_bus
from .models import Agent, ScanJob, ScanStatus
from .schemas import AgentCreate, ScanRequest


def create_agent(session: Session, agent_in: AgentCreate) -> Agent:
    existing = session.execute(select(Agent).where(Agent.name == agent_in.name)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent name already registered")

    agent = Agent(
        name=agent_in.name,
        base_url=str(agent_in.base_url).rstrip('/'),
        description=agent_in.description,
        api_key=agent_in.api_key,
    )
    agent.capabilities = agent_in.capabilities
    session.add(agent)
    session.flush()

    refresh_agent_metadata(session, agent)
    return agent


def enqueue_scan(session: Session, scan_req: ScanRequest) -> ScanJob:
    agent = session.get(Agent, scan_req.agent_id)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    job = ScanJob(
        agent_id=agent.id,
        scan_type=scan_req.scan_type,
        status=ScanStatus.PENDING,
    )
    payload: Dict[str, object] = {
        "interface": scan_req.interface,
        "channels": scan_req.channels,
        "extras": scan_req.extras,
    }
    job.set_request_payload(payload)
    session.add(job)
    session.flush()
    return job


def run_scan_job(job_id: int) -> None:
    with get_session() as session:
        job = session.get(ScanJob, job_id)
        if not job:
            return
        agent = job.agent
        job.status = ScanStatus.RUNNING
        job.started_at = datetime.utcnow()
        session.flush()
        event_bus.publish(
            'scan.started',
            {
                'scan_id': job.id,
                'agent_id': agent.id,
                'agent_name': agent.name,
                'scan_type': job.scan_type.value,
                'request': job.request_payload,
            },
        )

        try:
            payload = job.request_payload or {}
            interface = payload.get('interface')
            channels = payload.get('channels')
            extras = payload.get('extras')

            def progress_cb(update: Dict):
                event_bus.publish(
                    'scan.progress',
                    {
                        'scan_id': job.id,
                        'agent_id': agent.id,
                        'agent_name': agent.name,
                        'scan_type': job.scan_type.value,
                        'update': update,
                    },
                )

            result = execute_scan(
                agent,
                job.scan_type,
                interface=interface,
                channels=channels,
                extras=extras,
                progress_cb=progress_cb,
            )
            job.set_response_payload(result)
            job.status = ScanStatus.COMPLETED
            event_bus.publish(
                'scan.completed',
                {
                    'scan_id': job.id,
                    'agent_id': agent.id,
                    'agent_name': agent.name,
                    'scan_type': job.scan_type.value,
                    'response': result,
                },
            )
        except Exception as exc:  # pylint: disable=broad-except
            job.status = ScanStatus.FAILED
            job.error = str(exc)
            event_bus.publish(
                'scan.failed',
                {
                    'scan_id': job.id,
                    'agent_id': agent.id,
                    'agent_name': agent.name,
                    'scan_type': job.scan_type.value,
                    'error': str(exc),
                },
            )
        finally:
            job.completed_at = datetime.utcnow()
            session.flush()
def refresh_agent_metadata(session: Session, agent: Agent) -> Agent:
    client = AgentClient(agent)
    updated_capabilities = set(agent.capabilities)
    try:
        interfaces = client.get_interfaces().get('interfaces', {})
        agent.interfaces = interfaces
        if agent.monitor_map:
            active_aliases = set(interfaces.keys())
            cleaned = {managed: alias for managed, alias in agent.monitor_map.items() if alias in active_aliases}
            agent.monitor_map = cleaned
    except Exception:
        pass
    try:
        status = client.bluetooth_running()
        if status.get('hasbluetooth'):
            updated_capabilities.add('bluetooth')
    except Exception:
        pass
    try:
        gps_status = client.gps_status()
        agent.gps = gps_status
        if gps_status.get('gpssynch'):
            updated_capabilities.add('gps')
    except Exception:
        pass
    try:
        sample_iface = next(iter(agent.interfaces.keys()), None)
        if sample_iface:
            client.falcon_scan_running(sample_iface)
            updated_capabilities.add('falcon')
    except Exception:
        pass
    agent.capabilities = list(updated_capabilities)
    session.flush()
    return agent
