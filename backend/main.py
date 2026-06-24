import asyncio
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from database import init_db
import models  # registers all ORM models with Base.metadata before init_db()
from mqtt_subscriber import start_mqtt_subscriber
from ws_manager import ws_manager
from routers import data, control


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # Capture the running event loop so the MQTT thread can schedule
    # coroutine_threadsafe broadcasts onto it.
    loop = asyncio.get_running_loop()
    ws_manager.set_loop(loop)

    mqtt_thread = threading.Thread(
        target=start_mqtt_subscriber,
        daemon=True,
        name="mqtt-subscriber",
    )
    mqtt_thread.start()

    yield


app = FastAPI(
    title="IESS — IoT Energy Management Platform",
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


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
    await ws_manager.connect(client_id, websocket)
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
