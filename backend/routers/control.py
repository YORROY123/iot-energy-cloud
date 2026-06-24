from __future__ import annotations

import json
import os
import uuid
from time import time
from typing import Any

import paho.mqtt.client as mqtt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_redis

_MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
_MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"

_mqtt_client: mqtt.Client | None = None


def _get_mqtt_client() -> mqtt.Client:
    """Lazily create and connect the MQTT client on first use."""
    global _mqtt_client
    if _mqtt_client is None:
        _mqtt_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"iess-control-{uuid.uuid4().hex[:8]}",
            clean_session=True,
        )
        _mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
        _mqtt_client.connect(_MQTT_HOST, _MQTT_PORT, keepalive=60)
        _mqtt_client.loop_start()
    return _mqtt_client


router = APIRouter(prefix="/devices", tags=["control"])


class ControlRequest(BaseModel):
    cmd: str
    value: Any


class ScheduleRequest(BaseModel):
    cmd: str
    value: Any
    cron: str


@router.post("/{device_uid}/control")
def send_control(device_uid: str, body: ControlRequest):
    request_id = str(uuid.uuid4())
    payload = {
        "cmd": body.cmd,
        "value": body.value,
        "request_id": request_id,
        "expire_at": time() + 10,
    }
    if DEMO_MODE:
        # In demo mode there is no broker — just ack the request
        return {"status": "demo_mode_ack", "request_id": request_id}

    result = _get_mqtt_client().publish(
        f"iess/control/{device_uid}", json.dumps(payload), qos=2
    )
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        raise HTTPException(status_code=503, detail=f"MQTT publish failed (rc={result.rc})")
    return {"status": "sent", "request_id": request_id}


@router.post("/{device_uid}/schedule")
def create_schedule(
    device_uid: str,
    body: ScheduleRequest,
    redis=Depends(get_redis),
):
    redis.set(
        f"schedule:{device_uid}",
        json.dumps({"cmd": body.cmd, "value": body.value, "cron": body.cron}),
    )
    return {"status": "scheduled"}


@router.get("/{device_uid}/schedule")
def get_schedule(device_uid: str, redis=Depends(get_redis)):
    raw = redis.get(f"schedule:{device_uid}")
    return {"schedule": json.loads(raw) if raw else None}
