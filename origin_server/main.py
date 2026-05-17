import os
import json
import asyncio
from aiohttp import web
import paho.mqtt.client as mqtt

STORAGE_PATH = os.path.join(os.path.dirname(__file__), "storage")

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT   = 1883
MQTT_TOPIC  = "cdn/purge"

# TTL advertised to CDN nodes via Cache-Control: max-age.
# 0 means no Cache-Control header is sent (CDN caches indefinitely until purged).
# Override with the CACHE_TTL_SECONDS env var, e.g. CACHE_TTL_SECONDS=30 for demos.
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "0"))

mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    """Callback for when the client connects to the broker."""
    if rc == 0:
        print(f"Successfully connected to MQTT Broker at {MQTT_BROKER}")
    else:
        print(f"Failed to connect to MQTT Broker, return code {rc}")

mqtt_client.on_connect = on_connect

async def start_mqtt():
    """Connect to the MQTT broker and start the background network loop."""
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"MQTT Connection Error: {e}")


async def handle_file_request(request):
    """GET /{filename}

    Serves files from local storage to CDN nodes on a Cache Miss.
    Includes a Cache-Control: max-age header when CACHE_TTL_SECONDS > 0,
    allowing CDN nodes to apply TTL-based expiry (Phase 4.2).
    """
    filename = request.match_info.get('filename')
    file_path = os.path.join(STORAGE_PATH, filename)

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        print(f"File not found: {filename}")
        return web.Response(status=404, text="File not found")

    print(f"Serving file: {filename}")

    # Read file and build response manually so we can set Cache-Control
    with open(file_path, 'rb') as f:
        data = f.read()

    headers = {}
    if CACHE_TTL_SECONDS > 0:
        headers["Cache-Control"] = f"public, max-age={CACHE_TTL_SECONDS}"

    return web.Response(body=data, headers=headers,
                        content_type="application/octet-stream")


async def handle_purge_request(request):
    """POST /purge  –  Body: { "file": "filename.ext" }

    Publishes a PURGE message via MQTT to notify all CDN nodes to delete
    their local copy of the specified file.
    """
    try:
        data = await request.json()
        filename = data.get("file")

        if not filename:
            return web.Response(status=400, text="JSON must contain 'file' field")

        payload = json.dumps({"file": filename})
        mqtt_client.publish(MQTT_TOPIC, payload)
        print(f"Sent PURGE notification for: {filename}")

        return web.Response(text=f"Purge signal sent for {filename}\n")

    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON payload")
    except Exception as e:
        return web.Response(status=500, text=str(e))


async def init_app():
    """Initialise the aiohttp web application and define routes."""
    app = web.Application()
    app.add_routes([
        web.get('/{filename}',  handle_file_request),
        web.post('/purge',      handle_purge_request),
    ])
    return app

if __name__ == '__main__':
    if not os.path.exists(STORAGE_PATH):
        os.makedirs(STORAGE_PATH)
        print(f"Created storage directory at {STORAGE_PATH}")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_mqtt())

    ttl_msg = f"TTL={CACHE_TTL_SECONDS}s" if CACHE_TTL_SECONDS > 0 else "no TTL"
    print(f"Starting Origin Server on port 8000 ({ttl_msg})...")
    web.run_app(init_app(), port=8000)
