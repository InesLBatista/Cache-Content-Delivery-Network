# CDN Node - MQTT Client
# TODO:
# 1. Import an MQTT library (e.g., paho-mqtt or gmqtt).
# 2. Implement a client that:
#    - Connects to the MQTT broker.
#    - Subscribes to the 'cdn/purge' topic.
#    - On receiving a message, parses the JSON and calls cache_manager.purge_file().
