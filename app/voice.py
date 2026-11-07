from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .audio_utils import resample_pcm16_mono
from .wakeword import command_after_wake_word, contains_wake_word


WAKE_WORD = "Amigo"
_MODEL_CACHE: dict[str, object] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def list_input_devices() -> list[tuple[int, str, bool]]:
    """Return microphone devices and whether 16 kHz mono input is supported."""
    try:
        import sounddevice as sd
    except ImportError:
        return []
    result: list[tuple[int, str, bool]] = []
    try:
        devices = sd.query_devices()
    except Exception:
        return result
    for index, device in enumerate(devices):
        try:
            if int(device.get("max_input_channels", 0)) < 1:
                continue
            compatible = True
            try:
                sd.check_input_settings(
                    device=index, samplerate=16000, channels=1, dtype="int16"
                )
            except Exception:
                compatible = False
            result.append((index, str(device.get("name") or f"Microphone {index}"), compatible))
        except Exception:
            continue
    return result


def _input_sample_rate(sd, input_device: int | None) -> int:
    """Choose a sample rate supported by the chosen microphone; Vosk input is resampled later."""
    for sample_rate in (16000, 48000, 44100, 32000, 24000, 22050, 8000):
        try:
            sd.check_input_settings(
                device=input_device, samplerate=sample_rate, channels=1, dtype="int16"
            )
            return sample_rate
        except Exception:
            continue
    try:
        info = sd.query_devices(input_device, "input")
        return max(8000, int(info.get("default_samplerate") or 48000))
    except Exception:
        return 16000


def _load_local_model(model_dir: Path, model_class, set_log_level) -> object:
    """Load the Vosk model once per process to avoid disk-load latency on each popup."""
    cache_key = str(model_dir.resolve())
    with _MODEL_CACHE_LOCK:
        if cache_key not in _MODEL_CACHE:
            set_log_level(-1)
            _MODEL_CACHE[cache_key] = model_class(str(model_dir))
        return _MODEL_CACHE[cache_key]


class WakeWordWorker(QThread):
    """Always-on, local wake-word listener. Stops after one match to release the mic."""

    status = Signal(str)
    wake_detected = Signal(str)
    failed = Signal(str)
    SAMPLE_RATE = 16000

    def __init__(self, model_dir: Path, input_device: int | None = None) -> None:
        super().__init__()
        self.model_dir = model_dir
        self.input_device = input_device
        self._stop_event = threading.Event()

    def stop_listening(self) -> None:
        self._stop_event.set()

    @staticmethod
    def _partial_text(recognizer) -> str:
        try:
            packet = json.loads(recognizer.PartialResult())
            return str(packet.get("partial") or "").strip()
        except (ValueError, TypeError, AttributeError):
            return ""

    def run(self) -> None:
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError:
            self.failed.emit("Wake word needs the installed sounddevice and vosk packages. Reinstall requirements.txt.")
            return

        if not self.model_dir.is_dir():
            self.failed.emit("The offline voice model is missing. Run setup_voice.ps1 in the Amigo project folder.")
            return

        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=12)
        try:
            self.status.emit("Loading local wake-word listener…")
            model = _load_local_model(self.model_dir, Model, SetLogLevel)
            # Use the small offline English model with its normal vocabulary so a command
            # spoken immediately after “Amigo” can be retained as part of the same utterance.
            recognizer = KaldiRecognizer(model, self.SAMPLE_RATE)
            input_sample_rate = _input_sample_rate(sd, self.input_device)
            candidate_partial = ""

            def audio_callback(indata, frames, timing, status) -> None:
                try:
                    audio_queue.put_nowait(bytes(indata))
                except queue.Full:
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        audio_queue.put_nowait(bytes(indata))
                    except queue.Full:
                        pass

            with sd.RawInputStream(
                device=self.input_device,
                samplerate=input_sample_rate,
                blocksize=4000,
                dtype="int16",
                channels=1,
                callback=audio_callback,
            ):
                self.status.emit("Wake word active · say ‘Amigo’ to open Amigo")
                while not self._stop_event.is_set():
                    try:
                        audio = audio_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue

                    audio = resample_pcm16_mono(audio, input_sample_rate, self.SAMPLE_RATE)
                    if not audio:
                        continue
                    if recognizer.AcceptWaveform(audio):
                        try:
                            final_text = str(json.loads(recognizer.Result()).get("text") or "").strip()
                        except (ValueError, TypeError):
                            final_text = ""
                        utterance_candidates = [
                            value for value in (final_text, candidate_partial) if contains_wake_word(value)
                        ]
                        utterance = max(utterance_candidates, key=len, default="")
                        if contains_wake_word(utterance):
                            self.wake_detected.emit(command_after_wake_word(utterance))
                            return
                        candidate_partial = ""
                    else:
                        partial_text = self._partial_text(recognizer)
                        if contains_wake_word(partial_text):
                            # Wait for this utterance to finish so “Amigo, open Chrome”
                            # can be forwarded as one command instead of losing its tail.
                            candidate_partial = partial_text
        except Exception as exc:
            if not self._stop_event.is_set():
                self.failed.emit(
                    "Amigo couldn't start wake-word listening. Check Windows microphone permissions and the selected input device. "
                    f"Details: {exc}"
                )


