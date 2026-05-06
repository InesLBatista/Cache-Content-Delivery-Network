# CDN Node - Main Entry Point
# TODO:
# 1. Import aiohttp.web, aiohttp.ClientSession, and local modules (cache_manager, mqtt_client).
# 2. Define a request handler for client file requests.
# 3. Logic:
#    - Check cache_manager for file existence.
#    - If Hit: Serve file using aiofiles.
#    - If Miss: Download from Origin Server, save via cache_manager, then serve.
# 4. Initialize and start the MQTT client to listen for PURGE messages.
# 5. Start the aiohttp server.

"""CDN Node - Main Entry Point

Starts an aiohttp web server that:
- Serves client file requests with a cache-first strategy.
- Fetches and caches files from the Origin Server on a cache miss.
- Listens for PURGE messages via MQTT to invalidate cached files.
"""

import os
import asyncio
import aiohttp
from aiohttp import web

import cache_manager
import mqtt_client

# Configuration 

ORIGIN_URL = os.getenv("ORIGIN_URL", "http://origin:8080")
CDN_PORT   = int(os.getenv("CDN_PORT", "8081"))

# Request handler

async def handle_file_request(request: web.Request) -> web.Response:
    """GET /{filename}

    Cache-hit  → read from local cache and serve.
    Cache-miss → fetch from Origin Server, persist to cache, then serve.
    """
    filename = request.match_info["filename"]

    # Security: reject path-traversal attempts
    if ".." in filename or filename.startswith("/"):
        return web.Response(status=400, text="Invalid filename")

    # Cache hit
    if await cache_manager.exists(filename):
        print(f"[HIT]  {filename}")
        data = await cache_manager.read_file(filename)
        return web.Response(body=data, content_type="application/octet-stream")

    # Cache miss
    print(f"[MISS] {filename} — fetching from origin")
    origin_url = f"{ORIGIN_URL}/{filename}"

    async with aiohttp.ClientSession() as session:
        async with session.get(origin_url) as resp:
            if resp.status != 200:
                return web.Response(
                    status=resp.status,
                    text=f"Origin returned {resp.status} for '{filename}'"
                )
            data = await resp.read()

    await cache_manager.write_file(filename, data)
    print(f"[CACHED] {filename}")

    return web.Response(body=data, content_type="application/octet-stream")

# Application lifecycle hooks

async def on_startup(app: web.Application) -> None:
    """Start the MQTT client after the event loop is running."""
    loop = asyncio.get_event_loop()
    client = mqtt_client.start_mqtt_client(loop)
    if client:
        app["mqtt_client"] = client

async def on_cleanup(app: web.Application) -> None:
    """Stop the MQTT client gracefully when the server shuts down."""
    client = app.get("mqtt_client")
    if client:
        client.loop_stop()
        client.disconnect()

# App factory & entry point
def create_app() -> web.Application:
    cache_manager.ensure_cache_dir_exists()

    app = web.Application()
    app.add_routes([web.get("/{filename}", handle_file_request)])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = create_app()
    print(f"CDN Node starting on port {CDN_PORT}, origin → {ORIGIN_URL}")
    web.run_app(app, port=CDN_PORT)
