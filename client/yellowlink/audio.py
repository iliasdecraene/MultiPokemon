"""Stream PyBoy's per-frame audio samples into pygame's mixer."""
from __future__ import annotations

import collections

import numpy as np
import pygame

SAMPLE_RATE = 48000
CHUNK = 2048  # samples per queued Sound (~43 ms)


class AudioOut:
    def __init__(self, volume: int = 60):
        self.ok = False
        self.volume = max(0, min(100, volume)) / 100
        self.muted = False
        self._buf = collections.deque()
        self._buffered = 0
        try:
            pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 1024)
            pygame.mixer.init(SAMPLE_RATE, -16, 2, 1024)
            self.channel = pygame.mixer.Channel(0)
            self.ok = True
        except pygame.error:
            self.channel = None

    def push(self, samples):
        """samples: numpy int8 array (n, 2) from pyboy.sound.ndarray."""
        if not self.ok or samples is None or len(samples) == 0:
            return
        if self.muted:
            return
        arr = (samples.astype(np.int16) * int(256 * self.volume)).astype(np.int16)
        self._buf.append(arr)
        self._buffered += len(arr)
        if self._buffered >= CHUNK:
            chunk = np.concatenate(list(self._buf))
            self._buf.clear()
            self._buffered = 0
            snd = pygame.mixer.Sound(buffer=np.ascontiguousarray(chunk).tobytes())
            if not self.channel.get_busy():
                self.channel.play(snd)
            elif self.channel.get_queue() is None:
                self.channel.queue(snd)
            # else: we're ahead of real time; drop the chunk to keep latency low

    def set_muted(self, muted: bool):
        self.muted = muted
        if muted:
            self._buf.clear()
            self._buffered = 0
