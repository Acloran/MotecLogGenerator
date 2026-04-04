import tempfile
import unittest
from pathlib import Path

from data_log import DataLog
from unit_chart import (
    apply_channel_unit_chart,
    ChannelChartEntry,
    load_channel_unit_chart,
    normalize_chart_channel_name,
)


class UnitChartTests(unittest.TestCase):
    def test_load_channel_unit_chart_reads_rows_and_skips_comments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            chart_path = Path(temp_dir) / "channel_units.csv"
            chart_path.write_text(
                "Channel_Name,Quantity_Type,Units\n"
                "# Example,speed,kph\n"
                "Speed,speed,kph\n"
                "Engine RPM,rotspd,rpm\n",
                encoding="utf-8",
            )

            chart = load_channel_unit_chart(chart_path)

        self.assertEqual("speed", chart[normalize_chart_channel_name("Speed")].quantity_type)
        self.assertEqual("kph", chart[normalize_chart_channel_name("Speed")].unit)
        self.assertEqual("rotspd", chart[normalize_chart_channel_name("engine rpm")].quantity_type)
        self.assertEqual("rpm", chart[normalize_chart_channel_name("engine rpm")].unit)
        self.assertNotIn(normalize_chart_channel_name("# Example"), chart)

    def test_load_channel_unit_chart_requires_expected_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            chart_path = Path(temp_dir) / "channel_units.csv"
            chart_path.write_text("name_only\nSpeed\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_channel_unit_chart(chart_path)

    def test_apply_channel_unit_chart_updates_only_matching_channels(self):
        data_log = DataLog()
        data_log.add_channel("Speed", "", float, 1)
        data_log.add_channel("RPM", "", float, 0)
        data_log.add_channel("Coolant Temp", "", float, 1)

        matched_count = apply_channel_unit_chart(
            data_log,
            {
                normalize_chart_channel_name("speed"): ChannelChartEntry(
                    quantity_type="speed",
                    unit="kph",
                ),
                normalize_chart_channel_name("rpm"): ChannelChartEntry(
                    quantity_type="rotspd",
                    unit="rpm",
                ),
            },
        )

        self.assertEqual(2, matched_count)
        self.assertEqual("speed", data_log.channels["Speed"].quantity_type)
        self.assertEqual("kph", data_log.channels["Speed"].units)
        self.assertEqual("rotspd", data_log.channels["RPM"].quantity_type)
        self.assertEqual("rpm", data_log.channels["RPM"].units)
        self.assertEqual("", data_log.channels["Coolant Temp"].quantity_type)
        self.assertEqual("", data_log.channels["Coolant Temp"].units)


if __name__ == "__main__":
    unittest.main()
