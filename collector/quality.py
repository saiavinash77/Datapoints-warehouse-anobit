#!/usr/bin/env python3
"""Per-window signal quality scoring so bad takes are caught at collection time."""

import math

INT16_MAX = 32767.0
CLIP_THRESHOLD = 32700


def score_window(samples: dict) -> dict:
    """Score a 500ms window. Returns {score, verdict, reasons}."""
    reasons = []
    score = 1.0
    mic = samples.get('mic') or []
    if not mic:
        return {'score': 0.0, 'verdict': 'bad', 'reasons': ['no_mic_data']}

    n = len(mic)
    rms = math.sqrt(sum(s * s for s in mic) / n)
    norm_rms = rms / INT16_MAX
    dbfs = 20 * math.log10(norm_rms) if norm_rms > 0 else -120.0

    clipped = sum(1 for s in mic if abs(s) >= CLIP_THRESHOLD)
    clip_pct = clipped / n * 100

    if clip_pct > 1.0:
        reasons.append(f'clipping {clip_pct:.1f}%')
        score -= 0.5
    if dbfs < -45:
        reasons.append(f'too quiet ({dbfs:.0f} dBFS)')
        score -= 0.5
    elif dbfs < -30:
        reasons.append(f'low level ({dbfs:.0f} dBFS)')
        score -= 0.2

    if score >= 0.8:
        verdict = 'good'
    elif score >= 0.5:
        verdict = 'warn'
    else:
        verdict = 'bad'
    return {
        'score': round(max(0.0, score), 2),
        'verdict': verdict,
        'reasons': reasons,
        'dbfs': round(dbfs, 1),
        'clip_pct': round(clip_pct, 2),
    }
