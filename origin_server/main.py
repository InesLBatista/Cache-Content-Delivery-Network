import os
import json
import asyncio
from aiohttp import web
import paho.mqtt.client as mqtt

# Path to the directory where original files are stored.
# We use os.path.join and __file__ to ensure the path is relative to this script.
STORAGE_PATH = os.path.join(os.path.dirname(__file__), "storage")

# MQTT Broker configuration. 
# In a Docker environment, 'mqtt-broker' would be the service name.
# For local testing, 'localhost' is used.
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = 1883
MQTT_TOPIC = "cdn/purge"



# Initialize the MQTT client.
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    """Callback for when the client connects to the broker."""
    if rc == 0:
        print(f"Successfully connected to MQTT Broker at {MQTT_BROKER}")
    else:
        print(f"Failed to connect to MQTT Broker, return code {rc}")

mqtt_client.on_connect = on_connect

async def start_mqtt():
    """
    Connects to the MQTT broker and starts the background loop.
    This loop handles automatic reconnections and message publishing.
    """
    try:
        # connect() is blocking, but we run it once at startup.
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        # loop_start() starts a background thread to handle MQTT network traffic.
        mqtt_client.loop_start()
    except Exception as e:
        print(f"MQTT Connection Error: {e}")



# request handlers
async def handle_file_request(request):
    """
    GET /{filename}
    
    This handler serves files from the local storage directory.
    It is the main endpoint used by CDN nodes to fetch files during a 'Cache Miss'.
    """
    filename = request.match_info.get('filename')
    file_path = os.path.join(STORAGE_PATH, filename)

    # Security check: Ensure the file exists and is within the storage directory.
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        print(f"File not found: {filename}")
        return web.Response(status=404, text="File not found")

    print(f"Serving file: {filename}")
    # web.FileResponse is an efficient way to serve files asynchronously.
    return web.FileResponse(file_path)

async def handle_purge_request(request):
    """
    POST /purge
    Body: { "file": "filename.ext" }
    
    This endpoint simulates a file update or removal.
    When called, it sends a 'PURGE' message via MQTT to notify all CDN nodes
    that they should delete their local copy of the file.
    """
    try:
        data = await request.json()
        filename = data.get("file")
        
        if not filename:
            return web.Response(status=400, text="JSON must contain 'file' field")

        # Create the purge message
        payload = json.dumps({"file": filename})
        
        # Publish the message to the MQTT topic.
        # CDN nodes subscribed to this topic will receive the notification.
        mqtt_client.publish(MQTT_TOPIC, payload)
        print(f"Sent PURGE notification for: {filename}")

        return web.Response(text=f"Purge signal sent for {filename}\n")
    
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON payload")
    except Exception as e:
        return web.Response(status=500, text=str(e))


# application setup
async def init_app():
    """
    Initializes the aiohttp web application and defines routes.
    """
    app = web.Application()
    
    # Routes:
    # 1. GET /{filename} -> Serves files to CDN nodes.
    # 2. POST /purge     -> Triggers cache invalidation.
    app.add_routes([
        web.get('/{filename}', handle_file_request),
        web.post('/purge', handle_purge_request)
    ])
    
    return app

if __name__ == '__main__':
    # 1. Ensure the storage directory exists so the server doesn't crash.
    if not os.path.exists(STORAGE_PATH):
        os.makedirs(STORAGE_PATH)
        print(f"Created storage directory at {STORAGE_PATH}")

    # 2. Start the MQTT client connection.
    # We use the current event loop to run the startup coroutine.
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_mqtt())
    
    # 3. Start the Web Server.
    # By default, it runs on http://0.0.0.0:8000
    print("Starting Origin Server on port 8000...")
    web.run_app(init_app(), port=8000)
