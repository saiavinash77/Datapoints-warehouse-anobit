#!/usr/bin/env python3
"""Feature extraction (dsp_v1): real DSP per 500ms window, 13-feature vector.

Feature order (matches dataset docs):
 0 pressure_pa    1 velocity_ms     2 spl_dB          3 d_spl_dB
 4 rms            5 d_rms           6 zero_crossings  7 dominant_freq
 8 spectral_centroid  9 spectral_rolloff  10 spectral_flux  11 mfcc_0  12 mfcc_1
"""

import math

import numpy as np
from scipy.fft import dct, rfft

INT16_MAX = 32767.0
EXTRACTOR_NAME = 'dsp_v1'
EXTRACTOR_VERSION = 1


def _mel_filterbank(n_filters, n_fft, sr, fmin=50, fmax=None):
    fmax = fmax or sr / 2
    def hz2mel(hz):
        return 2595 * math.log10(1 + hz / 700)
    def mel2hz(m):
        return 700 * (10 ** (m / 2595) - 1)
    mels = np.linspace(hz2mel(fmin), hz2mel(fmax), n_filters + 2)
    bins = np.floor((n_fft + 1) * mel2hz(mels) / sr).astype(int)
    fb = np.zeros((n_filters, n_fft // 2 + 1))
    for i in range(n_filters):
        a, b, c = bins[i], bins[i + 1], bins[i + 2]
        if b == a:
            b = a + 1
        if c == b:
            c = b + 1
        c = min(c, n_fft // 2)
        fb[i, a:c] = np.hanning(max(2, c - a))
    return fb


class FeatureExtractor:
    """Stateful extractor: d_spl / d_rms / flux need the previous window."""

    def __init__(self):
        self._prev_rms = None
        self._prev_db = None
        self._prev_mag = None
        self._fb = None

    def extract(self, samples: dict, sensor_mask: int = 0) -> list:
        mic = np.asarray(samples.get('mic') or [], dtype=np.float64)
        piezo = np.asarray(samples.get('piezo') or [], dtype=np.float64)
        pressure = samples.get('pressure') or []
        airflow = samples.get('airflow') or []

        f = [0.0] * 13
        if pressure:
            f[0] = float(np.mean(np.abs(np.asarray(pressure, dtype=np.float64))))
        if airflow:
            f[1] = float(np.mean(np.abs(np.asarray(airflow, dtype=np.float64))))

        sig = mic if mic.size else piezo
        if sig.size == 0:
            return f
        sr = 16000 if mic.size else 8000

        rms = float(np.sqrt(np.mean(sig ** 2))) / INT16_MAX
        f[4] = rms
        db = 20 * math.log10(rms) if rms > 1e-9 else -120.0
        f[2] = db
        f[3] = (db - self._prev_db) if self._prev_db is not None else 0.0
        f[5] = (rms - self._prev_rms) if self._prev_rms is not None else 0.0
        self._prev_rms, self._prev_db = rms, db

        signs = np.signbit(sig)
        f[6] = float(np.count_nonzero(signs[1:] != signs[:-1]))

        n_fft = 1 << (sig.size - 1).bit_length()
        win = np.hanning(sig.size)
        mag = np.abs(rfft(sig * win, n_fft))
        freqs = np.fft.rfftfreq(n_fft, 1 / sr)
        total = mag.sum()
        if total > 0:
            f[7] = float(freqs[np.argmax(mag)])
            f[8] = float((mag * freqs).sum() / total)
            cum = np.cumsum(mag)
            f[9] = float(freqs[np.searchsorted(cum, 0.85 * total)])
            if self._prev_mag is not None and self._prev_mag.shape == mag.shape:
                f[10] = float(np.sqrt(np.sum((mag - self._prev_mag) ** 2)) / (total + 1e-9))
            self._prev_mag = mag

            if self._fb is None or self._fb.shape[1] != mag.size:
                self._fb = _mel_filterbank(20, n_fft, sr)
            mel = np.log(self._fb @ (mag ** 2) + 1e-10)
            coeffs = dct(mel, type=2, norm='ortho')[:2]
            f[11], f[12] = float(coeffs[0]), float(coeffs[1])
        return [round(x, 6) for x in f]


def extract_take_features(take_windows_rows, unblob_fn) -> np.ndarray:
    """Mean-pool window features of a take into one vector (per-take embedding, dsp_v1)."""
    vecs = []
    for w in take_windows_rows:
        v = [w[f'f{i}'] for i in range(13)]
        if any(x is not None for x in v):
            vecs.append([0.0 if x is None else x for x in v])
    if not vecs:
        return np.zeros(13)
    return np.mean(np.asarray(vecs), axis=0)
