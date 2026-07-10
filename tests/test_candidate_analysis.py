import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.candidate_analysis.analyzer import analyze_video
from src.candidate_analysis.audio_analysis import parse_silencedetect_output
from src.candidate_analysis.cache import cache_key, load_cached_analysis, save_cached_analysis
from src.candidate_analysis.cli import main, parse_durations
from src.candidate_analysis.metrics import calculate_metrics
from src.candidate_analysis.schemas import AnalysisConfig, GlobalAnalysis, Interval, WordTimestamp
from src.candidate_analysis.windows import CandidateWindow, generate_windows


class CandidateWindowsTests(unittest.TestCase):
    def test_generates_expected_sliding_windows(self):
        windows = generate_windows(66.0, [60.0], 3.0)
        self.assertEqual(
            windows,
            [CandidateWindow(0.0, 60.0), CandidateWindow(3.0, 63.0), CandidateWindow(6.0, 66.0)],
        )

    def test_windows_never_exceed_video_duration(self):
        windows = generate_windows(121.0, [60.0, 75.0, 120.0], 3.0)
        self.assertTrue(windows)
        self.assertTrue(all(window.end <= 121.0 for window in windows))

    def test_video_shorter_than_candidate_has_no_window(self):
        self.assertEqual(generate_windows(59.999, [60.0], 3.0), [])

    def test_exact_duration_boundary_creates_one_window(self):
        self.assertEqual(generate_windows(60.0, [60.0], 3.0), [CandidateWindow(0.0, 60.0)])

    def test_config_rejects_invalid_parameters(self):
        with self.assertRaises(ValueError):
            AnalysisConfig(step_seconds=0)
        with self.assertRaises(ValueError):
            AnalysisConfig(window_durations_seconds=(0,))


class CandidateMetricTests(unittest.TestCase):
    def setUp(self):
        self.window = CandidateWindow(10.0, 20.0)

    def metrics(self, silences=(), speech=(), words=(), hesitations=("euh", "heu")):
        return calculate_metrics(self.window, silences, speech, words, hesitations)

    def test_silence_ratio_clips_intervals_to_window(self):
        result = self.metrics(silences=(Interval(8, 12), Interval(18, 25)))
        self.assertAlmostEqual(result["silence_ratio"], 0.4)

    def test_longest_silence_is_longest_continuous_merged_interval(self):
        result = self.metrics(silences=(Interval(11, 13), Interval(12, 15), Interval(17, 18)))
        self.assertAlmostEqual(result["longest_silence_seconds"], 4.0)

    def test_speech_density_uses_independent_speech_timeline(self):
        result = self.metrics(
            silences=(Interval(10, 12),),
            speech=(Interval(12, 17),),
        )
        self.assertAlmostEqual(result["silence_ratio"], 0.2)
        self.assertAlmostEqual(result["speech_density"], 0.5)
        self.assertNotAlmostEqual(result["speech_density"], 1 - result["silence_ratio"])

    def test_wpm_uses_active_speech_seconds(self):
        words = tuple(WordTimestamp(word, 11 + index, 11.2 + index) for index, word in enumerate(("un", "deux", "trois", "quatre")))
        result = self.metrics(speech=(Interval(10, 12), Interval(14, 16)), words=words)
        self.assertAlmostEqual(result["words_per_minute"], 60.0)

    def test_zero_active_speech_avoids_division_by_zero(self):
        result = self.metrics(words=(WordTimestamp("mot", 11, 11.2),))
        self.assertEqual(result["words_per_minute"], 0.0)

    def test_hesitation_ratio_uses_configured_expressions(self):
        words = (
            WordTimestamp("Euh,", 10, 10.2),
            WordTimestamp("bonjour", 11, 11.2),
            WordTimestamp("tu", 12, 12.2),
            WordTimestamp("vois", 13, 13.2),
        )
        result = self.metrics(speech=(Interval(10, 14),), words=words, hesitations=("euh", "tu vois"))
        self.assertAlmostEqual(result["hesitation_ratio"], 0.5)

    def test_overlapping_or_duplicate_hesitations_stay_bounded(self):
        words = tuple(WordTimestamp("euh", 10 + index, 10.1 + index) for index in range(3))
        result = self.metrics(
            speech=(Interval(10, 13),),
            words=words,
            hesitations=("euh", "euh", "euh euh"),
        )
        self.assertEqual(result["hesitation_ratio"], 2 / 3)

    def test_startup_latency_uses_first_detected_speech(self):
        result = self.metrics(speech=(Interval(12.4, 14), Interval(16, 18)))
        self.assertAlmostEqual(result["startup_latency_seconds"], 2.4)

    def test_speech_already_active_at_window_start_has_zero_latency(self):
        result = self.metrics(speech=(Interval(8, 12),))
        self.assertEqual(result["startup_latency_seconds"], 0.0)

    def test_exact_word_boundaries_are_half_open(self):
        words = (
            WordTimestamp("avant", 9.999, 10.1),
            WordTimestamp("debut", 10.0, 10.2),
            WordTimestamp("fin", 20.0, 20.2),
        )
        result = self.metrics(speech=(Interval(10, 20),), words=words)
        self.assertAlmostEqual(result["words_per_minute"], 6.0)

    def test_candidate_without_speech_has_numeric_defaults(self):
        result = self.metrics()
        self.assertEqual(result["speech_density"], 0.0)
        self.assertEqual(result["words_per_minute"], 0.0)
        self.assertEqual(result["hesitation_ratio"], 0.0)
        self.assertEqual(result["startup_latency_seconds"], 10.0)

    def test_fully_silent_candidate(self):
        result = self.metrics(silences=(Interval(0, 30),))
        self.assertEqual(result["silence_ratio"], 1.0)
        self.assertEqual(result["longest_silence_seconds"], 10.0)


