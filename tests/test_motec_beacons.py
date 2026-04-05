import tempfile
import unittest
from pathlib import Path

from data_log import DataLog, Message
from motec_beacons import (
    beacon_preview_rows,
    build_gps_trace,
    detect_beacon_crossings,
    write_motec_beacon_file,
)


class MotecBeaconTests(unittest.TestCase):
    def _build_crossing_log(self):
        data_log = DataLog("Beacon Session")
        data_log.add_channel("Latitude", "", float, 6)
        data_log.add_channel("Longitude", "", float, 6)
        data_log.channels["Latitude"].messages = [
            Message(0.0, 0.0),
            Message(10.0, 0.0),
            Message(20.0, 0.0),
            Message(30.0, 0.0),
            Message(40.0, 0.0),
        ]
        data_log.channels["Longitude"].messages = [
            Message(0.0, -0.0002),
            Message(10.0, 0.0002),
            Message(20.0, -0.0002),
            Message(30.0, 0.0002),
            Message(40.0, -0.0002),
        ]
        return data_log

    def test_detect_beacon_crossings_finds_repeated_line_crossings(self):
        trace = build_gps_trace(self._build_crossing_log())
        geo_line = (-0.0001, 0.0, 0.0001, 0.0)

        crossings = detect_beacon_crossings(trace, geo_line)

        self.assertEqual(4, len(crossings))
        self.assertAlmostEqual(5.0, crossings[0].time, places=3)
        self.assertAlmostEqual(15.0, crossings[1].time, places=3)
        self.assertAlmostEqual(25.0, crossings[2].time, places=3)
        self.assertAlmostEqual(35.0, crossings[3].time, places=3)

    def test_beacon_preview_rows_build_full_laps_between_crossings(self):
        trace = build_gps_trace(self._build_crossing_log())
        trace.display_name = "Session A"
        geo_line = (-0.0001, 0.0, 0.0001, 0.0)

        preview_rows, _crossings = beacon_preview_rows([trace], geo_line)

        self.assertEqual(3, len(preview_rows))
        self.assertEqual("Session A", preview_rows[0].file_label)
        self.assertEqual("Lap 1", preview_rows[0].lap_label)
        self.assertAlmostEqual(10.0, preview_rows[0].duration, places=3)

    def test_write_motec_beacon_file_creates_ldx_sidecar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ld_path = Path(temp_dir) / "session.ld"
            ld_path.write_bytes(b"ld")

            ldx_path = write_motec_beacon_file(str(ld_path), [5.0, 15.0, 25.0])

            self.assertEqual(str(ld_path.with_suffix(".ldx")), ldx_path)
            text = Path(ldx_path).read_text(encoding="utf-8")
            self.assertIn('MarkerGroup Name="Beacons"', text)
            self.assertIn('ClassName="BCN"', text)
            self.assertIn('Time="5000000.000000"', text)
            self.assertIn('String Id="Total Laps" Value="4"', text)
            self.assertIn('String Id="Fastest Time" Value="0:10.000"', text)

    def test_write_motec_beacon_file_removes_stale_sidecar_when_no_beacons(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ld_path = Path(temp_dir) / "session.ld"
            ldx_path = ld_path.with_suffix(".ldx")
            ld_path.write_bytes(b"ld")
            ldx_path.write_text("stale", encoding="utf-8")

            result = write_motec_beacon_file(str(ld_path), [])

            self.assertIsNone(result)
            self.assertFalse(ldx_path.exists())


if __name__ == "__main__":
    unittest.main()
