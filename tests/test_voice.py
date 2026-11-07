import struct
import unittest

from app.audio_utils import resample_pcm16_mono
from app.wakeword import command_after_wake_word, contains_wake_word


class WakeWordMatchingTests(unittest.TestCase):
    def test_recognizes_standalone_word(self):
        self.assertTrue(contains_wake_word("amigo"))

    def test_recognizes_punctuation_and_sentence_context(self):
        self.assertTrue(contains_wake_word("Hey, Amigo!"))
        self.assertTrue(contains_wake_word("amigo open chrome"))

    def test_recognizes_common_vosk_split_transcription(self):
        self.assertTrue(contains_wake_word("a me go"))

    def test_does_not_match_longer_word(self):
        self.assertFalse(contains_wake_word("amigos"))
        self.assertFalse(contains_wake_word("friend"))

    def test_extracts_command_spoken_after_wake_word(self):
        self.assertEqual(command_after_wake_word("Amigo, open Chrome"), "open Chrome")
        self.assertEqual(command_after_wake_word("Hey Amigo create a folder"), "create a folder")
        self.assertEqual(command_after_wake_word("Amigo"), "")


class AudioResamplingTests(unittest.TestCase):
    def test_same_rate_is_returned_unchanged(self):
        audio = struct.pack("<hhhh", 0, 100, -100, 50)
        self.assertEqual(resample_pcm16_mono(audio, 16000), audio)

    def test_48khz_audio_is_converted_to_16khz(self):
        audio = b"".join(struct.pack("<h", 1000) for _ in range(4800))
        converted = resample_pcm16_mono(audio, 48000, 16000)
        self.assertEqual(len(converted) // 2, 1600)
        values = struct.unpack("<" + "h" * (len(converted) // 2), converted)
        self.assertTrue(all(value == 1000 for value in values))

    def test_invalid_sample_rate_is_rejected(self):
        with self.assertRaises(ValueError):
            resample_pcm16_mono(b"\x00\x00", 0, 16000)


if __name__ == "__main__":
    unittest.main()
