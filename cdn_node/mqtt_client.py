"""CDN Node – MQTT Client

Subscribes to the 'cdn/purge' topic and calls cache_manager.purge_file()
whenever the Origin Server publishes a cache invalidation message.
"""

import paho.mqtt.client as mqtt
import json
import asyncio
import os
from cache_manager import purge_file

MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt-broker")
MQTT_TOPIC  = "cdn/purge"
NODE_ID     = os.getenv("NODE_ID", "cdn-node-01")


def on_connect(client, userdata, flags, rc):
    """Called when the client connects to the broker."""
    if rc == 0:
        print(f"[{NODE_ID}] Connected to MQTT broker at {MQTT_BROKER}")
        # QoS 1: broker will redeliver messages missed while the node was offline
        client.subscribe(MQTT_TOPIC, qos=1)
    else:
        print(f"[{NODE_ID}] MQTT connection failed. Code: {rc}")


def on_message(client, userdata, msg):
    """Called when a PURGE message is received from the broker."""
    try:
        data = json.loads(msg.payload.decode())
        filename = data.get("file")

        if filename:
            print(f"[PURGE] Received purge request for: {filename}")
            # Bridge async purge_file into the server's running event loop
            loop = userdata.get("loop")
            if loop:
                asyncio.run_coroutine_threadsafe(purge_file(filename), loop)

    except Exception as e:
        print(f"[{NODE_ID}] Error processing purge message: {e}")


def start_mqtt_client(loop):
    """Create and start the MQTT client in a background thread."""
    # clean_session=False: broker retains undelivered QoS 1 messages while offline
    client = mqtt.Client(client_id=NODE_ID, clean_session=False, userdata={"loop": loop})
    client.on_connect = on_connect
    client.on_message = on_message

    # Automatic reconnection with progressive backoff (1 s → 120 s)
    client.reconnect_delay_set(min_delay=1, max_delay=120)

    # Last-will: broker publishes this if the node disconnects unexpectedly
    client.will_set(f"cdn/status/{NODE_ID}", payload="lost", qos=1, retain=True)

    try:
        client.connect(MQTT_BROKER, 1883, 60)
        client.loop_start()  # background thread — does not block the main server
        return client
    except Exception as e:
        print(f"[{NODE_ID}] Could not connect to broker: {e}")
        return None
