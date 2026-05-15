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

ORIGIN_URL      = os.getenv("ORIGIN_URL", "http://origin:8000")
CDN_PORT        = int(os.getenv("CDN_PORT", "8081"))

# Timeout / retry settings (0.4)
CONNECT_TIMEOUT = 5   # seconds to establish TCP connection
TOTAL_TIMEOUT   = 10  # seconds for the full response
MAX_RETRIES     = 3   # attempts before giving up
RETRY_DELAYS    = [1, 2, 4]  # exponential backoff in seconds

NODE_ID = os.getenv("NODE_ID", "cdn-node")

# Singleflight: maps filename → in-flight Future
# Prevents N concurrent requests for the same uncached file from all hitting the origin.
_inflight: dict[str, asyncio.Future] = {}

# ── 0.3 – Validação de path traversal ────────────────────────────────────────

def _safe_filename(filename: str) -> bool:
    """Returns True if the filename is safe to use as a cache path.

    Rejects:
    - Any segment containing '..' (path traversal)
    - Absolute paths (starting with '/' or '\\')
    - Names containing '\\' (unexpected Windows separator)
    - Paths that, after resolution, escape CACHE_DIR
    """
    if ".." in filename or filename.startswith("/") or filename.startswith("\\") or "\\" in filename:
        return False

    # Verificação adicional: o caminho resolvido tem de estar dentro do CACHE_DIR
    resolved = os.path.realpath(os.path.join(cache_manager.CACHE_DIR, filename))
    cache_root = os.path.realpath(cache_manager.CACHE_DIR)
    return resolved.startswith(cache_root + os.sep) or resolved == cache_root

# ── 0.4 – Fetch com timeout e retry exponencial ──────────────────────────────

async def _fetch_from_origin(filename: str) -> bytes:
    """GET from the Origin Server with timeout and up to MAX_RETRIES attempts.

    Raises:
        aiohttp.ClientError / asyncio.TimeoutError after exhausting retries.
        ValueError if the origin returns a non-200 status.
    """
    timeout = aiohttp.ClientTimeout(
        connect=CONNECT_TIMEOUT,
        total=TOTAL_TIMEOUT,
    )
    url = f"{ORIGIN_URL}/{filename}"
    last_exc: Exception = RuntimeError("No attempts made")

    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise ValueError(f"Origin returned {resp.status} for '{filename}'")
                    return await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                print(f"[RETRY {attempt}/{MAX_RETRIES}] {filename} — {exc} — retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                print(f"[FAIL] {filename} — all {MAX_RETRIES} attempts exhausted: {exc}")

    raise last_exc

# ── Request handler ───────────────────────────────────────────────────────────

async def handle_file_request(request: web.Request) -> web.Response:
    """GET /{filename}

    Cache-hit  → read from local cache and serve.
    Cache-miss → fetch from origin (with timeout + retry), save to cache, serve.
    """
    filename = request.match_info["filename"]

    # 0.3 – Reject unsafe filenames
    if not _safe_filename(filename):
        print(f"[BLOCKED] path traversal attempt: {filename!r}")
        return web.Response(status=403, text="Forbidden")

    # Cache hit
    if await cache_manager.exists(filename):
        print(f"[HIT]  [{NODE_ID}] {filename}")
        data = await cache_manager.read_file(filename)
        return web.Response(body=data, content_type="application/octet-stream")

    # Singleflight coalescing: if a download is already in progress for this
    # file, wait on the same Future instead of launching another origin request.
    if filename in _inflight:
        print(f"[COALESCE] [{NODE_ID}] {filename} — waiting for in-flight download")
        try:
            data = await asyncio.shield(_inflight[filename])
        except ValueError as exc:
            return web.Response(status=502, text=str(exc))
        except Exception as exc:
            return web.Response(status=503, text=f"Origin unreachable: {exc}")
        return web.Response(body=data, content_type="application/octet-stream")

    # Cache miss — this request is the "leader": create the Future and fetch.
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _inflight[filename] = future
    print(f"[MISS] [{NODE_ID}] {filename} — fetching from origin")
    try:
        data = await _fetch_from_origin(filename)
        await cache_manager.write_file(filename, data)
        print(f"[CACHED] [{NODE_ID}] {filename}")
        future.set_result(data)
    except Exception as exc:
        future.set_exception(exc)
        status = 502 if isinstance(exc, ValueError) else 503
        return web.Response(status=status, text=str(exc))
    finally:
        _inflight.pop(filename, None)

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

def create_app() -> web.Application:
    cache_manager.ensure_cache_dir_exists()

    app = web.Application()
    app.add_routes([web.get("/{filename}", handle_file_request)])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    app = create_app()
    print(f"CDN Node [{NODE_ID}] starting on port {CDN_PORT}, origin → {ORIGIN_URL}")
    web.run_app(app, port=CDN_PORT)
