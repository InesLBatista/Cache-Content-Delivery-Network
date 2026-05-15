# Testing Guide – CDN System

This document describes all commands needed to start, test, and stop the CDN system.
Tests are organized by scenario and include what to expect in the logs and in Wireshark.

---

## 1. Start the system

```bash
# Build Docker images and start all services in the background:
#   - mqtt-broker   → port 1883
#   - origin        → port 8001 (host) / 8000 (internal)
#   - cdn-node-1    → port 8081 (also reachable directly for debugging)
#   - cdn-node-2    → port 8082
#   - cdn-node-3    → port 8083
#   - load-balancer → port 8090 (single client entry point, round-robin to nodes 1/2/3)
docker compose up --build -d
```

```bash
# Confirm all 6 containers are running (STATUS = Up)
docker compose ps
```

```bash
# Follow logs from all services in real time
# Useful to observe HIT / MISS / PURGE events while running tests
docker compose logs -f
```

```bash
# Follow logs from the CDN nodes only (cleaner output during tests)
docker compose logs -f cdn-node-1 cdn-node-2 cdn-node-3
```

---

## 2. Round-Robin Load Balancing

Demonstrates that the load balancer distributes requests evenly across the three CDN nodes.
The response header X-CDN-Node shows which node handled each request.

```bash
# Send 6 requests through the load balancer and observe which node handles each.
# Expected: the three node IPs cycle in order (node-1 → node-2 → node-3 → node-1 ...)
# Wireshark: each request on port 8090 is forwarded to a different upstream on port 8081
for i in $(seq 1 6); do
  echo -n "Request $i → "
  curl -s -D - -o /dev/null http://localhost:8090/test.txt | grep -i "x-cdn-node"
done
```

---

## 3. Cache Miss → Cache Hit

The most important scenario: shows that the CDN fetches the file from the origin
on the first request and serves it locally on subsequent ones.

```bash
# FIRST request for the file (routed to cdn-node-1 by round-robin).
# That node has no local copy → fetches from Origin Server → saves to cache → serves.
# cdn-node-1 logs show: [MISS] [cdn-node-1] test.txt — fetching from origin
# cdn-node-1 logs show: [CACHED] [cdn-node-1] test.txt
# Wireshark: HTTP traffic visible on port 8000 (cdn-node-1 → origin)
curl http://localhost:8090/test.txt
```

```bash
# FOURTH request (round-robin cycles back to cdn-node-1).
# That node already has a local copy → serves directly without contacting the origin.
# cdn-node-1 logs show: [HIT] [cdn-node-1] test.txt
# Wireshark: NO traffic on port 8000 — only port 8090 (client → load-balancer → cdn-node-1)
curl http://localhost:8090/test.txt
curl http://localhost:8090/test.txt
curl http://localhost:8090/test.txt
curl http://localhost:8090/test.txt
```

```bash
# Compare response times: Miss (first hit on a node) vs. Hit (subsequent requests).
# The Hit should be significantly faster (local disk read vs. network round-trip).
# Run 4 times: requests 1-3 are Miss on each node, request 4 is Hit on node-1.
for i in $(seq 1 4); do
  curl -s -o /dev/null -w "Request $i → Time: %{time_total}s | HTTP %{http_code}\n" \
    http://localhost:8090/test.txt
done
```

```bash
# You can also hit each node directly (bypassing the load balancer) to isolate behaviour.
# Each node has its own independent cache volume.
curl -w "node-1: %{time_total}s\n" -s -o /dev/null http://localhost:8081/test.txt
curl -w "node-2: %{time_total}s\n" -s -o /dev/null http://localhost:8082/test.txt
curl -w "node-3: %{time_total}s\n" -s -o /dev/null http://localhost:8083/test.txt
```

---

## 4. PURGE via MQTT

Demonstrates the real-time cache invalidation mechanism across all nodes simultaneously.
The Origin publishes one MQTT message → all three CDN nodes delete their local copy.

```bash
# Step 1: warm up all 3 caches — send 3 requests so each node caches the file
curl -s -o /dev/null http://localhost:8090/test.txt
curl -s -o /dev/null http://localhost:8090/test.txt
curl -s -o /dev/null http://localhost:8090/test.txt
```

```bash
# Step 2: send a PURGE signal to the Origin Server via HTTP POST.
# The Origin publishes to the MQTT topic "cdn/purge": {"file": "test.txt"}.
# The broker delivers the message to ALL three CDN nodes (QoS 1).
# Each node deletes its local copy of the file.
# All three node logs show: [PURGE] Received purge request for: test.txt
# Wireshark: one MQTT PUBLISH packet on port 1883, followed by 3 PUBACK packets
curl -X POST http://localhost:8001/purge \
     -H "Content-Type: application/json" \
     -d '{"file": "test.txt"}'
```

```bash
# Step 3: send 3 more requests — each node must fetch from origin again (all Miss).
# All three node logs show: [MISS] [cdn-node-N] test.txt — fetching from origin
# Wireshark: 3 separate HTTP requests on port 8000 (one per node)
curl -s -o /dev/null http://localhost:8090/test.txt
curl -s -o /dev/null http://localhost:8090/test.txt
curl -s -o /dev/null http://localhost:8090/test.txt
```

