#!/usr/bin/env python3
"""
AVC Data Warehouse Service
Listens for UDP packets, parses AVC format, extracts features, and stores in SQLite warehouse.
"""

import socket
import struct
import sqlite3
import time
import threading
from datetime import datetime
import logging

# Configuration
UDP_PORT = 7777
DATABASE = 'avc_warehouse.db'
BUFFER_SIZE = 65535  # Max UDP packet size

# AVC Packet Format (from AVC project)
# <IIB> header: 
#   I: sensor_mask (2 bytes - actually H but we'll use I for alignment)
#   I: window_count (4 bytes)
#   B: packet_type (1 byte)
# Followed by sensor data:
#   For each sensor present in mask: 
#     H: sample_count (2 bytes)
#     sample_count * int16: sensor samples (little endian)
# Footer: CRC16 (2 bytes)

# Sensor masks
AVC_SENSOR_MIC = 0x01
AVC_SENSOR_PIEZO = 0x02
AVC_SENSOR_PRESSURE = 0x04
AVC_SENSOR_AIRFLOW = 0x08

# Expected sample counts for 500ms window
SAMPLE_COUNTS = {
    AVC_SENSOR_MIC: 8000,    # 16 kHz * 0.5s
    AVC_SENSOR_PIEZO: 4000,  # 8 kHz * 0.5s (open device)
    AVC_SENSOR_PRESSURE: 50, # 100 Hz * 0.5s
    AVC_SENSOR_AIRFLOW: 50,  # 100 Hz * 0.5s
}

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('warehouse.log'),
            logging.StreamHandler()
        ]
    )

