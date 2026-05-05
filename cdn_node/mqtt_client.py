# CDN Node - MQTT Client
# TODO:
# 1. Import an MQTT library (e.g., paho-mqtt or gmqtt).
# 2. Implement a client that:
#    - Connects to the MQTT broker.
#    - Subscribes to the 'cdn/purge' topic.
#    - On receiving a message, parses the JSON and calls cache_manager.purge_file().

import paho.mqtt.client as mqtt
import json
import asyncio
import os
from cache_manager import purge_file

# Configuração para o Docker
MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt-broker")
MQTT_TOPIC = "cdn/purge"

def on_connect(client, userdata, flags, rc):
    """Callback for cdn connection with broker"""
    if rc == 0:
        print(f"CDN has been connected to MQTT Broker: {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"MQTT connection error. Code: {rc}")

def on_message(client, userdata, msg):
    """Callback when Origin sends a PURGE"""
    try:
        data = json.loads(msg.payload.decode())
        filename = data.get("file")
        
        if filename:
            print(f"[!] Recebido PURGE para o arquivo: {filename}")
            # Integra função assíncrona com loop do servidor
            loop = userdata.get("loop")
            if loop:
                asyncio.run_coroutine_threadsafe(purge_file(filename), loop)
                
    except Exception as e:
        print(f"Error in processing purge request: {e}")

def start_mqtt_client(loop):
    """Starts the MQTT loop in the background"""
    client = mqtt.Client(userdata={"loop": loop})
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, 1883, 60)
        client.loop_start()  # Mantém conexão sem travar o código principal
        return client
    except Exception as e:
        print(f"It was not possible to connect with the broker: {e}")
        return None