---

## 5. Security – Path Traversal (Phase 0.3)

Attempts to access files outside the cache directory.
The CDN must block these and return 403 Forbidden.

```bash
# Attempt to access /etc/passwd via URL-encoded path traversal.
# The CDN detects ".." in the path and rejects the request.
# Node logs show: [BLOCKED] path traversal attempt: '../etc/passwd'
# Expected response: HTTP 403 Forbidden
curl -v "http://localhost:8090/..%2Fetc%2Fpasswd"
```

```bash
# Attempt with a URL-encoded absolute path.
# Expected response: HTTP 403 Forbidden
curl -v "http://localhost:8090/%2Fetc%2Fpasswd"
```

```bash
# Legitimate file — must continue to work normally.
# Expected response: HTTP 200 with file content
curl http://localhost:8090/test.txt
```

---

## 6. Resilience – Non-existent file (Phase 0.4)

Verifies the system behaviour when requesting a file that does not exist on the origin.

```bash
# The CDN tries to fetch "ghost.txt" from the origin.
# The origin returns 404 → the CDN retries 3 times (1s, 2s, 4s delays) then gives up.
# Node logs show: [FAIL] ghost.txt — all 3 attempts exhausted
# Expected response: HTTP 502 Bad Gateway
curl -v http://localhost:8090/ghost.txt
```

---

## 7. Simulating latency on the origin

Demonstrates system behaviour under network latency (professor requirement).
Uses `tc` (traffic control) to add artificial delay to the origin container's network interface.

```bash
# Add 200 ms of latency to the origin container's network interface.
# All requests from any CDN node to the origin will now take ~200 ms extra.
docker exec origin tc qdisc add dev eth0 root netem delay 200ms
```

```bash
# Purge the cache on all nodes to force fresh Miss requests
curl -X POST http://localhost:8001/purge \
     -H "Content-Type: application/json" \
     -d '{"file": "test.txt"}'
```

```bash
# With latency active: send 3 requests (one Miss per node).
# Each response time should reflect the 200 ms overhead from the origin fetch.
for i in 1 2 3; do
  curl -s -o /dev/null -w "Miss with latency (node $i): %{time_total}s\n" \
    http://localhost:8090/test.txt
done
```

```bash
# Now send 3 more requests — all Hit (served from local cache, no origin contact).
# Response times should be near zero regardless of the latency setting.
for i in 1 2 3; do
  curl -s -o /dev/null -w "Hit  from cache  (node $i): %{time_total}s\n" \
    http://localhost:8090/test.txt
done
```

```bash
# Remove the artificial latency when done
docker exec origin tc qdisc del dev eth0 root
```

---

## 8. Cache persistence across restarts

Demonstrates that each node's cache survives a container restart (persistent volumes).

```bash
# Step 1: ensure all 3 nodes have test.txt cached
curl -s -o /dev/null http://localhost:8090/test.txt
curl -s -o /dev/null http://localhost:8090/test.txt
curl -s -o /dev/null http://localhost:8090/test.txt
```

```bash
# Step 2: restart all CDN nodes (without removing volumes)
docker compose restart cdn-node-1 cdn-node-2 cdn-node-3
```

```bash
# Step 3: wait for nodes to come back up and send 3 requests.
# All should be Hit — the named volumes preserved the cache across the restart.
# Node logs show: [HIT] [cdn-node-N] test.txt
sleep 4
curl -s -o /dev/null -w "node-1 after restart: HTTP %{http_code}\n" http://localhost:8081/test.txt
curl -s -o /dev/null -w "node-2 after restart: HTTP %{http_code}\n" http://localhost:8082/test.txt
curl -s -o /dev/null -w "node-3 after restart: HTTP %{http_code}\n" http://localhost:8083/test.txt
```

---

## 9. Stop the system

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
| `localhost:8090/{filename}` | GET | Request a file via the load balancer (round-robin) |
| `localhost:8081/{filename}` | GET | Request directly from cdn-node-1 (debug) |
| `localhost:8082/{filename}` | GET | Request directly from cdn-node-2 (debug) |
| `localhost:8083/{filename}` | GET | Request directly from cdn-node-3 (debug) |
| `localhost:8001/{filename}` | GET | Request a file directly from the origin |
| `localhost:8001/purge` | POST `{"file": "..."}` | Trigger cache invalidation on all nodes via MQTT |

## Log reference

| Log entry | Meaning |
|-----------|---------|
| `[MISS] [cdn-node-N] filename` | Cache miss — node fetches from origin |
| `[CACHED] [cdn-node-N] filename` | File successfully saved to that node's cache |
| `[HIT] [cdn-node-N] filename` | Cache hit — node serves from its local disk |
| `[COALESCE] [cdn-node-N] filename` | Concurrent miss coalesced — waiting for in-flight download |
| `[PURGE] Received purge request for: filename` | Node received MQTT message and deleted local copy |
| `[BLOCKED] path traversal attempt` | Path traversal attempt blocked (403) |
| `[RETRY N/3] filename` | Node retrying after a failed origin request |
| `[FAIL] filename` | All 3 attempts exhausted — origin unreachable |
