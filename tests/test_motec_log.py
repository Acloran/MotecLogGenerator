import unittest
import warnings

import numpy as np

from data_log import Channel, Message
from motec_log import MotecLog


class MotecLogChannelTests(unittest.TestCase):
    def test_add_channel_uses_quantity_type_as_short_name(self):
        channel = Channel(
            "Vehicle Speed",
            "m/s",
            float,
            1,
            [Message(0.0, 0.0), Message(1.0, 1.0)],
            quantity_type="speed",
        )

        motec_log = MotecLog()
        motec_log.initialize()
        motec_log.add_channel(channel)

        self.assertEqual("speed", motec_log.ld_channels[0].short_name)

    def test_add_channel_clips_extreme_values_without_overflow_warning(self):
        channel = Channel(
            "Vehicle Speed",
            "kph",
            float,
            1,
            [
                Message(0.0, 0.0),
                Message(1.0, 1e50),
                Message(2.0, -1e50),
                Message(3.0, float("inf")),
                Message(4.0, float("-inf")),
                Message(5.0, float("nan")),
            ],
        )

        motec_log = MotecLog()
        motec_log.initialize()

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            motec_log.add_channel(channel)

        stored = motec_log.ld_channels[0]._data
        self.assertEqual(np.float32, stored.dtype.type)
        self.assertTrue(np.isfinite(stored).all())
        self.assertEqual(np.finfo(np.float32).max, stored[1])
        self.assertEqual(np.finfo(np.float32).min, stored[2])
        self.assertEqual(0.0, stored[-1])


if __name__ == "__main__":
    unittest.main()
