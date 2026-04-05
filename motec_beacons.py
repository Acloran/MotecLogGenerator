import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from data_log import normalize_label


EARTH_RADIUS_M = 6371000.0
MINIMUM_BEACON_GAP_SECONDS = 5.0
BEACON_SEGMENT_EXTENSION = 1.35
MAX_DISPLAY_POINTS = 5000

LATITUDE_EXACT_NAMES = {
    "latitude",
    "gps latitude",
    "lat",
}
LONGITUDE_EXACT_NAMES = {
    "longitude",
    "gps longitude",
    "lon",
    "long",
}


@dataclass
class GpsTrace:
    name: str
    latitude_channel: str
    longitude_channel: str
    times: list[float]
    latitudes: list[float]
    longitudes: list[float]

    def full_lap_rows(self, beacon_times):
        rows = []
        file_label = getattr(self, "display_name", self.name)
        for lap_index, (start_time, end_time) in enumerate(zip(beacon_times[:-1], beacon_times[1:]), start=1):
            rows.append(
                LapPreviewRow(
                    file_label=file_label,
                    lap_label="Lap %d" % lap_index,
                    start_time=start_time,
                    end_time=end_time,
                    duration=end_time - start_time,
                )
            )
        return rows


@dataclass
class BeaconCrossing:
    time: float
    latitude: float
    longitude: float
    x_m: float
    y_m: float


@dataclass
class LapPreviewRow:
    file_label: str
    lap_label: str
    start_time: float
    end_time: float
    duration: float


def format_lap_time(seconds):
    total_milliseconds = int(round(float(seconds) * 1000.0))
    minutes, milliseconds = divmod(total_milliseconds, 60 * 1000)
    seconds_whole, milliseconds = divmod(milliseconds, 1000)
    return "%d:%02d.%03d" % (minutes, seconds_whole, milliseconds)


def normalize_beacon_times(beacon_times, minimum_gap_seconds=MINIMUM_BEACON_GAP_SECONDS):
    normalized = []
    previous_time = None
    for raw_time in sorted(float(time_value) for time_value in beacon_times or []):
        if previous_time is not None and raw_time - previous_time < minimum_gap_seconds:
            continue
        normalized.append(raw_time)
        previous_time = raw_time
    return normalized


def infer_gps_channel_pair(data_log):
    best_latitude = None
    best_longitude = None
    best_latitude_score = -10**9
    best_longitude_score = -10**9

    for channel_name, channel in data_log.channels.items():
        if not channel.messages:
            continue

        normalized_name = normalize_label(channel_name)
        score = 0

        if normalized_name in LATITUDE_EXACT_NAMES:
            score = 500
            if score > best_latitude_score:
                best_latitude = channel_name
                best_latitude_score = score
            continue
        if normalized_name in LONGITUDE_EXACT_NAMES:
            score = 500
            if score > best_longitude_score:
                best_longitude = channel_name
                best_longitude_score = score
            continue

        if "latitude" in normalized_name:
            score += 220
        elif normalized_name == "lat":
            score += 210
        elif "lat" in normalized_name:
            score += 120

        if "longitude" in normalized_name:
            score += 220
        elif normalized_name in {"lon", "long"}:
            score += 210
        elif "lon" in normalized_name or "long" in normalized_name:
            score += 120

        if "gps" in normalized_name:
            score += 40
        if "accel" in normalized_name or "speed" in normalized_name or "gyro" in normalized_name:
            score -= 120

        if "latitude" in normalized_name or normalized_name == "lat" or normalized_name.endswith(" lat"):
            if score > best_latitude_score:
                best_latitude = channel_name
                best_latitude_score = score

        if "longitude" in normalized_name or normalized_name in {"lon", "long"} or normalized_name.endswith(" lon"):
            if score > best_longitude_score:
                best_longitude = channel_name
                best_longitude_score = score

    return best_latitude, best_longitude


def build_gps_trace(data_log):
    latitude_name, longitude_name = infer_gps_channel_pair(data_log)
    if not latitude_name or not longitude_name:
        raise ValueError("Latitude and Longitude channels are required for beacon placement.")

    latitude_channel = data_log.get_channel(latitude_name)
    longitude_channel = data_log.get_channel(longitude_name)
    if latitude_channel is None or longitude_channel is None:
        raise ValueError("Latitude and Longitude channels are required for beacon placement.")

    times, latitudes, longitudes = synchronize_gps_channels(latitude_channel, longitude_channel)
    if len(times) < 2:
        raise ValueError("At least two GPS samples are required for beacon placement.")

    return GpsTrace(
        name=data_log.name,
        latitude_channel=latitude_name,
        longitude_channel=longitude_name,
        times=times,
        latitudes=latitudes,
        longitudes=longitudes,
    )


