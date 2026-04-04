import csv
import os
from dataclasses import dataclass


CHANNEL_NAME_FIELDS = ("channel_name", "channel", "name")
QUANTITY_FIELDS = ("quantity_type", "quantity", "qty", "type")
UNIT_FIELDS = ("unit", "units")


@dataclass(frozen=True)
class ChannelChartEntry:
    quantity_type: str = ""
    unit: str = ""


def normalize_chart_channel_name(channel_name):
    return " ".join(str(channel_name or "").strip().casefold().split())


def _resolve_field_name(field_names, supported_names):
    normalized = {
        str(field_name or "").strip().lower(): field_name
        for field_name in field_names or []
    }
    for supported_name in supported_names:
        if supported_name in normalized:
            return normalized[supported_name]
    return None


def load_channel_unit_chart(chart_path):
    expanded_path = os.path.expanduser(str(chart_path or "").strip())
    if not expanded_path:
        return {}
    if not os.path.isfile(expanded_path):
        raise FileNotFoundError("Channel chart %s does not exist" % expanded_path)

    with open(expanded_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            return {}

        channel_field = _resolve_field_name(reader.fieldnames, CHANNEL_NAME_FIELDS)
        quantity_field = _resolve_field_name(reader.fieldnames, QUANTITY_FIELDS)
        unit_field = _resolve_field_name(reader.fieldnames, UNIT_FIELDS)
        if channel_field is None or (quantity_field is None and unit_field is None):
            raise ValueError(
                "Channel chart must contain 'channel_name' plus 'quantity_type' and/or 'unit' columns."
            )

        chart = {}
        for row in reader:
            channel_name = str(row.get(channel_field, "") or "").strip()
            if not channel_name or channel_name.startswith("#"):
                continue
            chart[normalize_chart_channel_name(channel_name)] = ChannelChartEntry(
                quantity_type=str(row.get(quantity_field, "") or "").strip() if quantity_field else "",
                unit=str(row.get(unit_field, "") or "").strip() if unit_field else "",
            )
        return chart


def apply_channel_unit_chart(data_log, chart):
    if not chart:
        return 0

    matched_count = 0
    for channel in data_log.channels.values():
        key = normalize_chart_channel_name(channel.name)
        if key not in chart:
            continue
        entry = chart[key]
        if entry.quantity_type:
            channel.quantity_type = entry.quantity_type
        if entry.unit:
            channel.units = entry.unit
        matched_count += 1
    return matched_count
