"""
IESS Clone — MQTT Subscriber

Subscribes to: iess/{customer_id}/{site_id}/{device_type}/{device_uid}
On each message:
  1. Write to InfluxDB
  2. Update Device.is_online + last_seen in PostgreSQL
  3. Check AlertRules → insert AlertLog if threshold breached
  4. Broadcast to all WebSocket clients via ws_manager
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from influxdb_client import Point, WritePrecision
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = "iess/+/+/+/+"

INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "my-super-secret-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iess")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "realtime")

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://iess:iess123@postgres/iess_db")

# ---------------------------------------------------------------------------
# Lazy singletons (created once on first message to avoid startup race)
# ---------------------------------------------------------------------------

_write_api = None
_SessionLocal = None


def _get_write_api():
    global _write_api
    if _write_api is None:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS
        client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        _write_api = client.write_api(write_options=SYNCHRONOUS)
    return _write_api


def _get_session():
    global _SessionLocal
    if _SessionLocal is None:
        engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    return _SessionLocal()


# ---------------------------------------------------------------------------
# MQTT callbacks (paho-mqtt v2 API)
# ---------------------------------------------------------------------------

def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        logger.info("MQTT connected — subscribing to %s", MQTT_TOPIC)
        client.subscribe(MQTT_TOPIC, qos=1)
    else:
        logger.error("MQTT connect failed: %s", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    if reason_code != 0:
        logger.warning("MQTT unexpected disconnect (%s) — paho will auto-reconnect", reason_code)
    # paho v2 loop_forever() handles reconnection automatically


def on_message(client, userdata, msg: mqtt.MQTTMessage) -> None:
    # 1. Parse topic
    parts = msg.topic.split("/")
    if len(parts) != 5:
        logger.warning("Unexpected topic: %s", msg.topic)
        return
    _, customer_id, site_id, device_type, device_uid = parts

    # 2. Parse payload
    try:
        payload: dict = json.loads(msg.payload)
        value: float = float(payload["value"])
        ts: int = int(payload["ts"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Bad payload on %s: %s — %s", msg.topic, msg.payload, exc)
        return

    # 3. Write to InfluxDB
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
        _get_write_api().write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    except Exception as exc:
        logger.error("InfluxDB write error: %s", exc)

    # 4 & 5. PostgreSQL updates + alert check
    db = None
    try:
        db = _get_session()
        now_utc = datetime.now(timezone.utc)

        db.execute(
            text(
                "UPDATE devices SET is_online = TRUE, last_seen = :now "
                "WHERE device_uid = :uid"
            ),
            {"now": now_utc, "uid": device_uid},
        )

        rules = db.execute(
            text(
                "SELECT id, condition, threshold FROM alert_rules "
                "WHERE device_uid = :uid AND is_active = TRUE"
            ),
            {"uid": device_uid},
        ).fetchall()

        for rule in rules:
            triggered = (
                (rule.condition == "gt" and value > rule.threshold)
                or (rule.condition == "lt" and value < rule.threshold)
            )
            if triggered:
                db.execute(
                    text(
                        "INSERT INTO alert_logs (rule_id, device_uid, value, triggered_at) "
                        "VALUES (:rule_id, :uid, :val, :ts)"
                    ),
                    {"rule_id": rule.id, "uid": device_uid, "val": value, "ts": now_utc},
                )
                logger.info(
                    "Alert: rule %d (%s %.2f) device=%s value=%.2f",
                    rule.id, rule.condition, rule.threshold, device_uid, value,
                )

        db.commit()
    except Exception as exc:
        if db:
            db.rollback()
        logger.error("PostgreSQL error: %s", exc)
    finally:
        if db:
            db.close()

    # 6. WebSocket broadcast (lazy import avoids circular import at module load)
    try:
        from ws_manager import ws_manager
        ws_manager.broadcast_from_thread({
            "type": "sensor_update",
            "customer_id": customer_id,
            "site_id": site_id,
            "device_type": device_type,
            "device_uid": device_uid,
            "value": value,
            "ts": ts,
        })
    except Exception as exc:
        logger.error("WS broadcast error: %s", exc)


# ---------------------------------------------------------------------------
# Entry point called from main.py startup
# ---------------------------------------------------------------------------

def start_mqtt_subscriber() -> None:
    """Create paho client, connect, and run loop_forever() — blocks the calling thread."""
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="iess-subscriber",
        clean_session=True,
    )
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    logger.info("Connecting to MQTT broker %s:%d …", MQTT_HOST, MQTT_PORT)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_forever()
