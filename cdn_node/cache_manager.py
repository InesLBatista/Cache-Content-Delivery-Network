"""CDN Node – Cache Manager

Handles all filesystem operations for the local cache:
read, write (atomic), existence check, and purge.
"""

import os
import aiofiles
import asyncio

# Cache directory, relative to this file.
# In Docker this path is overlaid by the named volume cdn_cache_N.
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')


def ensure_cache_dir_exists() -> None:
    """Ensure the cache directory exists."""
    os.makedirs(CACHE_DIR, exist_ok=True)


def cache_path(filename: str) -> str:
    """Return the absolute path for a file inside the cache directory.

    Does not validate against path traversal — callers must sanitize
    the filename before passing it here (see _safe_filename in main.py).
    """
    return os.path.join(CACHE_DIR, filename)


async def exists(filename: str) -> bool:
    """Return True if the file exists in the cache and is a regular file."""
    ensure_cache_dir_exists()
    path = cache_path(filename)
    return os.path.exists(path) and os.path.isfile(path)


async def read_file(filename: str) -> bytes:
    """Read a cached file and return its bytes.

    Raises FileNotFoundError if the file does not exist.
    """
    ensure_cache_dir_exists()
    path = cache_path(filename)
    if not os.path.exists(path) or not os.path.isfile(path):
        raise FileNotFoundError(f"Cache file not found: {filename}")

    async with aiofiles.open(path, mode='rb') as f:
        return await f.read()


async def write_file(filename: str, data: bytes) -> None:
    """Write bytes to a cache file asynchronously using an atomic rename.

    Writes to a .tmp file first, then renames it to the final path.
    This prevents partial content from being served if a write is interrupted.
    """
    ensure_cache_dir_exists()
    path = cache_path(filename)
    tmp_path = path + '.tmp'

    # Create any intermediate subdirectories (e.g. if filename contains '/')
    dirpath = os.path.dirname(path)
    os.makedirs(dirpath, exist_ok=True)

    async with aiofiles.open(tmp_path, mode='wb') as f:
        await f.write(data)

    # Yield to the event loop, then atomically replace the destination
    await asyncio.sleep(0)
    os.replace(tmp_path, path)


async def purge_file(filename: str) -> bool:
    """Remove a file from the cache.

    Returns True if the file was removed, False if it was not found.
    Propagates any other OS errors (e.g. permission denied).
    """
    ensure_cache_dir_exists()
    path = cache_path(filename)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


__all__ = [
    'ensure_cache_dir_exists',
    'exists',
    'read_file',
    'write_file',
    'purge_file',
    'CACHE_DIR',
]
