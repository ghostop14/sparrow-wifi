from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..continuous import continuous_manager
from ..dependencies import get_db
from ..models import ScanJob
from ..schemas import (
    ContinuousScanRequest,
    ContinuousScanStopRequest,
    ScanRead,
    ScanRequest,
)
from ..services import enqueue_scan, run_scan_job

router = APIRouter(prefix="/api/scans", tags=["scans"])


@router.get("", response_model=list[ScanRead])
def list_scans(db: Session = Depends(get_db), limit: int = 50):
    query = select(ScanJob).order_by(ScanJob.created_at.desc()).limit(limit)
    scans = db.execute(query).scalars().all()
    return scans


@router.get("/continuous")
async def list_continuous_scans():
    return continuous_manager.list()


@router.post("/continuous", status_code=status.HTTP_202_ACCEPTED)
async def start_continuous_scan(scan_req: ContinuousScanRequest):
    await continuous_manager.start(scan_req)
    return {"status": "started"}


@router.post("/continuous/stop", status_code=status.HTTP_200_OK)
async def stop_continuous_scan(stop_req: ContinuousScanStopRequest):
    stopped = await continuous_manager.stop(stop_req.agent_id, stop_req.interface, stop_req.scan_type)
    if not stopped:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Continuous scan not found")
    return {"status": "stopped"}


@router.get("/{scan_id}", response_model=ScanRead)
def get_scan(scan_id: int, db: Session = Depends(get_db)):
    scan = db.get(ScanJob, scan_id)
    if not scan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan job not found")
    return scan


@router.post("", response_model=ScanRead, status_code=status.HTTP_202_ACCEPTED)
def create_scan(scan_req: ScanRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = enqueue_scan(db, scan_req)
    background_tasks.add_task(run_scan_job, job.id)
    db.flush()
    return job
