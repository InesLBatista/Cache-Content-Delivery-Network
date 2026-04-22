# Cache-Content-Delivery-Network

This project implements a Content Delivery Network (CDN) system focused on minimizing latency and origin server load.

## Main Documentation

- **[Structure and Operation](file:///home/ines/Documents/GitHub/Redes/Projeto/Cache-Content-Delivery-Network/STRUCTURE.md)**: Detailed document on the system's architecture, components, and data flows.

## Key Features

- **Intelligent Caching**: Cache Hit/Miss logic with persistence.
- **Asynchronous I/O**: Uses `aiofiles` for handling heavy files.
- **MQTT Invalidation**: Real-time PURGE mechanism to avoid stale data.
- **Concurrency**: Support for multiple simultaneous clients.
- **Dockerized**: Ready to run in containers with persistent storage.
