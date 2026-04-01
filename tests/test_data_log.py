import unittest
from types import SimpleNamespace

import pyarrow as pa

from data_log import Channel, DataLog, Message
from libxrk import ChannelMetadata


class DataLogParsingTests(unittest.TestCase):
    def test_csv_parser_skips_invalid_samples_without_dropping_channel(self):
        log_lines = [
            "Time,Speed,Coolant Temp\n",
            "0.0,10.5,\n",
            "1.0,11.0,90\n",
            "2.0,12.0,91\n",
        ]

        data_log = DataLog()
        data_log.from_csv_log(log_lines)

        self.assertIn("Speed", data_log.channels)
        self.assertIn("Coolant Temp", data_log.channels)
        self.assertEqual(3, len(data_log.channels["Speed"].messages))
        self.assertEqual(2, len(data_log.channels["Coolant Temp"].messages))

    def test_accessport_parser_preserves_units_and_skips_ap_info(self):
        log_lines = [
            "Time (sec),RPM (RPM),Boost (kPa),AP Info:[sample]\n",
            "0.0,1000,120,0\n",
            "0.1,1100,121,0\n",
        ]

        data_log = DataLog()
        data_log.from_accessport_log(log_lines)

        self.assertIn("RPM", data_log.channels)
        self.assertIn("Boost", data_log.channels)
        self.assertNotIn("AP Info:[sample]", data_log.channels)
        self.assertEqual("RPM", data_log.channels["RPM"].units)
        self.assertEqual("kPa", data_log.channels["Boost"].units)

    def test_aim_log_is_mapped_into_channels_and_metadata(self):
        metadata = ChannelMetadata(units="rpm", dec_pts=0, interpolate=True).to_field_metadata()
        schema = pa.schema(
            [
                pa.field("timecodes", pa.int64()),
                pa.field("Engine RPM", pa.float32(), metadata=metadata),
            ]
        )
        table = pa.table(
            {
                "timecodes": pa.array([0, 100, 200], type=pa.int64()),
                "Engine RPM": pa.array([1000.0, 1050.0, 1100.0], type=pa.float32()),
            }
        ).cast(schema)

        aim_log = SimpleNamespace(
            file_name="session.xrk",
            channels={"Engine RPM": table},
            metadata={"Driver": "Test Driver", "Venue": "Test Track"},
        )

        data_log = DataLog()
        data_log.from_aim_log(aim_log)

        self.assertEqual("Test Driver", data_log.metadata["Driver"])
        self.assertIn("Engine RPM", data_log.channels)
        self.assertEqual("rpm", data_log.channels["Engine RPM"].units)
        self.assertEqual(3, len(data_log.channels["Engine RPM"].messages))
        self.assertAlmostEqual(0.2, data_log.channels["Engine RPM"].messages[-1].timestamp)

    def test_speed_channel_detection_prefers_vehicle_speed_over_wheel_speeds(self):
        data_log = DataLog()
        data_log.add_channel("WS_FL", "kph", float, 1)
        data_log.channels["WS_FL"].messages = [Message(0.0, 0.0), Message(1.0, 40.0)]
        data_log.add_channel("Vehicle Speed", "kph", float, 1)
        data_log.channels["Vehicle Speed"].messages = [Message(0.0, 0.0), Message(1.0, 38.0)]

        self.assertEqual("Vehicle Speed", data_log.infer_speed_channel())

    def test_extract_segment_rebases_time(self):
        data_log = DataLog()
        data_log.add_channel("Speed", "kph", float, 1)
        data_log.channels["Speed"].messages = [
            Message(0.0, 0.0),
            Message(5.0, 10.0),
            Message(10.0, 20.0),
        ]

        segment = data_log.extract_segment(5.0, 10.0, rebase_time=True)

        self.assertEqual(2, len(segment.channels["Speed"].messages))
        self.assertAlmostEqual(0.0, segment.channels["Speed"].messages[0].timestamp)
        self.assertAlmostEqual(5.0, segment.channels["Speed"].messages[-1].timestamp)


class ChannelResampleTests(unittest.TestCase):
    def test_single_sample_channel_is_not_erased_by_resample(self):
        channel = Channel("RPM", "rpm", float, 0, [Message(1.5, 2500.0)])

        channel.resample(1.5, 1.5, 20)

        self.assertEqual(1, len(channel.messages))
        self.assertAlmostEqual(1.5, channel.messages[0].timestamp)
        self.assertAlmostEqual(2500.0, channel.messages[0].value)


if __name__ == "__main__":
    unittest.main()
