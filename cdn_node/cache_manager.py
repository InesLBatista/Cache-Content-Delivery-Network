"""CDN Node – Cache Manager

Handles all filesystem operations for the local cache:
read, write (atomic), existence check, purge, LRU eviction, and TTL.

Phase 4 additions:
  4.1 – LRU eviction: tracks last_accessed and file size in SQLite.
        When total cache size exceeds CACHE_MAX_BYTES, the least-recently-used
        files are removed until the cache is back under the limit.
  4.2 – TTL: stores an expiry timestamp derived from the origin's
        Cache-Control: max-age header. Expired files are treated as a miss
        and deleted transparently on the next request.
"""

import os
import time
import sqlite3
import aiofiles
import asyncio
from typing import Optional

# Configuration 
# Cache directory. In Docker this is overlaid by the named volume cdn_cache_N.
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')

# Maximum total cache size in bytes (default 500 MB, override via env var).
CACHE_MAX_BYTES = int(os.getenv("CACHE_MAX_BYTES", str(500 * 1024 * 1024)))

# Path to the SQLite metadata database inside the cache directory.
_DB_PATH = os.path.join(CACHE_DIR, ".cache_meta.db")

# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection:
    """Open (or create) the SQLite metadata database and return a connection.

    The database holds one row per cached file:
      - filename     : relative path used as cache key
      - last_accessed: unix timestamp of the last read or write
      - size_bytes   : file size in bytes
      - expires_at   : unix timestamp after which the entry is stale (0 = never)
    """
    conn = sqlite3.connect(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_meta (
            filename      TEXT PRIMARY KEY,
            last_accessed REAL NOT NULL,
            size_bytes    INTEGER NOT NULL,
            expires_at    REAL NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _evict_lru_if_needed() -> None:
    """Remove the least-recently-used files until total size is under the limit.

    Called synchronously after every write. Fast in practice because the DB
    query is O(n log n) on the number of cached files, not on file content.
    """
    conn = _get_db()
    try:
        total = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM cache_meta").fetchone()[0]
        if total <= CACHE_MAX_BYTES:
            return

        # Fetch files ordered by least recently accessed first
        rows = conn.execute(
            "SELECT filename, size_bytes FROM cache_meta ORDER BY last_accessed ASC"
        ).fetchall()

        for filename, size in rows:
            if total <= CACHE_MAX_BYTES:
                break
            path = cache_path(filename)
            try:
                os.remove(path)
                print(f"[LRU-EVICT] Removed {filename} ({size} bytes) — cache over limit")
            except FileNotFoundError:
                pass  # already gone, still clean up the DB entry
            conn.execute("DELETE FROM cache_meta WHERE filename = ?", (filename,))
            total -= size

        conn.commit()
    finally:
        conn.close()


def _parse_max_age(cache_control: Optional[str]) -> Optional[int]:
    """Extract the max-age value (seconds) from a Cache-Control header string.

    Returns None if the header is absent or does not contain max-age.
    Example: 'public, max-age=3600' → 3600
    """
    if not cache_control:
        return None
    for part in cache_control.split(","):
        part = part.strip()
        if part.lower().startswith("max-age="):
            try:
                return int(part.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None

# ── Public API ────────────────────────────────────────────────────────────────

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
    """Return True if the file exists in the cache, is a regular file, and has not expired.

    If the file has expired (TTL elapsed), it is deleted and False is returned,
    causing the caller to treat this as a cache miss and re-fetch from the origin.
    """
    ensure_cache_dir_exists()
    path = cache_path(filename)

    if not os.path.exists(path) or not os.path.isfile(path):
        return False

    # 4.2 – TTL check: look up expiry in the metadata DB
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT expires_at FROM cache_meta WHERE filename = ?", (filename,)
        ).fetchone()
    finally:
        conn.close()

    if row is not None:
        expires_at = row[0]
        if expires_at > 0 and time.time() > expires_at:
            # Entry has expired — remove file and DB record
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            conn2 = _get_db()
            try:
                conn2.execute("DELETE FROM cache_meta WHERE filename = ?", (filename,))
                conn2.commit()
            finally:
                conn2.close()
            print(f"[TTL-EXPIRED] {filename} — removed from cache")
            return False

    return True


async def read_file(filename: str) -> bytes:
    """Read a cached file and return its bytes. Updates last_accessed timestamp.

    Raises FileNotFoundError if the file does not exist.
    """
    ensure_cache_dir_exists()
    path = cache_path(filename)
    if not os.path.exists(path) or not os.path.isfile(path):
        raise FileNotFoundError(f"Cache file not found: {filename}")

    async with aiofiles.open(path, mode='rb') as f:
        data = await f.read()

    # 4.1 – Update last_accessed so LRU eviction has accurate recency data
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE cache_meta SET last_accessed = ? WHERE filename = ?",
            (time.time(), filename)
        )
        conn.commit()
    finally:
        conn.close()

    return data


async def write_file(filename: str, data: bytes,
                     cache_control: Optional[str] = None) -> None:
    """Write bytes to a cache file asynchronously using an atomic rename.

    Writes to a .tmp file first, then renames it to the final path to prevent
    partial content from being served if a write is interrupted.

    After writing, updates the SQLite metadata (size, last_accessed, expires_at)
    and triggers LRU eviction if the total cache size exceeds CACHE_MAX_BYTES.

    Args:
        filename:      Relative cache key (already validated by the caller).
        data:          Raw file bytes to store.
        cache_control: Value of the Cache-Control header from the origin response,
                       used to derive the TTL (max-age). Pass None if absent.
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

    # 4.1 + 4.2 – Record metadata in SQLite
    now = time.time()
    max_age = _parse_max_age(cache_control)
    expires_at = (now + max_age) if max_age is not None else 0

    conn = _get_db()
    try:
        conn.execute("""
            INSERT INTO cache_meta (filename, last_accessed, size_bytes, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(filename) DO UPDATE SET
                last_accessed = excluded.last_accessed,
                size_bytes    = excluded.size_bytes,
                expires_at    = excluded.expires_at
        """, (filename, now, len(data), expires_at))
        conn.commit()
    finally:
        conn.close()

    # 4.1 – Evict LRU files if the cache is now over the size limit
    _evict_lru_if_needed()

    if expires_at > 0:
        ttl_remaining = int(expires_at - now)
        print(f"[META] {filename} — {len(data)} bytes, TTL {ttl_remaining}s")
    else:
        print(f"[META] {filename} — {len(data)} bytes, no TTL")


async def purge_file(filename: str) -> bool:
    """Remove a file from the cache and its metadata DB entry.

    Returns True if the file was removed, False if it was not found.
    Propagates any other OS errors (e.g. permission denied).
    """
    ensure_cache_dir_exists()
    path = cache_path(filename)
    removed = False
    try:
        os.remove(path)
        removed = True
    except FileNotFoundError:
        pass

    # Always clean up the DB entry, even if the file was already gone
    conn = _get_db()
    try:
        conn.execute("DELETE FROM cache_meta WHERE filename = ?", (filename,))
        conn.commit()
    finally:
        conn.close()

    return removed


def cache_stats() -> dict:
    """Return a snapshot of cache usage for monitoring / health endpoints.

    Returns a dict with:
      - total_files : number of files currently tracked
      - total_bytes : sum of all file sizes in the DB
      - max_bytes   : configured size limit
      - usage_pct   : percentage of the limit currently used
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM cache_meta"
        ).fetchone()
    finally:
        conn.close()

    total_files, total_bytes = row
    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "max_bytes":   CACHE_MAX_BYTES,
        "usage_pct":   round(total_bytes / CACHE_MAX_BYTES * 100, 2) if CACHE_MAX_BYTES else 0,
    }


__all__ = [
    'ensure_cache_dir_exists',
    'exists',
    'read_file',
    'write_file',
    'purge_file',
    'cache_stats',
    'CACHE_DIR',
    'CACHE_MAX_BYTES',
]
