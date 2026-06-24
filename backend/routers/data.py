from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, get_influx_query_api

router = APIRouter()

# 時間範圍 → (Flux range start, aggregateWindow 區間)
# aggregateWindow 用來降採樣，讓長時間範圍也只回傳約 120~200 個點。
_RANGE_PRESETS: dict[str, tuple[str, str]] = {
    "15m": ("-15m", "10s"),
    "1h":  ("-1h",  "30s"),
    "6h":  ("-6h",  "3m"),
    "24h": ("-24h", "12m"),
    "7d":  ("-7d",  "1h"),
    "30d": ("-30d", "6h"),
}

_UID_RE = re.compile(r"^[A-Za-z0-9_]+$")


class LatestReading(BaseModel):
    device_uid: str
    device_type: str
    value: float
    ts: datetime


class HistoryPoint(BaseModel):
    ts: datetime
    value: float


class DeviceOut(BaseModel):
    device_uid: str
    device_type: str
    name: Optional[str] = None


class SiteOut(BaseModel):
    site_id: int
    name: str
    devices: List[DeviceOut] = []


class AlertLogOut(BaseModel):
    id: int
    device_uid: str
    rule_id: int
    value: float
    triggered_at: datetime
    resolved_at: Optional[datetime]


def _parse_record(record: Any) -> Optional[dict]:
    ts_raw = record.get_time()
    if ts_raw is None:
        return None
    ts = ts_raw if isinstance(ts_raw, datetime) and ts_raw.tzinfo else (
        ts_raw.replace(tzinfo=timezone.utc) if isinstance(ts_raw, datetime) else None
    )
    if ts is None:
        return None
    try:
        value = float(record.get_value())
    except (TypeError, ValueError):
        return None
    return {
        "device_uid": record.values.get("device_uid", ""),
        "device_type": record.values.get("device_type", ""),
        "value": value,
        "ts": ts,
    }


@router.get("/sites/{site_id}/latest", response_model=List[LatestReading])
def get_site_latest(
    site_id: int,
    db: Session = Depends(get_db),
    influx=Depends(get_influx_query_api),
) -> List[LatestReading]:
    from models import Site, Device
    from collections import defaultdict

    site = db.query(Site).filter(Site.id == site_id).first()
    if site is None:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")

    devices = db.query(Device).filter(Device.site_id == site_id).all()
    if not devices:
        return []

    by_type: dict[str, list[str]] = defaultdict(list)
    for d in devices:
        by_type[d.device_type].append(d.device_uid)

    results: List[LatestReading] = []
    for device_type, uids in by_type.items():
        uid_array = ", ".join(f'"{uid}"' for uid in uids)
        flux = (
            f'from(bucket: "realtime")\n'
            f'  |> range(start: -1h)\n'
            f'  |> filter(fn: (r) => r["device_type"] == "{device_type}")\n'
            f'  |> filter(fn: (r) => contains(value: r["device_uid"], set: [{uid_array}]))\n'
            f'  |> group(columns: ["device_uid", "device_type"])\n'
            f'  |> last()'
        )
        for table in influx.query(flux):
            for record in table.records:
                parsed = _parse_record(record)
                if parsed and parsed["device_uid"]:
                    results.append(LatestReading(**parsed))

    return results


@router.get("/sites/{site_id}/history", response_model=List[HistoryPoint])
def get_device_history(
    site_id: int,
    device_uid: str = Query(...),
    hours: int = Query(24, ge=1, le=8760),
    db: Session = Depends(get_db),
    influx=Depends(get_influx_query_api),
) -> List[HistoryPoint]:
    from models import Device

    device = (
        db.query(Device)
        .filter(Device.device_uid == device_uid, Device.site_id == site_id)
        .first()
    )
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_uid}' not found in site {site_id}")

    flux = (
        f'from(bucket: "realtime")\n'
        f'  |> range(start: -{hours}h)\n'
        f'  |> filter(fn: (r) => r["device_uid"] == "{device_uid}")\n'
        f'  |> keep(columns: ["_time", "_value"])\n'
        f'  |> sort(columns: ["_time"])'
    )
    points: List[HistoryPoint] = []
    for table in influx.query(flux):
        for record in table.records:
            ts_raw = record.get_time()
            if not isinstance(ts_raw, datetime):
                continue
            ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=timezone.utc)
            try:
                value = float(record.get_value())
            except (TypeError, ValueError):
                continue
            points.append(HistoryPoint(ts=ts, value=value))

    return points


@router.get("/history", response_model=List[HistoryPoint])
def get_history_direct(
    device_uid: str = Query(..., description="設備唯一識別碼，例如 meter01"),
    time_range: str = Query("1h", alias="range", description="15m / 1h / 6h / 24h / 7d / 30d"),
    influx=Depends(get_influx_query_api),
) -> List[HistoryPoint]:
    """
    直接從 InfluxDB 撈指定設備、指定時間範圍的歷史資料（不依賴 PostgreSQL）。
    長時間範圍會自動降採樣（aggregateWindow），避免回傳過多資料點。
    """
    if not _UID_RE.match(device_uid):
        raise HTTPException(status_code=400, detail="invalid device_uid")
    if time_range not in _RANGE_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid range; allowed: {', '.join(_RANGE_PRESETS)}",
        )

    start, every = _RANGE_PRESETS[time_range]
    flux = (
        f'from(bucket: "realtime")\n'
        f'  |> range(start: {start})\n'
        f'  |> filter(fn: (r) => r["_measurement"] == "sensor_data")\n'
        f'  |> filter(fn: (r) => r["device_uid"] == "{device_uid}")\n'
        f'  |> aggregateWindow(every: {every}, fn: mean, createEmpty: false)\n'
        f'  |> keep(columns: ["_time", "_value"])\n'
        f'  |> sort(columns: ["_time"])'
    )

    points: List[HistoryPoint] = []
    for table in influx.query(flux):
        for record in table.records:
            ts_raw = record.get_time()
            if not isinstance(ts_raw, datetime):
                continue
            ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=timezone.utc)
            try:
                value = float(record.get_value())
            except (TypeError, ValueError):
                continue
            points.append(HistoryPoint(ts=ts, value=round(value, 2)))

    return points


@router.get("/customers/{customer_id}/sites", response_model=List[SiteOut])
def get_customer_sites(
    customer_id: int,
    db: Session = Depends(get_db),
) -> List[SiteOut]:
    from models import Customer, Site, Device

    if db.query(Customer).filter(Customer.id == customer_id).first() is None:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")

    sites = db.query(Site).filter(Site.customer_id == customer_id).all()
    output: List[SiteOut] = []
    for site in sites:
        devices = db.query(Device).filter(Device.site_id == site.id).all()
        output.append(
            SiteOut(
                site_id=site.id,
                name=site.name,
                devices=[DeviceOut(device_uid=d.device_uid, device_type=d.device_type, name=d.name) for d in devices],
            )
        )
    return output


@router.get("/devices/{device_uid}/alerts", response_model=List[AlertLogOut])
def get_device_alerts(
    device_uid: str,
    db: Session = Depends(get_db),
) -> List[AlertLogOut]:
    from models import AlertLog

    rows = (
        db.query(AlertLog)
        .filter(AlertLog.device_uid == device_uid)
        .order_by(AlertLog.triggered_at.desc())
        .limit(50)
        .all()
    )
    return [
        AlertLogOut(
            id=row.id,
            device_uid=row.device_uid,
            rule_id=row.rule_id,
            value=row.value,
            triggered_at=row.triggered_at,
            resolved_at=row.resolved_at,
        )
        for row in rows
    ]
