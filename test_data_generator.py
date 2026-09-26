#!/usr/bin/env python3
"""
Test data generator for the AVC warehouse.
Generates synthetic AVC UDP packets for testing.
"""

import socket
import struct
import time
import random
import math

# Configuration
UDP_IP = "127.0.0.1"
UDP_PORT = 7777
PACKET_INTERVAL = 0.5  # 500ms window

# Sensor masks
AVC_SENSOR_MIC = 0x01
AVC_SENSOR_PIEZO = 0x02
AVC_SENSOR_PRESSURE = 0x04
AVC_SENSOR_AIRFLOW = 0x08

def generate_synthetic_mic_samples(count=8000):
    """Generate synthetic microphone samples (simulating speech)"""
    samples = []
    # Create a simple tone with some noise to simulate speech
    frequency = 440  # Hz
    sample_rate = 16000
    
    for i in range(count):
        # Sine wave + noise
        t = i / sample_rate
        sine_wave = math.sin(2 * math.pi * frequency * t)
        noise = random.gauss(0, 0.1)
        sample = int((sine_wave + noise) * 1000)  # Scale to int16 range
        samples.append(max(-32768, min(32767, sample)))  # Clamp to int16
    
    return samples

def generate_synthetic_piezo_samples(count=4000):
    """Generate synthetic piezo samples"""
    samples = []
    # Lower frequency vibration for piezo
    frequency = 100  # Hz
    sample_rate = 8000
    
    for i in range(count):
        t = i / sample_rate
        # Vibration pattern
        vibration = math.sin(2 * math.pi * frequency * t)
        noise = random.gauss(0, 0.05)
        sample = int((vibration + noise) * 2000)
        samples.append(max(-32768, min(32767, sample)))
    
    return samples

def create_avc_packet(window_count=0):
    """Create a synthetic AVC UDP packet"""
    # Header: sensor_mask (4 bytes), window_count (4 bytes), packet_type (1 byte)
    sensor_mask = AVC_SENSOR_MIC | AVC_SENSOR_PIEZO  # Mic + piezo
    packet_type = 0  # Data packet
    
    header = struct.pack('!IIB', sensor_mask, window_count, packet_type)
    
    # Sensor data
    mic_samples = generate_synthetic_mic_samples()
    piezo_samples = generate_synthetic_piezo_samples()
    
    # Mic data: sample count (2 bytes) + samples
    mic_data = struct.pack('!H', len(mic_samples))
    mic_data += struct.pack(f'!{len(mic_samples)}h', *mic_samples)
    
    # Piezo data: sample count (2 bytes) + samples
    piezo_data = struct.pack('!H', len(piezo_samples))
    piezo_data += struct.pack(f'!{len(piezo_samples)}h', *piezo_samples)
    
    # Combine all data
    packet_data = header + mic_data + piezo_data
    
    # Add dummy CRC (2 bytes)
    packet_data += struct.pack('!H', 0x1234)
    
    return packet_data

def main():
    """Generate and send test packets"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    print(f"Sending test packets to {UDP_IP}:{UDP_PORT}")
    print("Press Ctrl+C to stop")
    
    window_count = 0
    
    try:
        while True:
            packet = create_avc_packet(window_count)
            sock.sendto(packet, (UDP_IP, UDP_PORT))
            print(f"Sent packet {window_count} ({len(packet)} bytes)")
            window_count += 1
            time.sleep(PACKET_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopping test data generator")
    finally:
        sock.close()

if __name__ == '__main__':
    main()