def synchronize_gps_channels(latitude_channel, longitude_channel):
    latitude_times = [message.timestamp for message in latitude_channel.messages]
    latitude_values = [message.value for message in latitude_channel.messages]
    longitude_times = [message.timestamp for message in longitude_channel.messages]
    longitude_values = [message.value for message in longitude_channel.messages]

    if len(latitude_times) == len(longitude_times) and all(
        math.isclose(latitude_time, longitude_time, abs_tol=1e-6)
        for latitude_time, longitude_time in zip(latitude_times, longitude_times)
    ):
        return list(latitude_times), list(latitude_values), list(longitude_values)

    if len(latitude_times) <= len(longitude_times):
        base_times = list(latitude_times)
        latitudes = list(latitude_values)
        longitudes = interpolate_series(longitude_times, longitude_values, base_times)
    else:
        base_times = list(longitude_times)
        latitudes = interpolate_series(latitude_times, latitude_values, base_times)
        longitudes = list(longitude_values)

    synchronized = [
        (time_value, latitude_value, longitude_value)
        for time_value, latitude_value, longitude_value in zip(base_times, latitudes, longitudes)
        if latitude_value is not None and longitude_value is not None
    ]
    if len(synchronized) < 2:
        return [], [], []

    times, latitudes, longitudes = zip(*synchronized)
    return list(times), list(latitudes), list(longitudes)


def interpolate_series(source_times, source_values, target_times):
    if not source_times:
        return [None for _unused in target_times]
    if len(source_times) == 1:
        return [float(source_values[0]) for _unused in target_times]

    interpolated = []
    index = 0
    max_index = len(source_times) - 1
    for target_time in target_times:
        if target_time < source_times[0] or target_time > source_times[-1]:
            interpolated.append(None)
            continue

        while index < max_index - 1 and source_times[index + 1] < target_time:
            index += 1

        left_time = source_times[index]
        right_time = source_times[min(index + 1, max_index)]
        left_value = float(source_values[index])
        right_value = float(source_values[min(index + 1, max_index)])

        if math.isclose(left_time, right_time, abs_tol=1e-9):
            interpolated.append(left_value)
            continue

        ratio = (target_time - left_time) / (right_time - left_time)
        interpolated.append(left_value + ((right_value - left_value) * ratio))
    return interpolated


def lat_lon_to_xy(latitude, longitude, reference_latitude, reference_longitude):
    latitude_radians = math.radians(float(latitude))
    longitude_radians = math.radians(float(longitude))
    reference_latitude_radians = math.radians(float(reference_latitude))
    reference_longitude_radians = math.radians(float(reference_longitude))

    x_m = (longitude_radians - reference_longitude_radians) * EARTH_RADIUS_M * math.cos(reference_latitude_radians)
    y_m = (latitude_radians - reference_latitude_radians) * EARTH_RADIUS_M
    return x_m, y_m


def xy_to_lat_lon(x_m, y_m, reference_latitude, reference_longitude):
    reference_latitude_radians = math.radians(float(reference_latitude))
    latitude = float(reference_latitude) + math.degrees(float(y_m) / EARTH_RADIUS_M)
    longitude = float(reference_longitude) + math.degrees(
        float(x_m) / (EARTH_RADIUS_M * max(1e-9, math.cos(reference_latitude_radians)))
    )
    return latitude, longitude


def project_trace(trace, reference_latitude, reference_longitude):
    x_values = []
    y_values = []
    for latitude, longitude in zip(trace.latitudes, trace.longitudes):
        x_m, y_m = lat_lon_to_xy(latitude, longitude, reference_latitude, reference_longitude)
        x_values.append(x_m)
        y_values.append(y_m)
    return x_values, y_values


def project_geo_line(geo_line, reference_latitude, reference_longitude):
    start_latitude, start_longitude, end_latitude, end_longitude = geo_line
    start_x, start_y = lat_lon_to_xy(start_latitude, start_longitude, reference_latitude, reference_longitude)
    end_x, end_y = lat_lon_to_xy(end_latitude, end_longitude, reference_latitude, reference_longitude)
    return start_x, start_y, end_x, end_y


def downsample_points(x_values, y_values, max_points=MAX_DISPLAY_POINTS):
    if len(x_values) <= max_points:
        return list(x_values), list(y_values)

    step = max(1, math.ceil(len(x_values) / max_points))
    sampled_x = list(x_values[::step])
    sampled_y = list(y_values[::step])
    if sampled_x[-1] != x_values[-1] or sampled_y[-1] != y_values[-1]:
        sampled_x.append(x_values[-1])
        sampled_y.append(y_values[-1])
    return sampled_x, sampled_y


