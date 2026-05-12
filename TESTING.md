# Testing Guide 
This document describes all commands needed to start, test, and stop the CDN system.
Tests are organized by scenario and include what to expect in the logs and in Wireshark.

---

## 1. Start the system

```bash
# Build Docker images and start all 3 services in the background:
#   - mqtt-broker  → port 1883
#   - origin       → port 8001 (host) / 8000 (internal)
#   - cdn-node     → port 8081
docker compose up --build -d
```

```bash
# Confirm all 3 containers are running (STATUS = Up)
docker compose ps
```

```bash
# Follow logs from all services in real time
# Useful to observe HIT / MISS / PURGE events while running tests
docker compose logs -f
```

```bash
# Follow logs from the CDN only (cleaner output during tests)
docker compose logs -f cdn-node
```

---

## 2. Cache Miss → Cache Hit

The most important scenario: shows that the CDN fetches the file from the origin
on the first request and serves it locally on subsequent ones.

```bash
# FIRST request for the file.
# The CDN has no local copy → fetches from Origin Server → saves to cache → serves.
# CDN logs show: [MISS] test.txt — fetching from origin
# CDN logs show: [CACHED] test.txt
# Wireshark: HTTP traffic visible on port 8000 (CDN → Origin)
curl http://localhost:8081/test.txt
```

```bash
# SECOND request (and any following) for the same file.
# The CDN already has a local copy → serves directly without contacting the origin.
# CDN logs show: [HIT] test.txt
# Wireshark: NO traffic on port 8000 — only port 8081 (Client → CDN)
curl http://localhost:8081/test.txt
```

```bash
# Compare response times between Miss and Hit.
# The Hit should be significantly faster (local disk read vs. network round-trip).
# Run twice and observe the "time_total" field.
curl -s -o /dev/null -w "Time: %{time_total}s | HTTP %{http_code}\n" http://localhost:8081/test.txt
curl -s -o /dev/null -w "Time: %{time_total}s | HTTP %{http_code}\n" http://localhost:8081/test.txt
```

---

## 3. PURGE via MQTT

Demonstrates the real-time cache invalidation mechanism.
The Origin publishes an MQTT message → the CDN deletes its local copy.

```bash
# Step 1: ensure the file is cached (make a normal request)
curl http://localhost:8081/test.txt
```

```bash
# Step 2: send a PURGE signal to the Origin Server via HTTP POST.
# The Origin publishes to the MQTT topic "cdn/purge": {"file": "test.txt"}.
# The CDN receives the message and deletes its local copy of the file.
# CDN logs show: [PURGE] Received purge request for: test.txt
# Wireshark: MQTT PUBLISH packet visible on port 1883
curl -X POST http://localhost:8001/purge \
     -H "Content-Type: application/json" \
     -d '{"file": "test.txt"}'
```

```bash
# Step 3: request the file again.
# Since the cache was invalidated, the CDN fetches from the origin again.
# CDN logs show: [MISS] test.txt — fetching from origin
curl http://localhost:8081/test.txt
```

---

## 4. Security – Path Traversal (0.3)

Attempts to access files outside the cache directory.
The CDN must block these and return 403 Forbidden.

```bash
# Attempt to access /etc/passwd via URL-encoded path traversal.
# The CDN detects ".." in the path and rejects the request.
# CDN logs show: [BLOCKED] path traversal attempt: '../etc/passwd'
# Expected response: HTTP 403 Forbidden
curl -v "http://localhost:8081/..%2Fetc%2Fpasswd"
```

```bash
# Attempt with a URL-encoded absolute path.
# Expected response: HTTP 403 Forbidden
curl -v "http://localhost:8081/%2Fetc%2Fpasswd"
```

```bash
# Legitimate file — must continue to work normally.
# Expected response: HTTP 200 with file content
curl http://localhost:8081/test.txt
```

---

## 5. Resilience – Non-existent file (0.4)

Verifies the system behaviour when requesting a file that does not exist on the origin.

```bash
# The CDN tries to fetch "ghost.txt" from the origin.
# The origin returns 404 → the CDN returns 502 Bad Gateway to the client.
# CDN logs show: [FAIL] ghost.txt — esgotadas 3 tentativas
# (retry delays of 1s, 2s, 4s are visible in the logs)
curl -v http://localhost:8081/ghost.txt
```

---

## 6. Simulating latency on the origin

Demonstrates system behaviour under network latency.
Uses `tc` (traffic control) to add artificial delay to the origin container's network interface.

```bash
# Add 200 ms of latency to the origin container's network interface.
# All requests from the CDN to the origin will now take ~200 ms extra.
docker exec origin tc qdisc add dev eth0 root netem delay 200ms
```

```bash
# With latency active: request a file not yet cached.
# Response time should reflect the 200 ms overhead.
# CDN logs show the [MISS] and the increased download time.
curl -s -o /dev/null -w "Cache Miss with latency: %{time_total}s\n" http://localhost:8081/test.txt
```

```bash
# Clear the CDN cache to force a new Miss (via PURGE)
curl -X POST http://localhost:8001/purge \
     -H "Content-Type: application/json" \
     -d '{"file": "test.txt"}'
```

```bash
# Compare: Miss with latency vs. Hit (no latency — served from local cache)
curl -s -o /dev/null -w "Miss (fetches from origin, +200ms): %{time_total}s\n" http://localhost:8081/test.txt
curl -s -o /dev/null -w "Hit  (local cache, fast):           %{time_total}s\n" http://localhost:8081/test.txt
```

```bash
# Remove the artificial latency when done
docker exec origin tc qdisc del dev eth0 root
```

---

## 7. Cache persistence across restarts

Demonstrates that the cache survives a CDN container restart (persistent volume).

```bash
# Step 1: ensure test.txt is cached
curl http://localhost:8081/test.txt
```

```bash
# Step 2: restart only the CDN container (without removing the volume)
docker compose restart cdn-node
```

```bash
# Step 3: wait for the CDN to come back up and request the file again.
# Because the volume is persistent, the file is still in cache.
# CDN logs show: [HIT] test.txt  ← proves the cache survived the restart
sleep 3 && curl http://localhost:8081/test.txt
```

---

## 8. Stop the system

```bash
# Stop all containers but keep the volumes (cache preserved)
docker compose down
```

```bash
# Stop all containers AND remove the volumes (clean slate)
# Use this when you want to start completely fresh
docker compose down -v
```

---

## Endpoint reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `localhost:8081/{filename}` | GET | Request a file from the CDN (hit or miss) |
| `localhost:8001/{filename}` | GET | Request a file directly from the origin |
| `localhost:8001/purge` | POST `{"file": "..."}` | Trigger cache invalidation via MQTT |

## Log reference

| Log entry | Meaning |
|-----------|---------|
| `[MISS] filename` | Cache miss — CDN fetches from origin |
| `[CACHED] filename` | File successfully saved to cache |
| `[HIT] filename` | Cache hit — CDN serves from local disk |
| `[PURGE] Received purge request for: filename` | CDN received MQTT message and deleted local copy |
| `[BLOCKED] path traversal attempt` | Path traversal attempt blocked (403) |
| `[RETRY N/3] filename` | CDN retrying after a failed origin request |
| `[FAIL] filename` | All 3 attempts exhausted — origin unreachable |
