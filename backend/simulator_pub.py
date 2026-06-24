"""
Built-in MQTT publisher (cloud demo only).

Render 免費方案不支援獨立的 background worker，因此把模擬器的發布邏輯
放進後端 web service 的一條執行緒。它把 12 台虛擬設備的資料 publish 到
公開 broker（broker.hivemq.com），再由 mqtt_subscriber 從同一個 broker
訂閱收回 —— 資料是真的走 MQTT 出去再進來，而非同程序內部自產自銷。

由環境變數 RUN_SIMULATOR=true 啟用。
"""

from __future__ import annotations

import json
import logging
import os
import random
import time

import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
PUBLISH_INTERVAL = int(os.getenv("SIM_INTERVAL", "3"))

# (customer_id, site_id, device_type, device_uid, (min, max))
DEVICES = [
    ("C001", "S001", "temperature", "fridge01", (-20.0, -15.0)),
    ("C001", "S001", "temperature", "fridge02", (-20.0, -15.0)),
    ("C001", "S001", "power",       "meter01",  (800.0, 1500.0)),
    ("C001", "S002", "humidity",    "humid01",  (40.0,  70.0)),
    ("C001", "S002", "co2",         "co2_01",   (400.0, 1200.0)),
    ("C001", "S002", "power",       "meter02",  (800.0, 1500.0)),
    ("C002", "S003", "temperature", "fridge03", (-20.0, -15.0)),
    ("C002", "S003", "temperature", "fridge04", (-20.0, -15.0)),
    ("C002", "S003", "power",       "meter03",  (800.0, 1500.0)),
    ("C002", "S004", "temperature", "fridge05", (-20.0, -15.0)),
    ("C002", "S004", "power",       "meter04",  (800.0, 1500.0)),
    ("C002", "S004", "humidity",    "humid02",  (40.0,  70.0)),
]

ANOMALY_VALUES = {"temperature": -8.0, "power": 3000.0}


def _generate_value(device_type: str, normal_range: tuple) -> float:
    if random.random() < 0.01 and device_type in ANOMALY_VALUES:
        return ANOMALY_VALUES[device_type]
    return round(random.uniform(*normal_range), 1)


def start_publisher() -> None:
    """Blocking loop; run from a daemon thread in main.py."""
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"iess-sim-pub-{random.randint(1000, 9999)}",
    )
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    logger.info("Simulator publisher connecting to %s:%d", MQTT_HOST, MQTT_PORT)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    while True:
        for customer_id, site_id, device_type, device_uid, normal_range in DEVICES:
            value = _generate_value(device_type, normal_range)
            topic = f"iess/{customer_id}/{site_id}/{device_type}/{device_uid}"
            payload = json.dumps({"value": value, "ts": int(time.time())})
            client.publish(topic, payload, qos=1)
        time.sleep(PUBLISH_INTERVAL)
