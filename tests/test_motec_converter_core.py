import tempfile
import unittest
from pathlib import Path

from data_log import DataLog, Message
from motec_converter_core import FileSettings, build_args_for_settings, process_log_file


class MotecConverterBeaconTests(unittest.TestCase):
    def test_process_log_file_writes_segment_relative_beacon_sidecar(self):
        data_log = DataLog("Segmented Session")
        data_log.add_channel("Speed", "kph", float, 1)
        data_log.channels["Speed"].messages = [
            Message(0.0, 0.0),
            Message(10.0, 10.0),
            Message(20.0, 20.0),
            Message(30.0, 30.0),
            Message(40.0, 40.0),
        ]

        settings = FileSettings()
        settings.segment_ranges = [(10.0, 30.0)]
        settings.beacon_markers = [5.0, 15.0, 25.0, 35.0]
        args = build_args_for_settings(settings, "Auto")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = str(Path(temp_dir) / "output.ld")
            written_files = process_log_file(
                "source.csv",
                "CSV",
                output_path,
                args,
                settings=settings,
                source_data_log=data_log,
            )

            self.assertEqual([output_path], written_files)
            ldx_text = Path(output_path).with_suffix(".ldx").read_text(encoding="utf-8")
            self.assertIn('Time="5000000.000000"', ldx_text)
            self.assertIn('Time="15000000.000000"', ldx_text)
            self.assertNotIn('Time="25000000.000000"', ldx_text)


if __name__ == "__main__":
    unittest.main()
