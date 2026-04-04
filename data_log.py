import copy
import csv
import math
import os
import re

try:
    import cantools
except ImportError:
    cantools = None

try:
    from libxrk import ChannelMetadata, aim_xrk
except ImportError:
    ChannelMetadata = None
    aim_xrk = None


SPEED_UNITS = {
    "km h",
    "kmh",
    "kph",
    "mph",
    "m s",
    "mps",
    "knot",
    "knots",
    "kt",
    "kts",
}
SPEED_EXACT_NAMES = {
    "speed",
    "vehicle speed",
    "gps speed",
    "ground speed",
    "car speed",
}
WHEEL_SPEED_HINTS = {
    "wheel speed",
    "ws fl",
    "ws fr",
    "ws rl",
    "ws rr",
    "speed fl",
    "speed fr",
    "speed rl",
    "speed rr",
}
WHEEL_TOKENS = {"fl", "fr", "rl", "rr", "lf", "rf", "lr"}


def normalize_label(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


class DataLog(object):
    """Container for storing log data which contains a set of channels with time series data."""

    def __init__(self, name=""):
        self.name = name
        self.channels = {}
        self.metadata = {}

    def clear(self):
        self.channels = {}
        self.metadata = {}

    def copy(self):
        copied = DataLog(self.name)
        copied.metadata = copy.deepcopy(self.metadata)
        for channel_name, channel in self.channels.items():
            copied.channels[channel_name] = channel.copy()
        return copied

    def add_channel(self, name, units, data_type, decimals, initial_message=None, quantity_type=""):
        channel_name = self.__unique_channel_name(name)
        msg = [] if not initial_message else [initial_message]
        self.channels[channel_name] = Channel(
            channel_name,
            units,
            data_type,
            decimals,
            msg,
            quantity_type=quantity_type,
        )
        return channel_name

    def channel_names(self):
        return list(self.channels.keys())

    def get_channel(self, channel_name):
        return self.channels.get(channel_name)

    def start(self):
        """Returns the earliest timestamp from all existing channels [s]."""
        t = math.inf
        for channel in self.channels.values():
            t = min(t, channel.start())

        if t != math.inf:
            return t
        return 0.0

    def end(self):
        """Returns the latest timestamp from all existing channels [s]."""
        end = 0
        for channel in self.channels.values():
            end = max(end, channel.end())

        return end

    def duration(self):
        """Returns the duration of the log [s]."""
        return self.end() - self.start()

    def extract_segment(self, start_time=None, end_time=None, rebase_time=True):
        """Creates a new DataLog containing only samples inside the requested time window."""
        extracted = DataLog(self.name)
        extracted.metadata = copy.deepcopy(self.metadata)

        log_start = self.start()
        log_end = self.end()
        actual_start = log_start if start_time is None else max(log_start, float(start_time))
        actual_end = log_end if end_time is None else min(log_end, float(end_time))

        if actual_end < actual_start:
            return extracted

        time_offset = actual_start if rebase_time else 0.0
        for channel_name, channel in self.channels.items():
            filtered_messages = [
                Message(message.timestamp - time_offset, message.value)
                for message in channel.messages
                if actual_start <= message.timestamp <= actual_end
            ]
            if filtered_messages:
                extracted.channels[channel_name] = Channel(
                    channel_name,
                    channel.units,
                    channel.data_type,
                    channel.decimals,
                    filtered_messages,
                    quantity_type=channel.quantity_type,
                )

        return extracted

    def infer_speed_channel(self):
        """Returns the most likely speed channel name for preview/trim operations."""
        best_channel = None
        best_score = -10**9

        for channel_name, channel in self.channels.items():
            if not channel.messages:
                continue

            normalized_name = normalize_label(channel_name)
            normalized_units = normalize_label(channel.units)
            score = 0

            if normalized_name in SPEED_EXACT_NAMES:
                score += 400
            if "vehicle" in normalized_name and "speed" in normalized_name:
                score += 260
            if "gps" in normalized_name and "speed" in normalized_name:
                score += 240
            if "ground" in normalized_name and "speed" in normalized_name:
                score += 180
            if "speed" in normalized_name:
                score += 140
            if "velocity" in normalized_name:
                score += 100
            if normalized_units in SPEED_UNITS:
                score += 90
            if any(hint == normalized_name for hint in WHEEL_SPEED_HINTS):
                score -= 220
            if any(token in normalized_name.split() for token in WHEEL_TOKENS) and "wheel" in normalized_name:
                score -= 180
            if "shaft" in normalized_name or "fan" in normalized_name:
                score -= 80

            values = [message.value for message in channel.messages]
            value_span = max(values) - min(values) if values else 0
            if value_span > 0:
                score += 15
            if len(channel.messages) > 50:
                score += 8

            if score > best_score:
                best_score = score
                best_channel = channel_name

        if best_channel:
            return best_channel

        for channel_name, channel in self.channels.items():
            if channel.messages:
                return channel_name
        return None

    def resample(self, frequency=None):
        """Resamples all channels such that all messages occur at a fixed frequency.

        If frequency is None, each channel will be resampled to its own average frequency.
        See the resample method of the Channel class for more details.
        """
        start = self.start()
        end = self.end()
        for channel in self.channels.values():
            target_freq = frequency
            if not target_freq:
                target_freq = max(1.0, round(channel.avg_frequency()))
            channel.resample(start, end, target_freq)

    def load_file(self, log_path, log_type, can_db=None):
        """Loads a supported source file into this DataLog."""
        self.name = os.path.splitext(os.path.basename(log_path))[0]

        if log_type == "CAN":
            if cantools is None:
                raise RuntimeError("cantools is not installed; CAN log support is unavailable.")
            with open(log_path, "r", encoding="utf-8-sig", errors="ignore", newline="") as file:
                self.from_can_log(file, can_db)
            return

        if log_type == "CSV":
            with open(log_path, "r", encoding="utf-8-sig", errors="ignore", newline="") as file:
                self.from_csv_log(file)
            return

        if log_type == "ACCESSPORT":
            with open(log_path, "r", encoding="utf-8-sig", errors="ignore", newline="") as file:
                self.from_accessport_log(file)
            return

        if log_type == "AIM":
            self.from_aim_file(log_path)
            return

        raise ValueError("Unsupported log type: %s" % log_type)

    def from_can_log(self, log_lines, can_db):
        """Creates channels populated with messages from a candump file and can database."""
        self.clear()

        if not can_db:
            raise ValueError("CAN log parsing requires a loaded DBC database.")

        known_ids = {msg.frame_id for msg in can_db.messages}

        for line_number, line in enumerate(log_lines, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                stamp, _bus, frame_id, data = self.__parse_can_log_line(line)
            except ValueError:
                print("WARNING: Skipping malformed CAN line %d" % line_number)
                continue

            if frame_id not in known_ids:
                continue

            try:
                db_msg = can_db.get_message_by_frame_id(frame_id)
                msg_decoded = can_db.decode_message(frame_id, data, decode_choices=False)
            except Exception as exc:
                print(
                    "WARNING: Failed to decode CAN line %d (0x%X): %s"
                    % (line_number, frame_id, exc)
                )
                continue

            for signal in db_msg.signals:
                if signal.name not in msg_decoded:
                    continue

                try:
                    value = float(msg_decoded[signal.name])
                except (TypeError, ValueError):
                    continue

                if signal.name in self.channels:
                    self.channels[signal.name].messages.append(Message(stamp, value))
                else:
                    self.add_channel(signal.name, signal.unit or "", float, 3, Message(stamp, value))

    def from_csv_log(self, log_lines):
        """Creates channels populated with messages from a CSV log file."""
        self._from_delimited_log(log_lines, self.__parse_csv_header)

    def from_accessport_log(self, log_lines):
        """Creates channels populated with messages from a COBB Accessport CSV log file."""
        self._from_delimited_log(log_lines, self.__parse_accessport_header)

    def from_aim_file(self, log_path):
        """Creates channels populated with messages from an AIM XRK/XRZ telemetry file."""
        if aim_xrk is None or ChannelMetadata is None:
            raise RuntimeError(
                "libxrk is not installed; AIM XRK/XRZ support is unavailable."
            )

        self.from_aim_log(aim_xrk(log_path))

    def from_aim_log(self, aim_log):
        """Creates channels populated with messages from a parsed libxrk LogFile."""
        if ChannelMetadata is None:
            raise RuntimeError(
                "libxrk metadata helpers are unavailable; AIM XRK/XRZ support cannot run."
            )

        self.clear()
        self.name = os.path.splitext(os.path.basename(aim_log.file_name))[0]
        self.metadata = dict(getattr(aim_log, "metadata", {}) or {})

        for channel_name, channel_table in aim_log.channels.items():
            if not channel_table.num_rows:
                continue

            value_column = next(
                (name for name in channel_table.column_names if name != "timecodes"),
                None,
            )
            if not value_column:
                continue

            metadata = ChannelMetadata.from_channel_table(channel_table)
            timecodes = channel_table.column("timecodes").to_pylist()
            values = channel_table.column(value_column).to_pylist()

            messages = []
            for timestamp_ms, value in zip(timecodes, values):
                if timestamp_ms is None or value is None:
                    continue

                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue

                messages.append(Message(float(timestamp_ms) / 1000.0, numeric_value))

            if not messages:
                continue

            unique_name = self.add_channel(
                channel_name,
                metadata.units,
                float,
                metadata.dec_pts,
            )
            self.channels[unique_name].messages = messages

    def _from_delimited_log(self, log_lines, header_parser):
        self.clear()

        reader = csv.reader(log_lines)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
        if not rows:
            return

        header = [cell.lstrip("\ufeff").strip() for cell in rows[0]]
        if len(header) < 2:
            return

        channel_columns = []
        invalid_counts = {}

        for column_index, raw_name in enumerate(header[1:], start=1):
            parsed = header_parser(raw_name)
            if not parsed:
                continue

            name, units = parsed
            if not name:
                continue

            unique_name = self.add_channel(name, units, float, 0)
            channel_columns.append((column_index, unique_name))
            invalid_counts[unique_name] = 0

        if not channel_columns:
            return

        for row_number, row in enumerate(rows[1:], start=2):
            try:
                timestamp = float(row[0].strip())
            except (IndexError, ValueError):
                print("WARNING: Skipping row %d because the time value is invalid." % row_number)
                continue

            for column_index, channel_name in channel_columns:
                raw_value = row[column_index].strip() if column_index < len(row) else ""
                if raw_value == "":
                    invalid_counts[channel_name] += 1
                    continue

                try:
                    numeric_value = float(raw_value)
                except ValueError:
                    invalid_counts[channel_name] += 1
                    continue

                channel = self.channels[channel_name]
                channel.messages.append(Message(timestamp, numeric_value))
                channel.decimals = max(channel.decimals, self.__count_decimals(raw_value))

        empty_channels = []
        for channel_name, channel in self.channels.items():
            if not channel.messages:
                empty_channels.append(channel_name)
                continue

            invalid_count = invalid_counts.get(channel_name, 0)
            if invalid_count:
                print(
                    "WARNING: Skipped %d invalid or missing samples for channel %s"
                    % (invalid_count, channel_name)
                )

        for channel_name in empty_channels:
            print("WARNING: Channel %s had no numeric samples and will be removed" % channel_name)
            del self.channels[channel_name]

    @staticmethod
    def __parse_csv_header(header_name):
        return header_name.strip(), ""

    @staticmethod
    def __parse_accessport_header(header_name):
        header_name = header_name.strip()
        if "AP Info" in header_name:
            return None

        if header_name.endswith(")") and " (" in header_name:
            name, units = header_name.rsplit(" (", 1)
            return name.strip(), units[:-1].strip()

        return header_name, ""

    @staticmethod
    def __count_decimals(value_text):
        value_text = value_text.strip()
        if "." not in value_text:
            return 0
        return len(value_text.rsplit(".", 1)[1])

    @staticmethod
    def __parse_can_log_line(line):
        """Extracts the timestamp, bus, arbitration id, and data from a single CAN log line."""
        stamp, bus, msg = line.split(maxsplit=2)
        stamp = float(stamp[1:-1])
        frame_id, data = msg.split("#", 1)
        frame_id = int(frame_id, 16)
        data = bytearray.fromhex(data)

        return stamp, bus, frame_id, data

    def __unique_channel_name(self, proposed_name):
        base_name = str(proposed_name).strip() or "Channel"
        if base_name not in self.channels:
            return base_name

        index = 2
        while True:
            unique_name = "%s (%d)" % (base_name, index)
            if unique_name not in self.channels:
                return unique_name
            index += 1

    def __str__(self):
        output = "Log: %s, Duration: %f s" % (self.name, (self.end() - self.start()))
        for channel_data in self.channels.values():
            output += "\n\t%s" % channel_data
        return output


class Channel(object):
    """Represents a single channel of data containing a time series of values."""

    def __init__(self, name, units, data_type, decimals, messages=None, quantity_type=""):
        self.name = str(name)
        self.units = str(units)
        self.quantity_type = str(quantity_type)
        self.data_type = data_type
        self.decimals = decimals
        if messages:
            self.messages = messages
        else:
            self.messages = []

    def copy(self):
        return Channel(
            self.name,
            self.units,
            self.data_type,
            self.decimals,
            [Message(message.timestamp, message.value) for message in self.messages],
            quantity_type=self.quantity_type,
        )

    def start(self):
        if self.messages:
            return self.messages[0].timestamp
        return 0

    def end(self):
        if self.messages:
            return self.messages[-1].timestamp
        return 0

    def avg_frequency(self):
        """Computes the average sample frequency for this channel."""
        if len(self.messages) >= 2:
            dt = self.end() - self.start()
            if dt > 0:
                return (len(self.messages) - 1) / dt
        return 0

    def resample(self, start_time, end_time, frequency):
        """Resamples the data such that all messages occur at a fixed frequency."""
        if not self.messages or not frequency or frequency <= 0:
            return

        if len(self.messages) == 1 or end_time <= start_time:
            self.messages = [Message(start_time, self.messages[-1].value)]
            return

        num_msgs = max(1, math.floor(frequency * (end_time - start_time)))
        dt_step = 1.0 / frequency

        value = 0
        t = start_time
        current_msgs_index = 0
        new_msgs = []
        for _index in range(num_msgs):
            while current_msgs_index < len(self.messages):
                msg_stamp = self.messages[current_msgs_index].timestamp

                if msg_stamp < t + 0.5 * dt_step:
                    value = self.messages[current_msgs_index].value
                    current_msgs_index += 1
                else:
                    break

            new_msgs.append(Message(t, value))
            t += dt_step

        self.messages = new_msgs

    def __str__(self):
        return "Channel: %s, Quantity: %s, Units: %s, Decimals: %d, Messages: %d, Frequency: %.2f Hz" % (
            self.name,
            self.quantity_type,
            self.units,
            self.decimals,
            len(self.messages),
            self.avg_frequency(),
        )


class Message(object):
    """A single message in a time series of data."""

    def __init__(self, timestamp=0, value=0):
        self.timestamp = float(timestamp)
        self.value = float(value)

    def __str__(self):
        return "t=%f, value=%f" % (self.timestamp, self.value)
