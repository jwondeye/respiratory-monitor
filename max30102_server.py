"""
FastAPI WebSocket server — MAX30102 vitals streamer.

Sensor code is imported directly from ~/max30102_live.py; nothing is
duplicated here. The sensor I/O runs in a daemon thread (blocking SMBus
calls stay off the event loop). A single asyncio task computes vitals
every 2 s and broadcasts JSON to all connected WebSocket clients.

Install:
    pip install fastapi "uvicorn[standard]" smbus2 numpy scipy

Run:
    python max30102_server.py           # binds 0.0.0.0:8000
    python max30102_server.py --port 80 # custom port
"""

import asyncio
import collections
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from smbus2 import SMBus

# ---------------------------------------------------------------------------
# Import sensor code from max30102_live.py (must live in the same directory
# or HOME).  No code is duplicated — functions are called by reference.
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_HOME = os.path.expanduser("~")
for _p in (_HERE, _HOME):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from max30102_live import (   # noqa: E402
    setup, read_fifo, preprocess, estimate_rate,
    I2C_BUS, I2C_ADDR, FS,
    BUFFER_SIZE, POLL_INTERVAL,
)

# ---------------------------------------------------------------------------
# Shared state  (written by sensor thread, read by async compute task)
# ---------------------------------------------------------------------------

# deque.append / deque.__len__ are GIL-atomic in CPython — safe to share
# between one producer thread and one consumer coroutine without a lock.
ir_buffer: collections.deque = collections.deque(maxlen=BUFFER_SIZE)

# Mutated only inside the event loop — no lock needed.
connected_clients: set[WebSocket] = set()
latest_vitals: dict[str, Any] = {
    "heart_rate": None,
    "resp_rate":  None,
    "timestamp":  None,
}

_stop_event = threading.Event()
log = logging.getLogger("max30102_server")

COMPUTE_INTERVAL = 2.0   # seconds between vitals updates

# ---------------------------------------------------------------------------
# Sensor thread  (blocking I/O — must NOT run in the event loop)
# ---------------------------------------------------------------------------

def _sensor_loop() -> None:
    log.info("Sensor thread starting — I2C bus %d addr 0x%02X", I2C_BUS, I2C_ADDR)
    try:
        with SMBus(I2C_BUS) as bus:
            setup(bus)
            log.info("MAX30102 initialised at %d Hz", FS)
            next_poll = time.monotonic()
            while not _stop_event.is_set():
                try:
                    for sample in read_fifo(bus):
                        ir_buffer.append(sample)
                except OSError as exc:
                    log.warning("I2C read error: %s", exc)

                next_poll += POLL_INTERVAL
                sleep_for = next_poll - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
    except Exception as exc:
        log.exception("Sensor thread crashed: %s", exc)
    finally:
        log.info("Sensor thread stopped")

# ---------------------------------------------------------------------------
# Vitals computation  (CPU-bound — offloaded to thread pool executor)
# ---------------------------------------------------------------------------

def _compute_vitals() -> dict[str, Any]:
    """Called in a thread-pool worker; returns a JSON-serialisable dict."""
    sig = preprocess(ir_buffer)   # snapshot + ±3σ clip
    rr  = estimate_rate(sig, lo=0.1, hi=0.5, min_dist_s=1.0)
    hr  = estimate_rate(sig, lo=0.8, hi=3.0, min_dist_s=0.3)
    return {
        "heart_rate": round(hr, 1) if hr is not None else None,
        "resp_rate":  round(rr, 1) if rr is not None else None,
        "timestamp":  time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

# ---------------------------------------------------------------------------
# Async broadcast + compute loop
# ---------------------------------------------------------------------------

async def _broadcast(payload: str) -> None:
    """Send text to every connected client; remove silently broken sockets."""
    global connected_clients
    dead: set[WebSocket] = set()
    for ws in connected_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    connected_clients -= dead


async def _compute_loop() -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            await asyncio.sleep(COMPUTE_INTERVAL)

            if len(ir_buffer) < BUFFER_SIZE:
                log.debug(
                    "Buffer filling: %d / %d samples", len(ir_buffer), BUFFER_SIZE
                )
                continue

            vitals = await loop.run_in_executor(None, _compute_vitals)
            latest_vitals.update(vitals)
            log.info("HR %.1f  RR %.1f", vitals["heart_rate"] or 0, vitals["resp_rate"] or 0)
            await _broadcast(json.dumps(vitals))

        except asyncio.CancelledError:
            raise                              # let shutdown propagate normally
        except Exception as exc:
            log.error("Compute loop error (will retry): %s", exc, exc_info=True)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    _stop_event.clear()

    sensor_thread = threading.Thread(
        target=_sensor_loop, daemon=True, name="sensor"
    )
    sensor_thread.start()

    compute_task = asyncio.create_task(_compute_loop(), name="compute")

    yield   # server is running

    log.info("Shutting down …")
    _stop_event.set()
    compute_task.cancel()
    sensor_thread.join(timeout=3)


app = FastAPI(title="MAX30102 Vitals Server", lifespan=lifespan)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    connected_clients.add(ws)
    log.info("WebSocket connected (%d total)", len(connected_clients))

    # Push the most recent vitals immediately so the client doesn't wait up
    # to COMPUTE_INTERVAL seconds for its first reading.
    if latest_vitals["timestamp"] is not None:
        await ws.send_text(json.dumps(latest_vitals))

    try:
        while True:
            # Receive with a timeout so we notice disconnects promptly even
            # when the client sends nothing.  WebSocketDisconnect is raised
            # when the peer closes the connection.
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=10.0)
            except asyncio.TimeoutError:
                pass   # keep-alive tick — client is still connected
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)
        log.info("WebSocket disconnected (%d remaining)", len(connected_clients))


@app.get("/health")
async def health() -> JSONResponse:
    buf_pct = round(len(ir_buffer) / BUFFER_SIZE * 100, 1)
    return JSONResponse({
        "status":            "ok",
        "buffer_samples":    len(ir_buffer),
        "buffer_capacity":   BUFFER_SIZE,
        "buffer_pct":        buf_pct,
        "clients_connected": len(connected_clients),
        "latest_vitals":     latest_vitals,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        # Single worker — Pi Zero 2W has 4 cores but SMBus is one device;
        # multiple workers would race on the I2C bus.
        workers=1,
    )
