# AVC Data Collection Platform

Ground-station system for the Artificial Vocal Cord (AVC) project: receives sensor
streams from the AVC device, records labeled speech/vibration takes, and builds a
clean ML dataset — raw signals + features + versioned embeddings.

## Architecture

```
[AVC Device] --UDP:7777--> [Ingest] --> [Recorder] --> [SQLite: avc_collector.db]
                                |                            |
                                v                            v
                         [Live dashboard]        [Process: embeddings] --> [Export CSV/NPZ/WAV]
```

- **Ingest** (`collector/ingest.py`): UDP listener, CRC16-validated packet parsing,
  device health tracking (packets, gaps, CRC errors, active sensors)
- **Recorder** (`collector/recorder.py`): prompt-based takes, auto-labeling,
  per-window quality verdicts (level, clipping), retake support
- **Storage** (`collector/store.py`, `schema.sql`): participants → sessions →
  prompts → takes → windows (raw int16 BLOBs + 13 DSP features) → embeddings
  (versioned, per-window + per-take)
- **Features** (`collector/features.py`): `dsp_v1` — RMS, SPL, zero-crossings,
  FFT dominant frequency, spectral centroid/rolloff/flux, MFCCs
- **Server** (`server/`): FastAPI + WebSocket live feed + REST API
- **Dashboard** (`dashboard/index.html`): Live / Collect / Process / Dataset tabs

## Run

```
pip install -r requirements.txt
python run.py              # dashboard at http://localhost:8000
python sim_device.py       # separate terminal: fake device for testing
```

## Connecting the real device (ESP32)

- Device and laptop on the same WiFi network
- Firmware sends UDP packets to `<laptop-ip>:7777`
- Packet format (see `collector/protocol.py`): header `!IIB` (sensor_mask,
  window_count, packet_type) → per-sensor blocks (`!H` count + big-endian int16
  samples) → CRC16-CCITT footer
- Sensor rates: mic 16 kHz, piezo 8 kHz, pressure 100 Hz, airflow 100 Hz,
  500 ms windows, max packet ~24 KB
- Allow Python through Windows Firewall for UDP port 7777 when prompted

## Collection workflow

1. **Collect tab** → Add Participant (consent required) → Start Session
   (stage 1/2/3, handheld/throat, operator ID)
2. Pick a prompt (bilingual Telugu/English words, phonemes, sustained vowels
   seeded by default) → Record → take is auto-labeled and quality-scored
3. Bad takes: retake immediately (marked, never silently deleted)
4. **Process tab** → run `dsp_v1` extraction → versioned embeddings stored
5. **Dataset tab** → class balance, participant-based 80/10/10 splits,
   export CSV/NPZ, listen to any take (mic or piezo WAV)
6. **Backup database** → clean SQLite copy in `backups/` for the central server

## Deployment model

- **Edge (collection sites):** this app + SQLite on local laptops — works offline
- **Central:** daily DB backups synced to a PostgreSQL + pgvector server for
  batch processing, neural embeddings, and model training

## Notes

- Raw samples are the source of truth — features/embeddings can always be
  recomputed; recordings are reconstructable as WAV from stored BLOBs
- Storage: ~48 KB/s while recording (~145 KB per 3 s take); 100 participants
  x 3 sessions x 20-30 prompts ≈ 1-2 GB
- The old warehouse files (`warehouse_service.py`, `test_data_generator.py`)
  are kept for reference only
