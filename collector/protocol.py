#!/usr/bin/env python3
"""AVC UDP packet protocol: wire format, CRC16, parse/build. Shared by ingest and simulator."""

import struct

UDP_PORT = 7777
MAX_PACKET = 65507

SENSOR_MIC = 0x01
SENSOR_PIEZO = 0x02
SENSOR_PRESSURE = 0x04
SENSOR_AIRFLOW = 0x08

SENSOR_NAMES = {
    SENSOR_MIC: 'mic',
    SENSOR_PIEZO: 'piezo',
    SENSOR_PRESSURE: 'pressure',
    SENSOR_AIRFLOW: 'airflow',
}

SENSOR_ORDER = [SENSOR_MIC, SENSOR_PIEZO, SENSOR_PRESSURE, SENSOR_AIRFLOW]

SAMPLE_RATES = {
    'mic': 16000,
    'piezo': 8000,
    'pressure': 100,
    'airflow': 100,
}

DEFAULT_COUNTS = {
    'mic': 8000,
    'piezo': 4000,
    'pressure': 50,
    'airflow': 50,
}

PACKET_DATA = 0
PACKET_SYNTHETIC = 1

HEADER_FMT = '!IIB'
HEADER_SIZE = struct.calcsize(HEADER_FMT)
CRC_SIZE = 2


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def build_packet(sensor_mask: int, window_count: int, packet_type: int, samples: dict) -> bytes:
    payload = struct.pack(HEADER_FMT, sensor_mask, window_count, packet_type)
    for bit in SENSOR_ORDER:
        name = SENSOR_NAMES[bit]
        if not (sensor_mask & bit):
            continue
        data = samples.get(name, [])
        payload += struct.pack('!H', len(data))
        if data:
            payload += struct.pack(f'!{len(data)}h', *data)
    return payload + struct.pack('!H', crc16_ccitt(payload))


def parse_packet(data: bytes):
    """Parse an AVC UDP packet. Returns dict or None if invalid."""
    if len(data) < HEADER_SIZE + CRC_SIZE:
        return None
    payload, crc_bytes = data[:-CRC_SIZE], data[-CRC_SIZE:]
    (crc_recv,) = struct.unpack('!H', crc_bytes)
    crc_calc = crc16_ccitt(payload)
    if crc_recv != crc_calc:
        return {'error': 'crc_mismatch', 'crc_recv': crc_recv, 'crc_calc': crc_calc}
    try:
        sensor_mask, window_count, packet_type = struct.unpack(HEADER_FMT, payload[:HEADER_SIZE])
    except struct.error:
        return {'error': 'bad_header'}
    if packet_type not in (PACKET_DATA, PACKET_SYNTHETIC):
        return {'error': 'bad_packet_type', 'packet_type': packet_type}
    offset = HEADER_SIZE
    samples = {}
    for bit in SENSOR_ORDER:
        if not (sensor_mask & bit):
            continue
        if offset + 2 > len(payload):
            return {'error': 'truncated', 'sensor': SENSOR_NAMES[bit]}
        (count,) = struct.unpack('!H', payload[offset:offset + 2])
        offset += 2
        end = offset + count * 2
        if end > len(payload):
            return {'error': 'truncated', 'sensor': SENSOR_NAMES[bit]}
        samples[SENSOR_NAMES[bit]] = list(struct.unpack(f'!{count}h', payload[offset:end]))
        offset = end
    return {
        'sensor_mask': sensor_mask,
        'window_count': window_count,
        'packet_type': packet_type,
        'samples': samples,
        'crc_ok': True,
    }
