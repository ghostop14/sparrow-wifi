from __future__ import annotations

import enum
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import relationship

from .database import Base


class Capability(enum.Enum):
    WIFI = "wifi"
    FALCON = "falcon"
    BLUETOOTH = "bluetooth"


class ScanStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScanType(enum.Enum):
    WIFI = "wifi"
    FALCON = "falcon"
    BLUETOOTH = "bluetooth"


_json_type = JSON().with_variant(SQLiteJSON(), "sqlite")


class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), unique=True, nullable=False)
    base_url = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    api_key = Column(String(255), nullable=True)
    capabilities_raw = Column(String(255), default="")
    interfaces_json = Column(JSON, nullable=True)
    monitor_map_json = Column(JSON, nullable=True)
    gps_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    scans = relationship("ScanJob", back_populates="agent")
    push_payloads = relationship("PushPayload", back_populates="agent")

    @property
    def capabilities(self) -> List[str]:
        if not self.capabilities_raw:
            return []
        return [c for c in self.capabilities_raw.split(",") if c]

    @capabilities.setter
    def capabilities(self, values: List[str]) -> None:
        self.capabilities_raw = ",".join(sorted(set(values)))

    @property
    def interfaces(self) -> Dict[str, Any]:
        return self.interfaces_json or {}

    @interfaces.setter
    def interfaces(self, data: Dict[str, Any]) -> None:
        self.interfaces_json = data

    @property
    def monitor_map(self) -> Dict[str, str]:
        return self.monitor_map_json or {}

    @monitor_map.setter
    def monitor_map(self, data: Dict[str, str]) -> None:
        self.monitor_map_json = data

    @property
    def gps(self) -> Dict[str, Any]:
        return self.gps_json or {}

    @gps.setter
    def gps(self, data: Dict[str, Any]) -> None:
        self.gps_json = data


class ScanJob(Base):
    __tablename__ = "scans"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    scan_type = Column(Enum(ScanType), nullable=False)
    status = Column(Enum(ScanStatus), default=ScanStatus.PENDING, nullable=False)
    request_payload = Column(_json_type, nullable=True)
    response_payload = Column(_json_type, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    agent = relationship("Agent", back_populates="scans")

    def set_request_payload(self, data: Dict[str, Any]) -> None:
        self.request_payload = data

    def set_response_payload(self, data: Dict[str, Any]) -> None:
        # Safeguard to ensure JSON serializable content
        if data is None:
            self.response_payload = None
        else:
            self.response_payload = json.loads(json.dumps(data))


class PushPayload(Base):
    __tablename__ = "push_payloads"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    scan_type = Column(Enum(ScanType), nullable=False)
    interface = Column(String(120), nullable=True)
    payload = Column(_json_type, nullable=False)
    source = Column(String(32), default="push", nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow)

    agent = relationship("Agent", back_populates="push_payloads")

    def set_payload(self, data: Dict[str, Any]) -> None:
        if data is None:
            self.payload = None
        else:
            self.payload = json.loads(json.dumps(data))