def detect_beacon_crossings(trace, geo_line, minimum_gap_seconds=MINIMUM_BEACON_GAP_SECONDS):
    reference_latitude = (trace.latitudes[0] + geo_line[0] + geo_line[2]) / 3.0
    reference_longitude = (trace.longitudes[0] + geo_line[1] + geo_line[3]) / 3.0
    x_values, y_values = project_trace(trace, reference_latitude, reference_longitude)
    line_start_x, line_start_y, line_end_x, line_end_y = project_geo_line(
        geo_line,
        reference_latitude,
        reference_longitude,
    )
    line_start_x, line_start_y, line_end_x, line_end_y = extend_line_segment(
        line_start_x,
        line_start_y,
        line_end_x,
        line_end_y,
        BEACON_SEGMENT_EXTENSION,
    )

    crossings = []
    previous_time = None
    for index in range(len(trace.times) - 1):
        path_start_x = x_values[index]
        path_start_y = y_values[index]
        path_end_x = x_values[index + 1]
        path_end_y = y_values[index + 1]

        intersection = segment_intersection(
            (path_start_x, path_start_y),
            (path_end_x, path_end_y),
            (line_start_x, line_start_y),
            (line_end_x, line_end_y),
        )
        if intersection is None:
            continue

        path_fraction, intersection_x, intersection_y = intersection
        crossing_time = trace.times[index] + ((trace.times[index + 1] - trace.times[index]) * path_fraction)
        if previous_time is not None and crossing_time - previous_time < minimum_gap_seconds:
            continue

        crossing_latitude, crossing_longitude = xy_to_lat_lon(
            intersection_x,
            intersection_y,
            reference_latitude,
            reference_longitude,
        )
        crossings.append(
            BeaconCrossing(
                time=crossing_time,
                latitude=crossing_latitude,
                longitude=crossing_longitude,
                x_m=intersection_x,
                y_m=intersection_y,
            )
        )
        previous_time = crossing_time

    return crossings


def extend_line_segment(start_x, start_y, end_x, end_y, scale):
    center_x = (start_x + end_x) * 0.5
    center_y = (start_y + end_y) * 0.5
    half_x = (end_x - start_x) * 0.5 * scale
    half_y = (end_y - start_y) * 0.5 * scale
    return (
        center_x - half_x,
        center_y - half_y,
        center_x + half_x,
        center_y + half_y,
    )


def cross_2d(first, second):
    return (first[0] * second[1]) - (first[1] * second[0])


def segment_intersection(path_start, path_end, line_start, line_end, epsilon=1e-9):
    path_vector = (path_end[0] - path_start[0], path_end[1] - path_start[1])
    line_vector = (line_end[0] - line_start[0], line_end[1] - line_start[1])
    denominator = cross_2d(path_vector, line_vector)
    if math.isclose(denominator, 0.0, abs_tol=epsilon):
        return None

    start_delta = (line_start[0] - path_start[0], line_start[1] - path_start[1])
    path_fraction = cross_2d(start_delta, line_vector) / denominator
    line_fraction = cross_2d(start_delta, path_vector) / denominator
    if not (-epsilon <= path_fraction <= 1.0 + epsilon and -epsilon <= line_fraction <= 1.0 + epsilon):
        return None

    clamped_fraction = max(0.0, min(1.0, path_fraction))
    intersection_x = path_start[0] + (path_vector[0] * clamped_fraction)
    intersection_y = path_start[1] + (path_vector[1] * clamped_fraction)
    return clamped_fraction, intersection_x, intersection_y


def beacon_preview_rows(gps_traces, geo_line):
    preview_rows = []
    crossings_by_file = {}
    for trace in gps_traces:
        crossings = detect_beacon_crossings(trace, geo_line)
        crossings_by_file[trace.name] = crossings
        preview_rows.extend(trace.full_lap_rows([crossing.time for crossing in crossings]))
    return preview_rows, crossings_by_file


def write_motec_beacon_file(ld_filename, beacon_times):
    ldx_filename = os.path.splitext(ld_filename)[0] + ".ldx"
    normalized_times = normalize_beacon_times(beacon_times, minimum_gap_seconds=0.001)

    if not normalized_times:
        if os.path.exists(ldx_filename):
            os.remove(ldx_filename)
        return None

    root = ET.Element("LDXFile", Version="1.6", Locale="English")
    layers = ET.SubElement(root, "Layers")
    layer = ET.SubElement(layers, "Layer")
    marker_block = ET.SubElement(layer, "MarkerBlock")
    marker_group = ET.SubElement(marker_block, "MarkerGroup", Name="Beacons", Index="3")

    for index, beacon_time in enumerate(normalized_times, start=1):
        ET.SubElement(
            marker_group,
            "Marker",
            Version="100",
            ClassName="BCN",
            Name="Manual.%d" % index,
            Flags="77",
            Time="%.6f" % (float(beacon_time) * 1_000_000.0),
        )

    ET.SubElement(layer, "RangeBlock")
    details = ET.SubElement(layer, "Details")
    ET.SubElement(details, "String", Id="Total Laps", Value=str(len(normalized_times) + 1))

    if len(normalized_times) >= 2:
        lap_times = [end_time - start_time for start_time, end_time in zip(normalized_times[:-1], normalized_times[1:])]
        fastest_index, fastest_time = min(enumerate(lap_times, start=1), key=lambda item: item[1])
        ET.SubElement(details, "String", Id="Fastest Time", Value=format_lap_time(fastest_time))
        ET.SubElement(details, "String", Id="Fastest Lap", Value=str(fastest_index))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(ldx_filename, encoding="utf-8", xml_declaration=True)
    return ldx_filename
