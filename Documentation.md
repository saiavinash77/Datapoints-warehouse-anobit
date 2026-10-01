# AVC Data Collection Platform — Project Documentation

**Project:** Artificial Vocal Cord (AVC) — Data Collection & Dataset Building System
**Repo:** `Datapoints-warehouse-anobit`
**Version:** 1.0 (edge collector, pre-deployment)

---

## 1. Project Vision

The AVC project builds assistive speech technology in three stages:

| Stage | Users | Signal Source | Device Mode |
|---|---|---|---|
| **1** | Fully speaking people | Airborne voice (electret mic) + throat reference | `handheld` |
| **2** | Partially speaking people | Larynx vibration (piezo) + air oscillation (pressure/airflow) — device worn on the neck | `throat` |
| **3** | Completely non-speaking people | Pure intended-speech vibration/air signals from the neck | `throat` |

This repository is the **ground station**: it receives sensor streams from the AVC
device, verifies data quality in real time, labels every recording with what was
spoken, and stores everything as a clean, exportable ML dataset.

**Core principle:** raw signals are the source of truth. Features and embeddings
are derived data that can always be recomputed — raw waveforms can never be
recovered once lost, so they are stored permanently.

---

## 2. System Architecture

```
[AVC Device / ESP32]          [Collector Laptop]
      |                              |
      |  WiFi, UDP packets           |  +------------------------+
      +-------- port 7777 ---------->|  | Ingest Service         |
                                     |  |  - CRC16 validation    |
                                     |  |  - device health       |
                                     |  |  - ring buffer (60s)   |
                                     |  +-----------+------------+
                                     |              |
                                     |  +-----------v-----------+
                                     |  | Quality Monitor        |
                                     |  |  - level / clipping    |
                                     |  |  - good|warn|bad       |
                                     |  +-----------+------------+
                                     |              |
                                     |  +-----------v------------+
                                     |  | Session Recorder       |
                                     |  |  prompt -> take ->     |
                                     |  |  auto-label + store    |
                                     |  +-----------+------------+
                                     |              |
                                     |  +-----------v------------+
                                     |  | SQLite avc_collector.db|
                                     |  +-----------+------------+
                                     |              |
                                     |  +-----------v------------+
                                     |  | Web Dashboard          |
                                     |  | Live/Collect/Process/  |
                                     |  | Dataset                |
                                     |  +------------------------+
```

Daily flow: collect at the edge (SQLite, offline-safe) → one-click DB backup →
sync to central **PostgreSQL + pgvector** server for neural embeddings and training.

---

## 3. Components

### 3.1 Ingest Service — `collector/ingest.py`
- UDP listener on port **7777**, receive buffer 262144 bytes (fixes the
  WinError 10040 datagram bug from the old warehouse)
- Parses every packet, validates **CRC16-CCITT**, tracks:
  - device address, connected/stale state (>2 s without packets)
  - packet count, byte count, CRC errors, parse errors
  - window-count gaps (packet loss estimate)
  - active sensor list from the mask
- Keeps a ring buffer of the last ~60 s of windows for the live view
- Notifies subscribers (recorder, dashboard WebSocket) for each window

### 3.2 Quality Monitor — `collector/quality.py`
Scores every 500 ms window so bad takes are caught **at collection time**:
- RMS level in dBFS (too quiet < -45 dBFS, low < -30 dBFS)
- Clipping percentage (samples ≥ 32700)
- Verdict: `good` (≥ 0.8) / `warn` (≥ 0.5) / `bad`, with human-readable reasons

### 3.3 Session Recorder — `collector/recorder.py`
- Operator picks a prompt and presses Record → captures N seconds of windows
- Every window is stored with: raw samples, 13 DSP features, quality verdict,
  packet metadata, and the **prompt text as its label**
- Take auto-finishes at the set duration; verdict = worst window verdict
- Bad takes are **marked, never deleted** — exclusion happens at export time

### 3.4 Feature Extraction — `collector/features.py` (`dsp_v1`, version 1)
13-feature vector per window (numpy/scipy, no placeholder zeros):

| # | Feature | Method |
|---|---|---|
| 0 | pressure_pa | mean abs of pressure channel |
| 1 | velocity_ms | mean abs of airflow channel |
| 2 | spl_dB | 20·log10(RMS) |
| 3 | d_spl_dB | delta vs previous window |
| 4 | rms | normalized RMS |
| 5 | d_rms | delta vs previous window |
| 6 | zero_crossings | sign-change count |
| 7 | dominant_freq | FFT peak (verified: detects 220 Hz tone) |
| 8 | spectral_centroid | magnitude-weighted mean frequency |
| 9 | spectral_rolloff | 85% cumulative-energy frequency |
| 10 | spectral_flux | frame-to-frame spectral change |
| 11 | mfcc_0 | first DCT coefficient of log-mel spectrum |
| 12 | mfcc_1 | second DCT coefficient |

### 3.5 Storage — `schema.sql` / `collector/store.py`
SQLite in WAL mode. Tables:

- **participants** — code (P001…), age, gender, language, dialect, consent, notes
- **sessions** — participant, stage (1/2/3), device_mode (handheld/throat),
  **operator**, mic gain, sample rates, start/end time
- **prompts** — text, category (word/phoneme/vowel), language; 30 bilingual
  defaults seeded automatically (English words, Telugu words in Unicode,
  sustained vowels aa/ee/oo, phonemes ka/ga/cha/ja/ta/da/pa/ba/ma)