class GlobalAnalysisAndCacheTests(unittest.TestCase):
    def test_parses_ffmpeg_silence_including_open_interval_at_eof(self):
        output = "silence_start: 1.5\nsilence_end: 3.0 | silence_duration: 1.5\nsilence_start: 8.0"
        self.assertEqual(parse_silencedetect_output(output, 10.0), (Interval(1.5, 3.0), Interval(8.0, 10.0)))

    def test_cache_round_trip(self):
        analysis = GlobalAnalysis(60, (Interval(0, 1),), (Interval(1, 2),), (WordTimestamp("mot", 1, 2),), "mock", "tiny")
        with tempfile.TemporaryDirectory() as directory:
            path = save_cached_analysis(Path(directory), "key", analysis)
            self.assertTrue(path.exists())
            self.assertEqual(load_cached_analysis(Path(directory), "key"), analysis)

    def test_cache_key_changes_when_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"first")
            first = cache_key(video, {"model": "tiny"})
            video.write_bytes(b"second-longer")
            second = cache_key(video, {"model": "tiny"})
            self.assertNotEqual(first, second)

    def test_analyzer_uses_cached_global_analysis_and_returns_json_ready_payload(self):
        calls = []
        global_result = GlobalAnalysis(
            60,
            (Interval(0, 6),),
            (Interval(1, 51),),
            (WordTimestamp("bonjour", 1, 1.3),),
            "mock-local",
            "tiny",
        )

        def fake_global(path, config):
            calls.append(path)
            return global_result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mp4"
            video.write_bytes(b"fixture")
            config = AnalysisConfig(window_durations_seconds=(60,), cache_dir=root / "cache")
            first = analyze_video(video, config, global_analyzer=fake_global)
            second = analyze_video(video, config, global_analyzer=fake_global)

        self.assertEqual(len(calls), 1)
        self.assertFalse(first["global_analysis"]["cache_hit"])
        self.assertTrue(second["global_analysis"]["cache_hit"])
        self.assertEqual(len(first["candidates"]), 1)
        json.dumps(first)
        self.assertEqual(set(first["candidates"][0]["metrics"]), {
            "silence_ratio", "longest_silence_seconds", "speech_density",
            "words_per_minute", "hesitation_ratio", "startup_latency_seconds",
        })


class CandidateAnalysisCliTests(unittest.TestCase):
    def test_parse_durations(self):
        self.assertEqual(parse_durations("60,75,90"), (60.0, 75.0, 90.0))

    @patch("src.candidate_analysis.cli.analyze_video")
    def test_cli_passes_configurable_parameters_and_writes_json(self, mocked_analyze):
        mocked_analyze.return_value = {"source_video": "video.mp4", "candidates": []}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            exit_code = main([
                "analyze", "video.mp4", "--step", "4", "--durations", "60,90",
                "--silence-threshold-db", "-32", "--model", "tiny", "--output", str(output),
            ])
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["candidates"], [])
        config = mocked_analyze.call_args.args[1]
        self.assertEqual(config.step_seconds, 4)
        self.assertEqual(config.window_durations_seconds, (60.0, 90.0))
        self.assertEqual(config.silence_threshold_db, -32)
        self.assertEqual(config.transcription_model, "tiny")


if __name__ == "__main__":
    unittest.main()
