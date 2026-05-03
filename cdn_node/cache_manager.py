# CDN Node - Cache Manager
# TODO:
# 1. Import aiofiles and os.
# 2. Implement functions to:
#    - Check if a file exists in the 'cache/' directory.
#    - Asynchronously read a file from 'cache/'.
#    - Asynchronously write a file to 'cache/'.
#    - Delete a file from 'cache/' (PURGE logic).

"""cdn_node.cache_manager

Minimal responsibilities:
- Manage the cache directory (ensure it exists).
- Check for files presence in the cache.
- Read/write files asynchronously using aiofiles.
- Delete files (PURGE) safely.

This implementation follows the same approach/style used in the other
project files: small, asynchronous I/O helpers and simple configuration.
"""

import os
import aiofiles
import asyncio
from typing import Optional

# Diretório de cache relativo a este ficheiro.
CACHE_DIR = os.path.join(os.path.dirname(__file__), 'cache')


def ensure_cache_dir_exists() -> None:
	"""Ensure the cache directory exists."""
	if not os.path.exists(CACHE_DIR):
		os.makedirs(CACHE_DIR, exist_ok=True)


def cache_path(filename: str) -> str:
	"""Return the absolute path for a file inside the cache directory.

	Note: This helper does not validate against path traversal ('..').
	If filenames can be provided by untrusted sources, add sanitization.
	"""
	return os.path.join(CACHE_DIR, filename)


async def exists(filename: str) -> bool:
	"""Check if a file exists in the cache.

	Returns True when the path exists and is a regular file.
	"""
	ensure_cache_dir_exists()
	path = cache_path(filename)
	# O acesso a os.path.exists é rápido; é aceitável ser síncrono.
	return os.path.exists(path) and os.path.isfile(path)


async def read_file(filename: str) -> bytes:
	"""Read a file from the cache and return its bytes.

	Raises FileNotFoundError if the file does not exist.
	"""
	ensure_cache_dir_exists()
	path = cache_path(filename)
	if not os.path.exists(path) or not os.path.isfile(path):
		raise FileNotFoundError(f"Cache file not found: {filename}")

	async with aiofiles.open(path, mode='rb') as f:
		return await f.read()


async def write_file(filename: str, data: bytes) -> None:
	"""Write bytes to a cache file asynchronously.

	Ensures the target directory exists and writes to a temporary file
	before atomically moving it to the final destination to avoid partial
	content exposure.
	"""
	ensure_cache_dir_exists()
	path = cache_path(filename)
	tmp_path = path + '.tmp'

	# Assegura a pasta do ficheiro (caso filename contenha subpastas)
	dirpath = os.path.dirname(path)
	if not os.path.exists(dirpath):
		os.makedirs(dirpath, exist_ok=True)

	# Escreve para ficheiro temporário e faz rename atómico.
	async with aiofiles.open(tmp_path, mode='wb') as f:
		await f.write(data)

	# await small moment to ensure file handles flushed (não estritamente necessário)
	await asyncio.sleep(0)
	os.replace(tmp_path, path)


async def purge_file(filename: str) -> bool:
	"""Remove a file from the cache. Returns True if removed, False if not found.

	This function swallows FileNotFoundError but will propagate other OS
	errors (permission issues, etc.).
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
