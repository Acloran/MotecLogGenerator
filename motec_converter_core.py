#!/usr/bin/env python3

import argparse
import copy
import datetime
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import cantools
except ImportError:
    cantools = None

from data_log import DataLog, normalize_label
from motec_beacons import normalize_beacon_times, write_motec_beacon_file
from motec_log import MotecLog
from unit_chart import apply_channel_unit_chart


AUTO_DETECT = "Auto-Detect"
CAN_EXTENSIONS = {".log"}
CSV_EXTENSIONS = {".csv"}
AIM_EXTENSIONS = {".xrk", ".xrz"}

METADATA_FIELDS = (
    ("driver", "Driver"),
    ("vehicle_id", "Vehicle ID"),
    ("vehicle_weight", "Vehicle Weight"),
    ("vehicle_type", "Vehicle Type"),
    ("vehicle_comment", "Vehicle Comment"),
    ("venue_name", "Venue Name"),
    ("event_name", "Event Name"),
    ("event_session", "Event Session"),
    ("short_comment", "Short Comment"),
    ("long_comment", "Long Comment"),
)


@dataclass
class FileSettings:
    driver: str = ""
    vehicle_id: str = ""
    vehicle_weight: str = "0"
    vehicle_type: str = ""
    vehicle_comment: str = ""
    venue_name: str = ""
    event_name: str = ""
    event_session: str = ""
    short_comment: str = ""
    long_comment: str = ""
    preview_channel: str = ""
    motion_channel: str = ""
    segment_ranges: list[tuple[float, float]] = field(default_factory=list)
    beacon_markers: list[float] = field(default_factory=list)
    beacon_line: tuple[float, float, float, float] | None = None

    def copy(self):
        return copy.deepcopy(self)


def emit_status(status_callback, message):
    if status_callback:
        status_callback(message)
    else:
        print(message)


def resolve_frequency(frequency_text):
    text = str(frequency_text or "Auto").strip()
    if not text or text.lower() == "auto":
        return None

    value = float(text)
    if value <= 0:
        raise ValueError("Frequency must be greater than zero.")
    return value


def detect_log_type(filepath):
    ext = Path(filepath).suffix.lower()
    if ext in CAN_EXTENSIONS:
        return "CAN"
    if ext in AIM_EXTENSIONS:
        return "AIM"
    if ext in CSV_EXTENSIONS:
        try:
            with open(filepath, "r", encoding="utf-8-sig", errors="ignore", newline="") as file:
                header = file.readline()
        except OSError:
            return None

        if "AP Info" in header:
            return "ACCESSPORT"
        return "CSV"
    return None


def matches_add_filter(filepath, selected_filter):
    detected_type = detect_log_type(filepath)
    if selected_filter == AUTO_DETECT:
        return detected_type is not None
    return detected_type == selected_filter


def flatten_input_paths(paths, selected_filter=AUTO_DETECT):
    files_to_process = []
    for raw_path in paths:
        path = os.path.expanduser(raw_path)
        if os.path.isdir(path):
            for child in sorted(os.listdir(path)):
                child_path = os.path.join(path, child)
                if os.path.isfile(child_path) and matches_add_filter(child_path, selected_filter):
                    files_to_process.append(child_path)
        elif os.path.isfile(path) and matches_add_filter(path, selected_filter):
            files_to_process.append(path)
    return files_to_process


def build_output_path(source_path, output_dir, output_stem=None):
    if not output_dir:
        return None
    output_name = (output_stem or Path(source_path).stem) + ".ld"
    return os.path.join(output_dir, output_name)


def build_output_filenames(log_path, output_path, segment_count, output_stem=None):
    if output_path:
        base_filename = os.path.splitext(output_path)[0] + ".ld"
    else:
        source_dir = os.path.dirname(log_path)
        stem = output_stem or Path(log_path).stem
        base_filename = os.path.join(source_dir, stem + ".ld")

    if segment_count <= 1:
        return [base_filename]

    stem, ext = os.path.splitext(base_filename)
    return [
        "%s_part%d%s" % (stem, index, ext)
        for index in range(1, segment_count + 1)
    ]


def load_can_database(dbc_path):
    if cantools is None:
        raise RuntimeError("cantools is not installed; CAN support is unavailable.")
    if not dbc_path:
        raise ValueError("DBC file is required for CAN log processing.")
    if not os.path.isfile(dbc_path):
        raise FileNotFoundError("DBC file %s does not exist" % dbc_path)
    return cantools.database.load_file(dbc_path)


def parse_aim_datetime(metadata):
    if not metadata:
        return None

    date_text = metadata.get("Log Date")
    time_text = metadata.get("Log Time")
    if not date_text or not time_text:
        return None

    for fmt in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime("%s %s" % (date_text, time_text), fmt)
        except ValueError:
            continue
    return None


