#!/usr/bin/env python3
"""Session recorder: captures windows while recording, writes takes + raw window blobs,
attaches quality verdicts and labels automatically."""

import logging
import threading
import time

from . import quality
from . import store as st
from .features import FeatureExtractor

logger = logging.getLogger(__name__)

DEFAULT_TAKE_SECONDS = 3.0


class Recorder:
    def __init__(self, store: st.Store, ingest):
        self.store = store
        self.ingest = ingest
        self.extractor = FeatureExtractor()
        self.lock = threading.Lock()
        self.recording = None

    @property
    def is_recording(self):
        with self.lock:
            return self.recording is not None

    def start_take(self, session_id, prompt_id, duration_s=DEFAULT_TAKE_SECONDS):
        with self.lock:
            if self.recording:
                raise RuntimeError('already_recording')
            take_id = st.create_take(self.store, session_id, prompt_id)
            self.recording = {
                'take_id': take_id,
                'session_id': session_id,
                'prompt_id': prompt_id,
                'started': time.time(),
                'duration': duration_s,
                'windows': [],
            }
        self.ingest.subscribe(self._on_window)
        logger.info("Recording take %d (session %d, %.1fs)", take_id, session_id, duration_s)
        return take_id

    def stop_take(self):
        with self.lock:
            rec = self.recording
            self.recording = None
        if not rec:
            return None
        self.ingest.unsubscribe(self._on_window)
        return self._finish(rec)

    def _on_window(self, win):
        with self.lock:
            rec = self.recording
            if rec is None:
                return
            rec['windows'].append(win)
            if time.time() - rec['started'] >= rec['duration']:
                self.recording = None
                auto = True
            else:
                auto = False
        if auto:
            self.ingest.unsubscribe(self._on_window)
            self._finish(rec)

    def _finish(self, rec):
        take_id = rec['take_id']
        try:
            prompt = self.store.query("SELECT text FROM prompts WHERE id=?", (rec['prompt_id'],))[0]['text']
            session = st.get_session(self.store, rec['session_id'])
            participant_id = session['participant_id']
            stage = session['stage']

            verdicts = []
            for seq, win in enumerate(rec['windows']):
                q = quality.score_window(win.samples)
                verdicts.append(q)
                feats = self.extractor.extract(win.samples, win.sensor_mask)
                st.insert_window(self.store, take_id, seq,
                                 (win.t - rec['started']) * 1000.0,
                                 win.samples, feats, q, win.packet_type, win.seq)

            if verdicts:
                bad = [v for v in verdicts if v['verdict'] == 'bad']
                mean_score = sum(v['score'] for v in verdicts) / len(verdicts)
                if bad:
                    verdict = 'bad'
                    reason = '; '.join(sorted({r for v in bad for r in v['reasons']}))
                elif any(v['verdict'] == 'warn' for v in verdicts):
                    verdict = 'warn'
                    reason = '; '.join(sorted({r for v in verdicts if v['verdict'] == 'warn'
                                               for r in v['reasons']}))
                else:
                    verdict, reason = 'good', ''
                duration = rec['windows'][-1].t - rec['started'] + 0.5 if rec['windows'] else 0.0
            else:
                verdict, reason, mean_score, duration = 'bad', 'no_data', 0.0, 0.0

            st.finish_take(self.store, take_id, duration, verdict, reason)
            logger.info("Take %d finished: %s (%s), %d windows, label '%s'",
                        take_id, verdict, reason, len(rec['windows']), prompt)
            return {'take_id': take_id, 'verdict': verdict, 'reason': reason,
                    'windows': len(rec['windows']), 'label': prompt,
                    'participant_id': participant_id, 'stage': stage, 'score': mean_score}
        except Exception:
            logger.exception("Failed to finish take %d", take_id)
            return {'take_id': take_id, 'verdict': 'error'}
