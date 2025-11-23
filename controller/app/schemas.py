from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, HttpUrl, Field

from .models import ScanStatus, ScanType


class AgentBase(BaseModel):
    name: str
    base_url: HttpUrl
    description: Optional[str] = None
    api_key: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)


class AgentCreate(AgentBase):
    pass


class AgentRead(AgentBase):
    id: int
    created_at: datetime
    interfaces: Dict[str, Any] | None = None
    monitor_map: Dict[str, str] | None = None
    gps: Dict[str, Any] | None = None

    class Config:
        from_attributes = True


class ScanRequest(BaseModel):
    agent_id: int
    scan_type: ScanType
    interface: Optional[str] = None
    channels: Optional[List[int]] = None
    extras: Dict[str, Any] | None = None


class ContinuousScanRequest(ScanRequest):
    interval_seconds: int = Field(default=10, ge=2, le=3600)


class ScanRead(BaseModel):
    id: int
    agent_id: int
    scan_type: ScanType
    status: ScanStatus
    request_payload: Optional[Dict[str, Any]] = None
    response_payload: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FalconMonitorRequest(BaseModel):
    interface: str


class FalconScanRequest(BaseModel):
    interface: str


class FalconDeauthRequest(BaseModel):
    interface: str
    apmacaddr: str
    stationmacaddr: str = ''
    channel: int
    continuous: bool = True


class FalconCrackRequest(BaseModel):
    interface: str
    channel: int
    ssid: str
    apmacaddr: str
    cracktype: str = Field(default='wpapsk')
    hasclient: bool = False


class ContinuousScanStopRequest(BaseModel):
    agent_id: int
    scan_type: ScanType
    interface: str