def apply_metadata(motec_log, args, data_log, source_path):
    source_metadata = getattr(data_log, "metadata", {}) or {}

    def choose(current_value, *metadata_keys):
        if current_value:
            return current_value
        for key in metadata_keys:
            metadata_value = source_metadata.get(key)
            if metadata_value:
                return str(metadata_value)
        return ""

    motec_log.driver = choose(args.driver, "Driver")
    motec_log.vehicle_id = choose(args.vehicle_id, "Vehicle")
    motec_log.vehicle_weight = args.vehicle_weight
    motec_log.vehicle_type = choose(args.vehicle_type)
    motec_log.vehicle_comment = choose(args.vehicle_comment, "Device Name", "Logger Model")
    motec_log.venue_name = choose(args.venue_name, "Venue")
    motec_log.event_name = choose(args.event_name, "Series")
    motec_log.event_session = choose(args.event_session, "Session")
    motec_log.long_comment = choose(args.long_comment, "Long Comment")
    motec_log.short_comment = choose(args.short_comment) or Path(source_path).stem

    log_datetime = parse_aim_datetime(source_metadata)
    if log_datetime:
        motec_log.datetime = log_datetime


def load_data_log(log_path, log_type, can_db=None, status_callback=None, channel_unit_chart=None):
    emit_status(status_callback, "Loading %s" % os.path.basename(log_path))
    data_log = DataLog(Path(log_path).stem)
    data_log.load_file(log_path, log_type, can_db)
    apply_channel_unit_chart(data_log, channel_unit_chart)
    if not data_log.channels:
        raise RuntimeError("Failed to find any channels in log data: %s" % log_path)
    emit_status(
        status_callback,
        "Loaded %.1fs with %d channels" % (data_log.duration(), len(data_log.channels)),
    )
    return data_log


def normalize_segment_ranges(segment_ranges, start_bound, end_bound, minimum_duration=0.05):
    normalized = []
    for start_time, end_time in segment_ranges or []:
        start_value = max(start_bound, min(float(start_time), end_bound))
        end_value = max(start_bound, min(float(end_time), end_bound))
        if end_value - start_value < minimum_duration:
            continue
        normalized.append((start_value, end_value))

    normalized.sort(key=lambda segment: (segment[0], segment[1]))

    merged = []
    for start_value, end_value in normalized:
        if merged and start_value <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_value))
        else:
            merged.append((start_value, end_value))
    return merged


def normalized_segments(data_log, settings):
    log_start = data_log.start()
    log_end = data_log.end()
    configured_ranges = []
    if settings is not None:
        configured_ranges = normalize_segment_ranges(
            settings.segment_ranges,
            log_start,
            log_end,
        )

    if not configured_ranges:
        configured_ranges = [(log_start, log_end)]

    return [
        ("part%d" % index, start_time, end_time)
        for index, (start_time, end_time) in enumerate(configured_ranges, start=1)
    ]


def build_args_for_settings(settings, frequency_text):
    args = argparse.Namespace()
    args.frequency = frequency_text
    args.driver = settings.driver.strip()
    args.vehicle_id = settings.vehicle_id.strip()
    args.vehicle_type = settings.vehicle_type.strip()
    args.vehicle_comment = settings.vehicle_comment.strip()
    args.venue_name = settings.venue_name.strip()
    args.event_name = settings.event_name.strip()
    args.event_session = settings.event_session.strip()
    args.long_comment = settings.long_comment.strip()
    args.short_comment = settings.short_comment.strip()
    try:
        args.vehicle_weight = int((settings.vehicle_weight or "0").strip() or "0")
    except ValueError:
        args.vehicle_weight = 0
    return args


def beacon_times_for_segment(settings, start_time, end_time):
    if settings is None:
        return []

    beacon_times = getattr(settings, "beacon_markers", []) or []
    if not beacon_times:
        return []

    relative_times = [
        float(beacon_time) - float(start_time)
        for beacon_time in beacon_times
        if float(start_time) <= float(beacon_time) <= float(end_time)
    ]
    return normalize_beacon_times(relative_times)


