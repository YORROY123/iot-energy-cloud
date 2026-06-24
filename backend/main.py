import asyncio
import os
import random
import threading
import time as time_module
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from database import (
    INFLUX_BUCKET, INFLUX_ORG, SessionLocal, init_db, write_api,
)
from influxdb_client import Point, WritePrecision
import models  # registers all ORM models with Base.metadata before init_db()
from mqtt_subscriber import start_mqtt_subscriber
from ws_manager import ws_manager
from routers import data, control
from sqlalchemy import text

DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"
RUN_SIMULATOR: bool = os.getenv("RUN_SIMULATOR", "false").lower() == "true"

# 12 virtual devices (mirrors simulator/main.py)
_DEMO_DEVICES = [
    ("C001", "S001", "temperature", "fridge01"),
    ("C001", "S001", "temperature", "fridge02"),
    ("C001", "S001", "power",       "meter01"),
    ("C001", "S002", "humidity",    "humid01"),
    ("C001", "S002", "co2",         "co2_01"),
    ("C001", "S002", "power",       "meter02"),
    ("C002", "S003", "temperature", "fridge03"),
    ("C002", "S003", "temperature", "fridge04"),
    ("C002", "S003", "power",       "meter03"),
    ("C002", "S004", "temperature", "fridge05"),
    ("C002", "S004", "power",       "meter04"),
    ("C002", "S004", "humidity",    "humid02"),
]


def _demo_value(device_type: str) -> float:
    if device_type == "temperature":
        v = random.uniform(-24.0, -18.0)
        if random.random() < 0.01:
            v = random.uniform(-8.0, -5.0)
    elif device_type == "power":
        v = random.uniform(800.0, 2000.0)
        if random.random() < 0.01:
            v = random.uniform(2800.0, 3200.0)
    elif device_type == "humidity":
        v = random.uniform(55.0, 75.0)
    else:  # co2
        v = random.uniform(700.0, 1000.0)
    return round(v, 1)


async def _demo_data_generator() -> None:
    """Async loop that emits fake sensor data every 3 seconds (DEMO_MODE only)."""
    await asyncio.sleep(2)  # let DB init settle first
    while True:
        ts = int(time_module.time())
        now_utc = datetime.now(timezone.utc)

        for customer_id, site_id, device_type, device_uid in _DEMO_DEVICES:
            value = _demo_value(device_type)

            # InfluxDB write (blocking but fast; run in thread pool)
            try:
                point = (
                    Point("sensor_data")
                    .tag("customer_id", customer_id)
                    .tag("site_id", site_id)
                    .tag("device_type", device_type)
                    .tag("device_uid", device_uid)
                    .field("value", value)
                    .time(ts, WritePrecision.S)
                )
                await asyncio.to_thread(
                    write_api.write, bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point
                )
            except Exception:
                pass

            # PostgreSQL update
            try:
                db = SessionLocal()
                db.execute(
                    text("UPDATE devices SET is_online = TRUE, last_seen = :now WHERE device_uid = :uid"),
                    {"now": now_utc, "uid": device_uid},
                )
                db.commit()
                db.close()
            except Exception:
                pass

            # WebSocket broadcast (direct await — we're in the event loop)
            await ws_manager.broadcast({
                "type": "sensor_update",
                "customer_id": customer_id,
                "site_id": site_id,
                "device_type": device_type,
                "device_uid": device_uid,
                "value": value,
                "ts": ts,
            })

        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    if DEMO_MODE:
        # Demo: internal async generator, no MQTT broker needed
        asyncio.create_task(_demo_data_generator())
    else:
        # Production: real MQTT subscriber in a daemon thread
        loop = asyncio.get_running_loop()
        ws_manager.set_loop(loop)
        threading.Thread(
            target=start_mqtt_subscriber,
            daemon=True,
            name="mqtt-subscriber",
        ).start()

        # Cloud demo: also publish simulated data to the same MQTT broker
        # (Render free tier has no separate worker, so we run it here).
        if RUN_SIMULATOR:
            from simulator_pub import start_publisher
            threading.Thread(
                target=start_publisher,
                daemon=True,
                name="mqtt-simulator-publisher",
            ).start()

    yield


app = FastAPI(
    title="IoT Energy Cloud — Open IoT Energy Management Platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data.router, prefix="/api")
app.include_router(control.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok", "demo_mode": DEMO_MODE}


@app.get("/stats")
def stats():
    """目前線上看板人數（WebSocket 連線數）。"""
    return {"online": ws_manager.count()}


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
    await ws_manager.connect(client_id, websocket)
    # 通知所有人最新線上人數
    await ws_manager.broadcast({"type": "online_count", "count": ws_manager.count()})
    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
                await websocket.send_json({"type": "ack", "received": msg})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_manager.disconnect(client_id)
    except Exception:
        ws_manager.disconnect(client_id)
    finally:
        ws_manager.disconnect(client_id)
        # 離線後再廣播一次更新人數
        try:
            await ws_manager.broadcast({"type": "online_count", "count": ws_manager.count()})
        except Exception:
            pass
