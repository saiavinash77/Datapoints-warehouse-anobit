#!/usr/bin/env python3
"""FastAPI app: serves the dashboard, WebSocket live feed, and REST API."""

import asyncio
import logging
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from collector import store as st
from collector.ingest import IngestService
from collector.quality import score_window
from collector.recorder import Recorder
from server import api
from server.api import ProcessManager

logger = logging.getLogger(__name__)

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / 'dashboard'

app = FastAPI(title='AVC Collector')
store = st.Store()
ingest = IngestService()
recorder = Recorder(store, ingest)
jobs = ProcessManager(store)
api.CTX = {'store': store, 'ingest': ingest, 'recorder': recorder, 'jobs': jobs}
app.include_router(api.router)


@app.on_event('startup')
def startup():
    ingest.start()


@app.on_event('shutdown')
def shutdown():
    ingest.stop()


@app.get('/')
def index():
    return FileResponse(DASHBOARD_DIR / 'index.html')


@app.get('/live')
def live_page():
    return FileResponse(DASHBOARD_DIR / 'index.html')


@app.websocket('/ws/live')
async def ws_live(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    latest = {}

    def on_window(win):
        q = score_window(win.samples)
        mic = win.samples.get('mic') or []
        piezo = win.samples.get('piezo') or []
        step_mic = max(1, len(mic) // 250)
        step_pz = max(1, len(piezo) // 250)
        latest.clear()
        latest.update({
            't': win.t,
            'seq': win.seq,
            'quality': q,
            'mic': [round(v / 32768, 3) for v in mic[::step_mic]],
            'piezo': [round(v / 32768, 3) for v in piezo[::step_pz]],
        })

    ingest.subscribe(on_window)
    try:
        while True:
            await asyncio.sleep(0.15)
            device = ingest.device.snapshot()
            payload = {'device': device, 'recording': recorder.is_recording,
                       't': time.time(), **latest}
            await ws.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        ingest.unsubscribe(on_window)