def process_log_file(
    log_path,
    log_type,
    output_path,
    args,
    can_db=None,
    settings=None,
    status_callback=None,
    source_data_log=None,
    output_stem=None,
    channel_unit_chart=None,
):
    data_log = source_data_log.copy() if source_data_log is not None else load_data_log(
        log_path,
        log_type,
        can_db,
        status_callback,
        channel_unit_chart=channel_unit_chart,
    )
    if source_data_log is not None:
        apply_channel_unit_chart(data_log, channel_unit_chart)

    segments = normalized_segments(data_log, settings)
    frequency = resolve_frequency(args.frequency)
    output_filenames = build_output_filenames(log_path, output_path, len(segments), output_stem=output_stem)

    written_files = []
    for index, (_label, start_time, end_time) in enumerate(segments, start=1):
        segment_label = "segment %d/%d" % (index, len(segments))
        emit_status(status_callback, "Preparing %s" % segment_label)

        segment_log = data_log.extract_segment(start_time, end_time, rebase_time=True)
        if not segment_log.channels:
            raise RuntimeError("Segment %d for %s contains no channel data." % (index, log_path))

        emit_status(status_callback, "Resampling %s" % segment_label)
        segment_log.resample(frequency)

        emit_status(status_callback, "Writing %s" % segment_label)
        motec_log = MotecLog()
        apply_metadata(motec_log, args, segment_log, log_path)
        motec_log.initialize()
        motec_log.add_all_channels(segment_log)

        output_filename = output_filenames[index - 1]
        output_dir = os.path.dirname(output_filename)
        if output_dir and not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        motec_log.write(output_filename)
        write_motec_beacon_file(
            output_filename,
            beacon_times_for_segment(settings, start_time, end_time),
        )
        written_files.append(output_filename)

    emit_status(status_callback, "Done")
    return written_files


def format_seconds(seconds):
    if seconds is None:
        return "-"
    return "%.2f s" % seconds


def format_bytes(size_bytes):
    if size_bytes < 1024:
        return "%d B" % size_bytes
    if size_bytes < 1024 * 1024:
        return "%.1f KB" % (size_bytes / 1024.0)
    if size_bytes < 1024 * 1024 * 1024:
        return "%.1f MB" % (size_bytes / (1024.0 * 1024.0))
    return "%.1f GB" % (size_bytes / (1024.0 * 1024.0 * 1024.0))


def preview_series_for_channel(channel, max_points=3500):
    if not channel or not channel.messages:
        return [], []

    if len(channel.messages) <= max_points:
        messages = channel.messages
    else:
        step = max(1, math.ceil(len(channel.messages) / max_points))
        messages = channel.messages[::step]
        if messages[-1] is not channel.messages[-1]:
            messages = messages + [channel.messages[-1]]

    times = [message.timestamp for message in messages]
    values = [message.value for message in messages]
    return times, values


def motion_threshold_for_channel(channel):
    if not channel or not channel.messages:
        return 0.0

    peak_value = max(abs(message.value) for message in channel.messages)
    unit = normalize_label(channel.units)

    if unit in {"km h", "kmh", "kph"}:
        base = 5.0
    elif unit == "mph":
        base = 3.0
    elif unit in {"m s", "mps"}:
        base = 1.0
    else:
        base = 1.0

    return max(base, peak_value * 0.03)


def detect_active_range(channel):
    if not channel or not channel.messages:
        return None, None

    threshold = motion_threshold_for_channel(channel)
    active_messages = [
        message for message in channel.messages if abs(message.value) >= threshold
    ]
    if not active_messages:
        return channel.start(), channel.end()
    return active_messages[0].timestamp, active_messages[-1].timestamp


def detect_split_ranges(channel, minimum_gap=12.0, minimum_segment=8.0):
    if not channel or len(channel.messages) < 3:
        return []

    active_start, active_end = detect_active_range(channel)
    if active_start is None or active_end is None:
        return []

    threshold = motion_threshold_for_channel(channel)
    relevant_messages = [
        message
        for message in channel.messages
        if active_start <= message.timestamp <= active_end
    ]

    if not relevant_messages:
        return []

    segments = []
    segment_start = active_start
    gap_start = None
    previous_message = None
    for message in relevant_messages:
        stationary = abs(message.value) < threshold
        if stationary and gap_start is None:
            gap_start = previous_message.timestamp if previous_message is not None else message.timestamp
        elif not stationary and gap_start is not None and previous_message is not None:
            gap_end = previous_message.timestamp
            if gap_end - gap_start >= minimum_gap and gap_start - segment_start >= minimum_segment:
                segments.append((segment_start, gap_start))
                segment_start = message.timestamp
            gap_start = None
        previous_message = message

    if gap_start is not None and previous_message is not None:
        gap_end = previous_message.timestamp
        if gap_end - gap_start >= minimum_gap and gap_start - segment_start >= minimum_segment:
            segments.append((segment_start, gap_start))
            segment_start = None

    if segment_start is not None and active_end - segment_start >= minimum_segment:
        segments.append((segment_start, active_end))

    if not segments and active_end > active_start:
        return [(active_start, active_end)]

    return normalize_segment_ranges(segments, active_start, active_end)
