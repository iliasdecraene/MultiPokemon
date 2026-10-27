"""Stream the emulator's per-frame audio samples to the sound card through pygame.mixer.

The game makes ~800 samples per frame. They collect in a reserve and are handed to one mixer channel in chunks of
50 ms (one playing + one queued + one spare in the reserve). Nothing is thrown away while less than ~300 ms is
waiting (only the oldest audio, if the game gets far ahead), and after running dry we wait for 3 chunks before
starting again, so short hiccups don't turn into constant crackling. Sound lags the picture by ~0.1-0.15 s.

Why not an SDL audio callback (pygame._sdl2 AudioDevice, used in 0.6.5/0.6.6): its callback runs Python code on the
sound card's thread. Closing/pausing that device from the main thread while the callback waits for Python's lock
deadlocks (seen in tests: the game froze on close). SDL_mixer's own callback is C code and never needs that lock.
"""
from __future__ import annotations

import collections
import logging

import numpy as np
import pygame

log = logging.getLogger("yellowlink.audio")

SAMPLE_RATE = 48000
CHANNELS = 2
CHUNK = 2400                          # samples per queued Sound (50 ms)
START_AT = 3 * CHUNK                  # (re)start with one chunk playing, one queued and one spare
MAX_BUFFERED = SAMPLE_RATE * 3 // 10  # ~300 ms: beyond this the oldest audio is dropped...
TARGET = START_AT                     # ...down to ~150 ms
GAME_CHANNEL = 0                      # the game's stream
FX_CHANNEL = 1                        # our own sounds (the shiny jingle): never touches the game's stream, own volume


class AudioOut:
    def __init__(self, volume: int = 60):
        self.ok = False
        self.volume = max(0, min(100, volume)) / 100
        self.muted = False
        self.channel = None
        self.fx_channel = None
        self.underruns = 0
        self._buf: collections.deque = collections.deque()    # int16 arrays (n, 2)
        self._buffered = 0
        self._started = False
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(SAMPLE_RATE, -16, CHANNELS, 1024)
            if pygame.mixer.get_num_channels() <= FX_CHANNEL:
                pygame.mixer.set_num_channels(8)
            pygame.mixer.set_reserved(FX_CHANNEL + 1)       # 0 = the game, 1 = our effects: Sound.play() never takes them
            self.channel = pygame.mixer.Channel(GAME_CHANNEL)
            self.fx_channel = pygame.mixer.Channel(FX_CHANNEL)
            self.ok = True
        except Exception as e:  # noqa: BLE001 - no sound card at all: play silently, never crash
            log.warning("no sound (%s)", e)
            self.channel = None

    # --- game side ---------------------------------------------------------------------------------------
    def push(self, samples):
        """samples: numpy int8 array (n, 2) from the emulator."""
        if not self.ok or samples is None or len(samples) == 0 or self.muted:
            return
        arr = (samples.astype(np.int16) * int(256 * self.volume)).astype(np.int16).reshape(-1, CHANNELS)
        self._buf.append(arr)
        self._buffered += len(arr)
        if self._buffered > MAX_BUFFERED:          # far ahead of the sound card: drop the oldest audio
            self._take(self._buffered - TARGET)
        try:
            self._feed()
        except Exception as e:  # noqa: BLE001 - mixer gone: no sound rather than a broken game
            log.warning("audio off: %s", e)
            self.ok = False

    def _take(self, n: int):
        """Remove and return the oldest n samples (as one array)."""
        out, need = [], n
        while need > 0 and self._buf:
            a = self._buf[0]
            if len(a) <= need:
                out.append(self._buf.popleft())
                need -= len(a)
            else:
                out.append(a[:need])
                self._buf[0] = a[need:]
                need = 0
        self._buffered -= n - need
        return np.concatenate(out) if out else np.zeros((0, CHANNELS), np.int16)

    def _sound(self):
        chunk = self._take(CHUNK)
        return pygame.mixer.Sound(buffer=np.ascontiguousarray(chunk).tobytes())

    def _feed(self):
        ch = self.channel
        if not ch.get_busy():
            if self._started:                       # it ran dry: refill a little before starting again
                self._started = False
                self.underruns += 1
            if self._buffered < START_AT:
                return
            ch.play(self._sound())
            self._started = True
        if ch.get_queue() is None and self._buffered >= CHUNK:
            ch.queue(self._sound())

    def play_fx(self, sound) -> bool:
        """Play one of our own sounds (shiny jingle) on its own channel at the sound's own fixed volume: the volume
        slider, mute and the game's stream (channel 0) don't affect it. DS too (DeSmuME has its own device).
        False without a sound card / mixer (never raises)."""
        if not self.ok or self.fx_channel is None or sound is None:
            return False
        try:
            self.fx_channel.set_volume(1.0)
            self.fx_channel.play(sound)
            return True
        except Exception as e:  # noqa: BLE001 - a sound effect must never break the game
            log.warning("sound effect failed: %s", e)
            return False

    def set_muted(self, muted: bool):
        self.muted = muted
        if muted:
            self._buf.clear()
            self._buffered = 0
            if self.channel is not None:
                try:
                    self.channel.stop()
                except Exception:  # noqa: BLE001
                    pass

    def close(self):
        self._buf.clear()
        self._buffered = 0
        for ch in (self.channel, self.fx_channel):
            if ch is not None:
                try:
                    ch.stop()
                except Exception:  # noqa: BLE001
                    pass
