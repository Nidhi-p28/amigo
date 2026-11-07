"""Pure audio helpers used by the offline Amigo microphone pipeline."""
from __future__ import annotations

import array
import sys


def resample_pcm16_mono(audio: bytes, source_rate: int, target_rate: int = 16000) -> bytes:
    """Convert raw mono signed-16-bit PCM to a target rate using linear interpolation.

    This keeps Vosk receiving 16 kHz samples even when a Bluetooth headset or USB
    microphone only supports 44.1/48 kHz input. This is a lightweight fallback;
    it is not intended as a studio-quality resampler.
    """
    if not audio or source_rate == target_rate:
        return audio
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Sample rates must be positive integers.")
    usable_length = len(audio) - (len(audio) % 2)
    samples = array.array("h")
    samples.frombytes(audio[:usable_length])
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) < 2:
        return b""

    output_count = max(1, round(len(samples) * target_rate / source_rate))
    converted = array.array("h")
    ratio = source_rate / target_rate
    last = len(samples) - 1
    for index in range(output_count):
        position = min(index * ratio, last)
        left = int(position)
        right = min(left + 1, last)
        fraction = position - left
        value = round(samples[left] * (1.0 - fraction) + samples[right] * fraction)
        converted.append(max(-32768, min(32767, value)))
    if sys.byteorder != "little":
        converted.byteswap()
    return converted.tobytes()