- **takes** — session, prompt, take number, duration, verdict, reason,
  marked_bad flag, train/val/test split
- **windows** — raw int16 BLOBs per sensor (mic, piezo, pressure, airflow),
  13 features, quality score/verdict, packet metadata
- **embeddings** — versioned vectors: extractor name + version, dim, float32
  blob, label, participant, stage, quality, is_bad; per-window AND per-take
  (mean-pooled) rows; re-processing creates v2 alongside v1, never overwrites

### 3.6 Processing — `server/api.py` (`ProcessManager`)
- Batch job: converts takes into embeddings (`dsp_v1` today; neural extractors
  later), with progress bar in the Process tab
- Skips already-processed takes (idempotent per extractor version)
- Option to include or exclude bad takes

### 3.7 Dashboard — `dashboard/index.html`
- **Live tab:** device badge (IP + packet count), real-time mic and piezo
  waveforms, quality meters (verdict, dBFS, clipping, packets, gaps, CRC, sensors)
- **Collect tab:** participant registration with consent, session setup
  (stage, device mode, operator), prompt picker, Record/Stop, live quality
  badge, take list with mic/piezo playback and mark-bad
- **Process tab:** extractor + scope selection, background job with progress
- **Dataset tab:** class balance per label, participant-based 80/10/10 split
  assignment (prevents same-person train/test leakage), CSV/NPZ export,
  one-click database backup

---

## 4. Device Packet Contract (ESP32 firmware must match)

Transport: **UDP**, device → laptop IP, port **7777**. Big-endian throughout.

```
Header   !IIB   sensor_mask (4B) | window_count (4B) | packet_type (1B)
Blocks   per active sensor bit (mic=0x01, piezo=0x02, pressure=0x04, airflow=0x08):
           !H sample_count (2B)
           sample_count × int16 samples
Footer   CRC16-CCITT (init 0xFFFF, poly 0x1021) over header+payload (2B)
```

- `packet_type`: 0 = live data, 1 = synthetic/test
- Sample rates: mic 16 kHz, piezo 8 kHz, pressure 100 Hz, airflow 100 Hz
- Window: 500 ms → default counts: mic 8000, piezo 4000, pressure 50, airflow 50
- Max packet ≈ 24 KB (mic + piezo)
- Reference implementation: `collector/protocol.py` (`build_packet` / `parse_packet`)
- Test device: `python sim_device.py` (sends valid CRC'd packets at 2 Hz)

---

## 5. Collection Workflow (operator guide)

1. **Register:** Collect tab → enter age/gender/language → check consent →
   Add Participant (auto-code P001, P002, …)
2. **Session:** enter operator name/ID → stage → device mode → Start Session
3. **Device check:** Live tab must show green badge, moving waveform,
   verdict `good` — adjust device position/gain *before* recording
4. **Per prompt:** select prompt (or type a new one) → person speaks → Record
   → take appears with verdict badge → retake if bad
5. **Finish:** End Session → summary stays in the takes table
6. **Process:** Process tab → Run processing → embeddings generated
7. **Export/Backup:** Dataset tab → check class balance → assign splits →
   Export CSV/NPZ → Backup database for the central server

---

## 6. Storage & Scale

- Raw rate while recording: ~48 KB/s → 3 s take ≈ 145 KB
- Target: 100 participants × 3 sessions × 20–30 prompts ≈ **1–2 GB total**
- SQLite (WAL) handles this comfortably on a laptop; central Postgres+pgvector
  takes over for aggregation and similarity search
- WAV playback is reconstructed from stored BLOBs (mic @16 kHz, piezo @8 kHz)

---

## 7. API Summary

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | device connection state and counters |
| `POST/GET /api/participants` | register / list participants (consent required) |
| `POST/GET /api/sessions` | create (stage, mode, operator) / list sessions |
| `GET/POST /api/prompts` | list / add prompts (30 seeded defaults) |
| `POST /api/record/start` · `/stop` · `/status` | recording control |
| `GET /api/takes` · `POST /api/takes/{id}/mark` | take list / mark bad |
| `GET /api/takes/{id}/audio.wav?sensor=mic\|piezo` | WAV playback |
| `POST /api/process/start` · `GET /api/process/status` | embedding batch jobs |
| `GET /api/dataset/counts` | class balance |
| `POST /api/dataset/splits/auto` | participant-based 80/10/10 splits |
| `GET /api/export/csv` · `/api/export/npz` | dataset export |
| `GET /api/backup` | clean SQLite backup file |
| `WS /ws/live` | real-time waveform + quality + device health stream |

---

## 8. Testing

- **No hardware:** `python run.py` + `python sim_device.py` — full pipeline
  (record → process → export) works against the simulator
- **Verified end-to-end:** CRC validation, exact raw-sample roundtrip,
  220 Hz dominant-frequency detection, quality verdicts, splits, exports
- **With hardware:** ESP32 on same WiFi → UDP to laptop:7777 → Live tab
  shows connection within ~2 s; CRC/gap counters reveal any format mismatch

---

## 9. Roadmap

- [x] Edge collector (this repo): ingest, record, process, export, backup
- [ ] Serial→UDP bridge (if any device variant uses USB instead of WiFi)
- [ ] Central server: PostgreSQL + pgvector, backup ingestion, neural
      embedding generation (v2 extractors), similarity search
- [ ] Trained-model inference tab (live prediction from device stream)
- [ ] Stage 2/3 piezo/pressure-specific feature pipelines
- [ ] Multi-laptop sync (manual backup copy first, upload button later)
