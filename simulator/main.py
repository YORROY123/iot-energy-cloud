"""
IESS Clone - Simulator
Simulates IoT gateways sending sensor data to EMQX via MQTT.

Topic: iess/{customer_id}/{site_id}/{device_type}/{device_uid}
Payload: {"value": float, "ts": int}
Anomaly: 1% chance per message (temperature -> -8C, power -> 3000W)
"""

import json
import os
import random
import time

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
PUBLISH_INTERVAL = 3

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
UNITS = {"temperature": "C", "power": "W", "humidity": "%", "co2": "ppm"}


def generate_value(device_type: str, normal_range: tuple) -> tuple:
    is_anomaly = random.random() < 0.01
    if is_anomaly and device_type in ANOMALY_VALUES:
        return ANOMALY_VALUES[device_type], True
    return round(random.uniform(*normal_range), 1), False


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print(f"[MQTT] Connected to {MQTT_HOST}:{MQTT_PORT}")
    else:
        print(f"[MQTT] Connection failed: {reason_code}")


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    print(f"[MQTT] Disconnected ({reason_code}) — paho auto-reconnecting...")


def main():
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="iess-simulator",
    )
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    print(f"[Simulator] Connecting to {MQTT_HOST}:{MQTT_PORT} ...")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    print("[Simulator] Sending data every 3s | Anomaly rate: 1%")
    print("-" * 60)

    try:
        while True:
            for customer_id, site_id, device_type, device_uid, normal_range in DEVICES:
                value, is_anomaly = generate_value(device_type, normal_range)
                topic = f"iess/{customer_id}/{site_id}/{device_type}/{device_uid}"
                payload = json.dumps({"value": value, "ts": int(time.time())})

                result = client.publish(topic, payload, qos=1)
                result.wait_for_publish()

                tag = " [ANOMALY!]" if is_anomaly else ""
                unit = UNITS.get(device_type, "")
                print(f"[PUB]{tag} {topic} = {value} {unit}")

            time.sleep(PUBLISH_INTERVAL)

    except KeyboardInterrupt:
        print("\n[Simulator] Stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
