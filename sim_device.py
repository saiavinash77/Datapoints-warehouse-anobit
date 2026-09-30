#!/usr/bin/env python3
"""AVC device simulator: sends valid CRC'd UDP packets to the collector, mimicking
the real device (mic 16kHz + piezo 8kHz, 500ms windows)."""

import argparse
import math
import random
import socket
import time

from collector import protocol

SAMPLE_RATE_MIC = 16000
SAMPLE_RATE_PIEZO = 8000


def mic_samples(t0, count, freq=220.0, noise=0.05, amp=8000):
    out = []
    for i in range(count):
        t = t0 + i / SAMPLE_RATE_MIC
        v = amp * math.sin(2 * math.pi * freq * t)
        v += amp * noise * random.gauss(0, 1)
        out.append(max(-32768, min(32767, int(v))))
    return out


def piezo_samples(t0, count, freq=90.0, amp=4000):
    out = []
    for i in range(count):
        t = t0 + i / SAMPLE_RATE_PIEZO
        v = amp * math.sin(2 * math.pi * freq * t) + amp * 0.05 * random.gauss(0, 1)
        out.append(max(-32768, min(32767, int(v))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=protocol.UDP_PORT)
    ap.add_argument('--freq', type=float, default=220.0, help='tone frequency (Hz)')
    ap.add_argument('--amp', type=int, default=8000, help='mic amplitude (0-32767)')
    ap.add_argument('--interval', type=float, default=0.5)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mask = protocol.SENSOR_MIC | protocol.SENSOR_PIEZO
    print(f"Simulating device -> {args.host}:{args.port} "
          f"(mic {SAMPLE_RATE_MIC}Hz + piezo {SAMPLE_RATE_PIEZO}Hz, {args.interval}s windows). Ctrl+C to stop.")
    seq = 0
    t0 = time.time()
    try:
        while True:
            samples = {
                'mic': mic_samples(t0, protocol.DEFAULT_COUNTS['mic'], freq=args.freq, amp=args.amp),
                'piezo': piezo_samples(t0, protocol.DEFAULT_COUNTS['piezo']),
            }
            packet = protocol.build_packet(mask, seq, protocol.PACKET_SYNTHETIC, samples)
            sock.sendto(packet, (args.host, args.port))
            seq += 1
            t0 += args.interval
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nStopped after {seq} packets")


if __name__ == '__main__':
    main()
