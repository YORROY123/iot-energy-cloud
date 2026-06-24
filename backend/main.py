import asyncio
import os
import random
import threading
import time as time_module
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

from database import (
    INFLUX_BUCKET, INFLUX_ORG, SessionLocal, init_db, write_api, query_api,
)
from influxdb_client import Point, WritePrecision
import models  # registers all ORM models with Base.metadata before init_db()
from mqtt_subscriber import start_mqtt_subscriber
from ws_manager import ws_manager
from routers import data, control
from sqlalchemy import text

DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() == "true"
RUN_SIMULATOR: bool = os.getenv("RUN_SIMULATOR", "false").lower() == "true"
# 管理金鑰：設定後，踢人等管理操作需帶此金鑰。未設定則代表無保護（僅 Demo）。
ADMIN_KEY: str = os.getenv("ADMIN_KEY", "")

# 後端啟動時間（用於計算 uptime）
_START_TS = time_module.time()

try:
    import psutil
    _PROC = psutil.Process()
    _PROC.cpu_percent(interval=None)  # 第一次呼叫先初始化，之後才有意義
except Exception:
    psutil = None
    _PROC = None

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


def _system_metrics() -> dict:
    """讀取後端程序的 CPU / 記憶體用量。psutil 不可用時回傳 None 值。"""
    metrics = {
        "cpu_percent": None,
        "mem_used_mb": None,
        "mem_percent": None,
        "uptime_seconds": int(time_module.time() - _START_TS),
    }
    if _PROC is None:
        return metrics
    try:
        # 程序記憶體 (RSS)
        rss = _PROC.memory_info().rss
        metrics["mem_used_mb"] = round(rss / (1024 * 1024), 1)
        # 系統記憶體使用率
        vm = psutil.virtual_memory()
        metrics["mem_percent"] = round(vm.percent, 1)
        # 系統 CPU 使用率（短暫取樣）
        metrics["cpu_percent"] = round(psutil.cpu_percent(interval=0.3), 1)
    except Exception:
        pass
    return metrics


@app.get("/admin/overview")
def admin_overview():
    """
    後台總覽：彙總系統狀態、所有設備最新值、近 24 小時資料量。
    直接查 InfluxDB（不依賴 PostgreSQL）。
    """
    # 1. 每台設備最新值（過去 10 分鐘內的 last）
    devices = []
    online_devices = 0
    try:
        flux_latest = (
            f'from(bucket: "{INFLUX_BUCKET}")\n'
            f'  |> range(start: -10m)\n'
            f'  |> filter(fn: (r) => r["_measurement"] == "sensor_data")\n'
            f'  |> group(columns: ["device_uid", "device_type", "customer_id", "site_id"])\n'
            f'  |> last()'
        )
        now = datetime.now(timezone.utc)
        for table in query_api.query(flux_latest):
            for rec in table.records:
                t = rec.get_time()
                age = (now - t).total_seconds() if t else 9999
                is_online = age < 30
                if is_online:
                    online_devices += 1
                devices.append({
                    "device_uid": rec.values.get("device_uid", ""),
                    "device_type": rec.values.get("device_type", ""),
                    "customer_id": rec.values.get("customer_id", ""),
                    "site_id": rec.values.get("site_id", ""),
                    "value": round(float(rec.get_value()), 2),
                    "last_seen": t.isoformat() if t else None,
                    "is_online": is_online,
                })
    except Exception as exc:
        return {"error": str(exc), "online_viewers": ws_manager.count()}

    devices.sort(key=lambda d: (d["customer_id"], d["site_id"], d["device_uid"]))

    # 2. 近 24 小時總資料筆數
    total_points_24h = 0
    try:
        flux_count = (
            f'from(bucket: "{INFLUX_BUCKET}")\n'
            f'  |> range(start: -24h)\n'
            f'  |> filter(fn: (r) => r["_measurement"] == "sensor_data")\n'
            f'  |> count()\n'
            f'  |> sum()'
        )
        for table in query_api.query(flux_count):
            for rec in table.records:
                total_points_24h += int(rec.get_value() or 0)
    except Exception:
        pass

    return {
        "online_viewers": ws_manager.count(),
        "demo_mode": DEMO_MODE,
        "device_count": len(devices),
        "devices_online": online_devices,
        "total_points_24h": total_points_24h,
        "server_time": datetime.now(timezone.utc).isoformat(),
        "system": _system_metrics(),
        "devices": devices,
    }


def _check_admin_key(key: Optional[str]) -> None:
    """若有設定 ADMIN_KEY，則管理操作需帶正確金鑰。"""
    if ADMIN_KEY and key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="invalid admin key")


@app.get("/admin/clients")
def admin_clients():
    """目前所有線上連線清單（client_id / IP / 連線時長）。"""
    return {"clients": ws_manager.list_clients(), "protected": bool(ADMIN_KEY)}


@app.post("/admin/kick/{client_id}")
async def admin_kick(client_id: str, x_admin_key: Optional[str] = Header(None)):
    """踢除指定連線。若有設定 ADMIN_KEY，需在 X-Admin-Key 標頭帶金鑰。"""
    _check_admin_key(x_admin_key)
    ok = await ws_manager.kick(client_id)
    if not ok:
        raise HTTPException(status_code=404, detail="client not found")
    # 踢除後廣播最新人數
    try:
        await ws_manager.broadcast({"type": "online_count", "count": ws_manager.count()})
    except Exception:
        pass
    return {"status": "kicked", "client_id": client_id}


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
    client_ip = websocket.client.host if websocket.client else ""
    await ws_manager.connect(client_id, websocket, ip=client_ip)
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
