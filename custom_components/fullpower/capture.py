"""
Capture log for future LOCAL control.

Appends every piece of cloud data we observe (device identity, REST deviceData
snapshots, and raw MQTT topic/payloads) to a JSONL file under the HA config dir:

    <config>/fullpower_capture/<mac>.jsonl

This builds the dataset needed to later replace the cloud with a local broker:
the exact MQTT topics the device uses, the command->payload mapping, and the
full OCPP telemetry schema. See LOCAL_CONTROL.md for how to use it.
"""
from __future__ import annotations

import json
import logging
import os
import time

_LOGGER = logging.getLogger(__name__)


def capture_dir(config_dir: str) -> str:
    path = os.path.join(config_dir, "fullpower_capture")
    os.makedirs(path, exist_ok=True)
    return path


def append(config_dir: str, mac: str, source: str, data, topic: str | None = None) -> None:
    """Blocking append of one record. Call via hass.async_add_executor_job."""
    safe_mac = (mac or "unknown").replace(":", "").replace("/", "")
    record = {
        "ts": round(time.time(), 3),
        "source": source,          # "identity" | "rest" | "mqtt"
        "topic": topic,
        "data": data,
    }
    try:
        fn = os.path.join(capture_dir(config_dir), f"{safe_mac}.jsonl")
        with open(fn, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as err:
        _LOGGER.debug("capture append failed: %s", err)