class VoiceInputWorker(QThread):
    """Offline speech-to-text with manual or silence-ended recording."""

    status = Signal(str)
    transcribed = Signal(str)
    failed = Signal(str)
    SAMPLE_RATE = 16000

    def __init__(
        self,
        model_dir: Path,
        *,
        auto_stop_on_silence: bool = False,
        silence_seconds: float = 1.8,
        wait_for_speech_seconds: float = 8.0,
        max_seconds: float = 25.0,
        input_device: int | None = None,
    ) -> None:
        super().__init__()
        self.model_dir = model_dir
        self.input_device = input_device
        self.auto_stop_on_silence = auto_stop_on_silence
        self.silence_seconds = silence_seconds
        self.wait_for_speech_seconds = wait_for_speech_seconds
        self.max_seconds = max_seconds if not auto_stop_on_silence else min(max_seconds, 18.0)
        self._stop_event = threading.Event()

    def stop_recording(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError:
            self.failed.emit("Voice packages are missing. Run the install command in the Amigo setup instructions.")
            return

        if not self.model_dir.is_dir():
            self.failed.emit("The offline voice model is not installed yet. Run setup_voice.ps1 from the Amigo project folder.")
            return

        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=30)
        try:
            self.status.emit("Loading offline voice model…")
            model = _load_local_model(self.model_dir, Model, SetLogLevel)
            recognizer = KaldiRecognizer(model, self.SAMPLE_RATE)
            input_sample_rate = _input_sample_rate(sd, self.input_device)

            def audio_callback(indata, frames, timing, status) -> None:
                try:
                    audio_queue.put_nowait(bytes(indata))
                except queue.Full:
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        audio_queue.put_nowait(bytes(indata))
                    except queue.Full:
                        pass

            completed_segments: list[str] = []
            started = time.monotonic()
            last_recognized_change = started
            last_partial = ""
            saw_speech = False
            timed_out_before_speech = False

            with sd.RawInputStream(
                device=self.input_device,
                samplerate=input_sample_rate,
                blocksize=4000,
                dtype="int16",
                channels=1,
                callback=audio_callback,
            ):
                if self.auto_stop_on_silence:
                    self.status.emit("Listening · speak your command, then pause")
                else:
                    self.status.emit("Listening · click Finish to stop")

                while not self._stop_event.is_set() and time.monotonic() - started < self.max_seconds:
                    try:
                        audio = audio_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    now = time.monotonic()
                    audio = resample_pcm16_mono(audio, input_sample_rate, self.SAMPLE_RATE)
                    if not audio:
                        continue

                    if recognizer.AcceptWaveform(audio):
                        text = str(json.loads(recognizer.Result()).get("text") or "").strip()
                        if text:
                            completed_segments.append(text)
                            saw_speech = True
                            last_recognized_change = now
                        last_partial = ""
                    else:
                        partial = str(json.loads(recognizer.PartialResult()).get("partial") or "").strip()
                        if partial and partial != last_partial:
                            saw_speech = True
                            last_recognized_change = now
                            last_partial = partial
                        elif not partial:
                            last_partial = ""

                    if self.auto_stop_on_silence:
                        elapsed = now - started
                        if not saw_speech and elapsed >= self.wait_for_speech_seconds:
                            timed_out_before_speech = True
                            break
                        if saw_speech and elapsed >= 0.9 and now - last_recognized_change >= self.silence_seconds:
                            break

            final_text = str(json.loads(recognizer.FinalResult()).get("text") or "").strip()
            if final_text:
                completed_segments.append(final_text)
            transcript = " ".join(completed_segments).strip()
            if transcript:
                self.transcribed.emit(transcript)
            elif timed_out_before_speech:
                self.failed.emit("I didn't hear a command after the wake word. Say ‘Amigo’ again and try speaking after the popup appears.")
            else:
                self.failed.emit("I didn't catch any words. Check your microphone and try again.")
        except Exception as exc:
            self.failed.emit(
                "I couldn't use the microphone. Check Windows microphone permissions and choose the right input device. "
                f"Details: {exc}"
            )


class SpeechOutputWorker(QThread):
    """Read a short response through the Windows offline speech engine."""

    failed = Signal(str)

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text
        self._engine = None
        self._engine_lock = threading.Lock()
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        """Ask the Windows speech engine to stop the current utterance."""
        self._stop_requested.set()
        with self._engine_lock:
            engine = self._engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    def run(self) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init("sapi5")
            with self._engine_lock:
                self._engine = engine
            if self._stop_requested.is_set():
                engine.stop()
                return
            engine.setProperty("rate", 185)
            engine.setProperty("volume", 0.9)
            engine.say(self.text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            if not self._stop_requested.is_set():
                self.failed.emit(f"Couldn't speak the reply: {exc}")
        finally:
            with self._engine_lock:
                self._engine = None
