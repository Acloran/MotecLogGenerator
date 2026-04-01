import tempfile
import unittest
from pathlib import Path

from data_log import Channel, Message
from motec_log_generator import (
    build_output_filenames,
    build_output_path,
    detect_log_type,
    detect_split_ranges,
    normalize_segment_ranges,
    resolve_frequency,
)


class MotecLogGeneratorHelpersTests(unittest.TestCase):
    def test_resolve_frequency_supports_auto(self):
        self.assertIsNone(resolve_frequency("Auto"))
        self.assertIsNone(resolve_frequency(""))
        self.assertEqual(20.0, resolve_frequency("20"))

    def test_detect_log_type_handles_supported_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            can_file = temp_path / "sample.log"
            can_file.write_text("(1.0) can0 123#00\n", encoding="utf-8")

            csv_file = temp_path / "sample.csv"
            csv_file.write_text("Time,Speed\n0.0,10\n", encoding="utf-8")

            accessport_file = temp_path / "sample_accessport.csv"
            accessport_file.write_text(
                "Time (sec),RPM (RPM),AP Info:[sample]\n0.0,1000,0\n",
                encoding="utf-8",
            )

            aim_file = temp_path / "sample.xrk"
            aim_file.write_bytes(b"placeholder")

            self.assertEqual("CAN", detect_log_type(can_file))
            self.assertEqual("CSV", detect_log_type(csv_file))
            self.assertEqual("ACCESSPORT", detect_log_type(accessport_file))
            self.assertEqual("AIM", detect_log_type(aim_file))

    def test_detect_split_ranges_supports_multiple_stops(self):
        channel = Channel(
            "Vehicle Speed",
            "kph",
            float,
            1,
            [
                Message(0.0, 0.0),
                Message(10.0, 20.0),
                Message(20.0, 21.0),
                Message(30.0, 19.0),
                Message(40.0, 0.0),
                Message(55.0, 0.0),
                Message(60.0, 18.0),
                Message(70.0, 19.0),
                Message(80.0, 0.0),
                Message(95.0, 0.0),
                Message(100.0, 16.0),
                Message(110.0, 17.0),
                Message(120.0, 0.0),
            ],
        )

        self.assertEqual(
            [(10.0, 30.0), (60.0, 70.0), (100.0, 110.0)],
            detect_split_ranges(channel),
        )

    def test_normalize_segment_ranges_sorts_clips_and_merges(self):
        self.assertEqual(
            [(0.0, 12.0)],
            normalize_segment_ranges([(-5.0, 5.0), (4.0, 12.0), (20.0, 20.0)], 0.0, 15.0),
        )

    def test_build_output_path_uses_custom_stem(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = build_output_path("/tmp/input.csv", temp_dir, "session_part2")
            self.assertEqual(str(Path(temp_dir) / "session_part2.ld"), output_path)

    def test_build_output_filenames_supports_many_segments(self):
        filenames = build_output_filenames("/tmp/input.csv", None, 4)
        self.assertEqual(
            [
                "/tmp/input_part1.ld",
                "/tmp/input_part2.ld",
                "/tmp/input_part3.ld",
                "/tmp/input_part4.ld",
            ],
            filenames,
        )


if __name__ == "__main__":
    unittest.main()
