"""Stream the emulator's per-frame audio samples to the sound card.

The sound card pulls samples from a small reserve (SDL audio callback). The game adds ~800 samples per frame. A short
hiccup of the game loop (slow PC, Windows busy, Bluetooth headset) is covered by the reserve instead of turning
into crackles: we aim for ~100 ms buffered, wait for ~60 ms before (re)starting after running dry, and drop the
oldest audio if more than ~250 ms piles up (so the sound never lags behind the picture).
Falls back to pygame.mixer channel queuing if the SDL audio device can't be opened.
"""
from __future__ import annotations

import collections
import logging
import threading

import numpy as np
import pygame

log = logging.getLogger("yellowlink.audio")

SAMPLE_RATE = 48000
CHANNELS = 2
START_AT = SAMPLE_RATE * 60 // 1000      # samples buffered before playing (again)
TARGET = SAMPLE_RATE * 100 // 1000
MAX_BUFFERED = SAMPLE_RATE * 250 // 1000
CHUNK = 2048                             # fallback path: samples per queued Sound


class AudioOut:
    def __init__(self, volume: int = 60):
        self.ok = False
        self.volume = max(0, min(100, volume)) / 100
        self.muted = False
        self._lock = threading.Lock()
        self._buf: collections.deque = collections.deque()    # int16 arrays (n, 2)
        self._buffered = 0
        self._head = 0                     # samples already played from _buf[0]
        self._running = False              # False while refilling after running dry
        self.underruns = 0
        self.device = None
        self.channel = None
        try:
            from pygame._sdl2 import audio as sdl_audio
            from pygame._sdl2 import sdl2
            if pygame.mixer.get_init():
                pygame.mixer.quit()                # pygame.init() opened it; we feed the sound card ourselves
            sdl2.init_subsystem(sdl2.INIT_AUDIO)
            self.device = sdl_audio.AudioDevice(
                devicename=None, iscapture=False, frequency=SAMPLE_RATE, audioformat=sdl_audio.AUDIO_S16,
                numchannels=CHANNELS, chunksize=1024, allowed_changes=0, callback=self._callback)
            self.device.pause(0)
            self.ok = True
            log.info("audio: SDL device, %d Hz", SAMPLE_RATE)
        except Exception as e:  # noqa: BLE001 - any failure: use the mixer instead
            log.warning("audio device failed (%s); using the mixer", e)
            self.device = None
            try:
                pygame.mixer.init(SAMPLE_RATE, -16, CHANNELS, 1024)
                self.channel = pygame.mixer.Channel(0)
                self.ok = True
            except Exception as e2:  # noqa: BLE001 - no sound card at all: play silently, never crash
                log.warning("no sound (%s)", e2)
                self.channel = None

    # --- game side ---------------------------------------------------------------------------------------
    def push(self, samples):
        """samples: numpy int8 array (n, 2) from the emulator."""
        if not self.ok or samples is None or len(samples) == 0 or self.muted:
            return
        arr = (samples.astype(np.int16) * int(256 * self.volume)).astype(np.int16).reshape(-1, CHANNELS)
        if self.device is None:
            self._push_mixer(arr)
            return
        with self._lock:
            self._buf.append(arr)
            self._buffered += len(arr)
            if self._buffered > MAX_BUFFERED:      # too far ahead (e.g. after a pause): drop the oldest
                self._drop(self._buffered - TARGET)

    def _drop(self, n):
        while n > 0 and self._buf:
            left = len(self._buf[0]) - self._head
            if left <= n:
                self._buf.popleft()
                self._buffered -= left
                n -= left
                self._head = 0
            else:
                self._head += n
                self._buffered -= n
                n = 0

    # --- sound card side (SDL audio thread) ----------------------------------------------------------------
    def _callback(self, device, mem):
        out = np.frombuffer(mem, dtype=np.int16).reshape(-1, CHANNELS)
        need = len(out)
        pos = 0
        with self._lock:
            if not self._running and self._buffered >= START_AT:
                self._running = True
            if self._running:
                while pos < need and self._buf:
                    chunk = self._buf[0]
                    take = min(need - pos, len(chunk) - self._head)
                    out[pos:pos + take] = chunk[self._head:self._head + take]
                    pos += take
                    self._head += take
                    self._buffered -= take
                    if self._head >= len(chunk):
                        self._buf.popleft()
                        self._head = 0
                if pos < need:                     # ran dry: silence, and refill before playing again
                    self._running = False
                    self.underruns += 1
        if pos < need:
            out[pos:] = 0

    # --- fallback --------------------------------------------------------------------------------------------
    def _push_mixer(self, arr):
        self._buf.append(arr)
        self._buffered += len(arr)
        if self._buffered >= CHUNK:
            chunk = np.concatenate(list(self._buf))
            self._buf.clear()
            self._buffered = 0
            try:
                snd = pygame.mixer.Sound(buffer=np.ascontiguousarray(chunk).tobytes())
                if not self.channel.get_busy():
                    self.channel.play(snd)
                elif self.channel.get_queue() is None:
                    self.channel.queue(snd)
            except Exception as e:  # noqa: BLE001 - mixer gone: no sound rather than a broken game
                log.warning("audio off: %s", e)
                self.ok = False

    def set_muted(self, muted: bool):
        self.muted = muted
        if muted:
            with self._lock:
                self._buf.clear()
                self._buffered = 0
                self._head = 0
                self._running = False

    def close(self):
        if self.device is not None:
            try:
                self.device.pause(1)
                self.device.close()
            except Exception:  # noqa: BLE001
                pass
            self.device = None
