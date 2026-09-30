#!/usr/bin/env python3
"""UDP ingest service: receives AVC packets, validates CRC, tracks device health,
keeps a live ring buffer, and notifies subscribers (recorder, dashboard feed)."""

import logging
import socket
import threading
import time
from collections import deque

from . import protocol

logger = logging.getLogger(__name__)

STALE_AFTER_S = 2.0
RING_CAPACITY = 120  # ~60s of 500ms windows


class DeviceState:
    def __init__(self):
        self.lock = threading.Lock()
        self.addr = None
        self.first_seen = None
        self.last_seen = 0.0
        self.packets = 0
        self.crc_errors = 0
        self.parse_errors = 0
        self.bytes_received = 0
        self.last_window_count = -1
        self.window_gaps = 0
        self.active_sensors = []
        self.packet_type = 0

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            connected = self.addr is not None and (now - self.last_seen) < STALE_AFTER_S
            return {
                'connected': connected,
                'addr': self.addr[0] if self.addr else None,
                'packets': self.packets,
                'crc_errors': self.crc_errors,
                'parse_errors': self.parse_errors,
                'window_gaps': self.window_gaps,
                'bytes_received': self.bytes_received,
                'active_sensors': list(self.active_sensors),
                'packet_type': self.packet_type,
                'seconds_since_last': round(now - self.last_seen, 2) if self.last_seen else None,
            }


class Window:
    __slots__ = ('t', 'seq', 'packet_type', 'samples', 'sensor_mask')

    def __init__(self, t, seq, packet_type, samples, sensor_mask):
        self.t = t
        self.seq = seq
        self.packet_type = packet_type
        self.samples = samples
        self.sensor_mask = sensor_mask


class IngestService:
    def __init__(self, port=protocol.UDP_PORT, recv_buffer=65535, socket_rcvbuf=262144):
        self.port = port
        self.recv_buffer = recv_buffer
        self.socket_rcvbuf = socket_rcvbuf
        self.device = DeviceState()
        self.ring = deque(maxlen=RING_CAPACITY)
        self.ring_lock = threading.Lock()
        self.subscribers = []
        self.sub_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def subscribe(self, callback):
        with self.sub_lock:
            self.subscribers.append(callback)

    def unsubscribe(self, callback):
        with self.sub_lock:
            if callback in self.subscribers:
                self.subscribers.remove(callback)

    def recent_windows(self, n=None):
        with self.ring_lock:
            items = list(self.ring)
        return items[-n:] if n else items

    def start(self):
        self._thread = threading.Thread(target=self._run, name='ingest', daemon=True)
        self._thread.start()
        logger.info("Ingest service listening on UDP port %d", self.port)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.socket_rcvbuf)
        except OSError as e:
            logger.warning("Could not raise SO_RCVBUF: %s", e)
        actual_buf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        logger.info("UDP receive buffer: %d bytes", actual_buf)
        sock.bind(('0.0.0.0', self.port))
        sock.settimeout(1.0)
        buf = bytearray(self.recv_buffer)
        while not self._stop.is_set():
            try:
                n, addr = sock.recvfrom_into(buf)
            except socket.timeout:
                continue
            except OSError as e:
                logger.error("recvfrom failed: %s", e)
                continue
            data = bytes(buf[:n])
            self._handle(data, addr)

    def _handle(self, data, addr):
        now = time.time()
        d = self.device
        with d.lock:
            d.addr = addr
            d.last_seen = now
            if d.first_seen is None:
                d.first_seen = now
                logger.info("Device connected from %s:%d", *addr)
            d.packets += 1
            d.bytes_received += len(data)

        parsed = protocol.parse_packet(data)
        if parsed is None:
            with d.lock:
                d.parse_errors += 1
            logger.debug("Unparseable packet from %s", addr)
            return
        if parsed.get('error'):
            with d.lock:
                if parsed['error'] == 'crc_mismatch':
                    d.crc_errors += 1
                else:
                    d.parse_errors += 1
            logger.debug("Packet error %s from %s", parsed['error'], addr)
            return

        seq = parsed['window_count']
        with d.lock:
            if d.last_window_count >= 0 and seq > d.last_window_count + 1:
                d.window_gaps += seq - d.last_window_count - 1
            d.last_window_count = seq
            d.packet_type = parsed['packet_type']
            d.active_sensors = [protocol.SENSOR_NAMES[b] for b in protocol.SENSOR_ORDER
                                if parsed['sensor_mask'] & b]

        win = Window(now, seq, parsed['packet_type'], parsed['samples'], parsed['sensor_mask'])
        with self.ring_lock:
            self.ring.append(win)
        with self.sub_lock:
            subs = list(self.subscribers)
        for cb in subs:
            try:
                cb(win)
            except Exception:
                logger.exception("Subscriber failed")