def init_database():
    """Initialize the database with the schema"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Read schema from file
    with open('schema.sql', 'r') as f:
        schema = f.read()
    
    cursor.executescript(schema)
    conn.commit()
    conn.close()
    logging.info("Database initialized")

def parse_avc_packet(data):
    """
    Parse AVC UDP packet according to the format.
    Returns dictionary with sensor data or None if invalid.
    """
    if len(data) < 9:  # Minimum header size (4+4+1) + at least some data
        return None
    
    try:
        # Parse header: sensor_mask (4 bytes), window_count (4 bytes), packet_type (1 byte)
        # Note: Using I for sensor_mask even though it's H in spec for easier unpacking
        sensor_mask, window_count, packet_type = struct.unpack('!IIB', data[:9])
        
        # Validate packet type (0 for data, 1 for synthetic, etc.)
        if packet_type not in [0, 1]:
            logging.warning(f"Unknown packet type: {packet_type}")
            return None
        
        # Parse sensor data
        offset = 9
        sensor_data = {}
        
        # Check each sensor in the mask
        for sensor_bit, sensor_name in [
            (AVC_SENSOR_MIC, 'mic'),
            (AVC_SENSOR_PIEZO, 'piezo'),
            (AVC_SENSOR_PRESSURE, 'pressure'),
            (AVC_SENSOR_AIRFLOW, 'airflow')
        ]:
            if sensor_mask & sensor_bit:
                if offset + 2 > len(data):
                    logging.warning("Incomplete packet: missing sample count")
                    return None
                
                # Read sample count
                sample_count = struct.unpack('!H', data[offset:offset+2])[0]
                offset += 2
                
                # Calculate expected bytes
                expected_bytes = sample_count * 2  # int16 = 2 bytes
                if offset + expected_bytes > len(data):
                    logging.warning(f"Incomplete packet: missing {sensor_name} data")
                    return None
                
                # Read sensor samples
                fmt = f'!{sample_count}h'  # Network byte order, signed short
                samples = list(struct.unpack(fmt, data[offset:offset+expected_bytes]))
                offset += expected_bytes
                
                sensor_data[sensor_name] = samples
        
        # Skip CRC for now (last 2 bytes)
        # In production, you'd validate CRC here
        
        return {
            'sensor_mask': sensor_mask,
            'window_count': window_count,
            'packet_type': packet_type,
            'samples': sensor_data,
            'timestamp': time.time()
        }
        
    except struct.error as e:
        logging.error(f"Failed to parse packet: {e}")
        return None

def extract_features(sensor_data):
    """
    Extract 13-feature vector from sensor data.
    Matches the format used in AVC services/features.py
    """
    features = [0.0] * 13
    
    # Feature indices (must match services/schemas.py DESCRIPTOR_NAMES order)
    # 0: pressure_pa
    # 1: velocity_ms  
    # 2: spl_dB
    # 3: d_spl_dB
    # 4: rms_amplitude
    # 5: d_rms
    # 6: zero_crossings
    # 7: dominant_freq
    # 8: spectral_centroid
    # 9: spectral_rolloff
    # 10: spectral_flux
    # 11: mfcc_0
    # 12: mfcc_1
    
    # For open device (mic + piezo only):
    # pressure_pa = 0.0 (index 0)
    # velocity_ms = 0.0 (index 1)
    
    if 'mic' in sensor_data and len(sensor_data['mic']) > 0:
        mic_samples = sensor_data['mic']
        
        # Calculate RMS (feature 4)
        import math
        sum_squares = sum(s*s for s in mic_samples)
        rms = math.sqrt(sum_squares / len(mic_samples)) if mic_samples else 0.0
        features[4] = float(rms)
        
        # Calculate zero crossings (feature 6)
        zero_crossings = 0
        for i in range(1, len(mic_samples)):
            if mic_samples[i] * mic_samples[i-1] < 0:
                zero_crossings += 1
        features[6] = float(zero_crossings)
        
        # Simple dominant frequency estimation (feature 7)
        # In real implementation, you'd use FFT
        if len(mic_samples) >= 2:
            # Count sign changes as rough frequency estimate
            zero_crossings_per_second = zero_crossings * (16000 / len(mic_samples))  # Scale to Hz
            features[7] = float(min(zero_crossings_per_second, 4000))  # Cap at Nyquist/2
        
        # Placeholder for other features (would need proper signal processing)
        # These would normally come from librosa/scipy or custom DSP
        features[2] = 0.0   # spl_dB (placeholder)
        features[3] = 0.0   # d_spl_dB (placeholder)
        features[5] = 0.0   # d_rms (placeholder)
        features[8] = 0.0   # spectral_centroid
        features[9] = 0.0   # spectral_rolloff
        features[10] = 0.0  # spectral_flux
        features[11] = 0.0  # mfcc_0
        features[12] = 0.0  # mfcc_1
    
    # Piezo features would go here if we had more sophisticated processing
    # For now, we'll leave most features as zero or use mic data as proxy
    
    return features

def store_features(features, label=None, source='udp'):
    """Store feature vector in the warehouse database"""
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO avc_features 
        (gateway_timestamp, feature_0, feature_1, feature_2, feature_3, 
         feature_4, feature_5, feature_6, feature_7, feature_8, feature_9, 
         feature_10, feature_11, feature_12, label, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        time.time(),
        features[0], features[1], features[2], features[3],
        features[4], features[5], features[6], features[7],
        features[8], features[9], features[10], features[11],
        features[12], label, source
    ))
    
    conn.commit()
    conn.close()

def udp_listener():
    """Listen for UDP packets and process them"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', UDP_PORT))
    sock.settimeout(1.0)  # 1 second timeout for checking shutdown
    
    logging.info(f"Listening for UDP packets on port {UDP_PORT}")
    
    packets_received = 0
    packets_stored = 0
    
    try:
        while True:
            try:
                data, addr = sock.recvfrom(BUFFER_SIZE)
                packets_received += 1
                
                # Parse packet
                parsed = parse_avc_packet(data)
                if parsed is None:
                    logging.debug(f"Failed to parse packet from {addr}")
                    continue
                
                # Extract features
                features = extract_features(parsed['samples'])
                
                # Store in warehouse
                store_features(features, source='udp')
                packets_stored += 1
                
                if packets_received % 100 == 0:
                    logging.info(f"Received {packets_received} packets, stored {packets_stored}")
                    
            except socket.timeout:
                continue  # Check for shutdown signal
            except Exception as e:
                logging.error(f"Error processing packet: {e}")
                
    except KeyboardInterrupt:
        logging.info("Shutting down UDP listener...")
    finally:
        sock.close()
        logging.info(f"UDP listener stopped. Total: {packets_received} received, {packets_stored} stored")

def main():
    setup_logging()
    logging.info("Starting AVC Data Warehouse Service")
    
    # Initialize database
    init_database()
    
    # Start UDP listener in a thread
    udp_thread = threading.Thread(target=udp_listener, daemon=True)
    udp_thread.start()
    
    try:
        # Keep main thread alive
        while udp_thread.is_alive():
            udp_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        logging.info("Received shutdown signal")
    
    logging.info("Warehouse service stopped")

if __name__ == '__main__':
    main()