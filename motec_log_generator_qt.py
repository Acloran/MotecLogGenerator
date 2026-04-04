#!/usr/bin/env python3

import argparse
import concurrent.futures
import copy
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError as exc:
    raise SystemExit(
        "PySide6 is required for this app. Install it with "
        "`python -m pip install -r requirements-pyside6.txt`."
    ) from exc

from motec_converter_core import (
    METADATA_FIELDS,
    FileSettings,
    build_args_for_settings,
    build_output_path,
    detect_active_range,
    detect_log_type,
    detect_split_ranges,
    flatten_input_paths,
    format_bytes,
    format_seconds,
    load_can_database,
    load_data_log,
    normalize_segment_ranges,
    preview_series_for_channel,
    process_log_file,
    resolve_frequency,
)
from unit_chart import load_channel_unit_chart


APP_BG = "#0b0f14"
APP_PANEL = "#111821"
APP_CARD = "#161f2b"
APP_CARD_ALT = "#1b2633"
APP_TILE = "#1f2c3a"
APP_TEXT = "#f4f7fb"
APP_MUTED = "#94a7bd"
APP_SUBTLE = "#7f90a5"
APP_BORDER = "#273647"
APP_BORDER_SOFT = "#314356"
APP_ACCENT = "#2f7cff"
APP_ACCENT_ALT = "#5aa6ff"
APP_SELECTION = "#203247"
APP_SUCCESS = "#49c075"
APP_ERROR = "#ff6b6b"
APP_WARNING = "#ffb14a"
APP_PLOT_BG = "#1a2430"
APP_PLOT_GRID = "#334457"
APP_SEGMENT = QtGui.QColor(47, 124, 255, 40)
APP_SEGMENT_SELECTED = QtGui.QColor(90, 166, 255, 70)
APP_SELECTION_FILL = QtGui.QColor(255, 255, 255, 36)
APP_SHADOW = QtGui.QColor(0, 0, 0, 70)
UNDO_PREVIEW_LIMIT_BYTES = 1024 * 1024 * 1024
SEGMENT_COLORS = ("#2f7cff", "#5aa6ff", "#45c3ff", "#40b36f", "#9d7dff")

TEXT_EDIT_FIELDS = {"vehicle_comment", "long_comment"}


def resource_path(*parts):
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, *parts)


def load_app_icon():
    icon_path = resource_path("assets", "app_icon_256.png")
    if os.path.isfile(icon_path):
        return QtGui.QIcon(icon_path)
    return QtGui.QIcon()


@dataclass
class QtFileItem:
    item_id: str
    path: str
    detected_type: str | None
    status: str = "Queued"
    detail: str = ""
    duration: float | None = None
    preview_log: object | None = None
    preview_error: str = ""
    preview_loading: bool = False
    preview_channels: list[str] = field(default_factory=list)
    settings: FileSettings = field(default_factory=FileSettings)
    outputs: list[str] = field(default_factory=list)
    display_name: str | None = None
    output_stem: str | None = None
    derived_from: str | None = None
    preview_token: int = 0
    display_size_bytes: int | None = None

    @property
    def name(self):
        if self.display_name:
            return self.display_name
        return os.path.basename(self.path)

    @property
    def duration_text(self):
        if self.duration is None:
            return "-"
        return "%.1fs" % self.duration

    @property
    def size_text(self):
        if self.outputs:
            existing_outputs = [output_path for output_path in self.outputs if os.path.isfile(output_path)]
            if existing_outputs:
                try:
                    return format_bytes(sum(os.path.getsize(output_path) for output_path in existing_outputs))
                except OSError:
                    pass
        if self.display_size_bytes is not None:
            return format_bytes(self.display_size_bytes)
        try:
            return format_bytes(os.path.getsize(self.path))
        except OSError:
            return "-"


class WorkerSignals(QtCore.QObject):
    preview_finished = QtCore.Signal(str, int, object, str)
    convert_item_status = QtCore.Signal(str, str)
    convert_item_complete = QtCore.Signal(str, object)
    convert_item_error = QtCore.Signal(str, str)
    convert_progress = QtCore.Signal(int, int)
    convert_finished = QtCore.Signal()
    convert_fatal_error = QtCore.Signal(str)


class PreviewLoadTask(QtCore.QRunnable):
    def __init__(self, item_id, preview_token, path, detected_type, dbc_path, unit_chart_path):
        super().__init__()
        self.item_id = item_id
        self.preview_token = preview_token
        self.path = path
        self.detected_type = detected_type
        self.dbc_path = dbc_path
        self.unit_chart_path = unit_chart_path
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            can_db = None
            channel_unit_chart = load_channel_unit_chart(self.unit_chart_path)
            if self.detected_type == "CAN":
                can_db = load_can_database(self.dbc_path)
            preview_log = load_data_log(
                self.path,
                self.detected_type,
                can_db,
                status_callback=lambda _msg: None,
                channel_unit_chart=channel_unit_chart,
            )
        except Exception as exc:
            self.signals.preview_finished.emit(self.item_id, self.preview_token, None, str(exc))
            return

        self.signals.preview_finished.emit(self.item_id, self.preview_token, preview_log, "")


class ConvertQueueTask(QtCore.QRunnable):
    def __init__(self, items, output_dir, frequency_text, dbc_path, unit_chart_path):
        super().__init__()
        self.items = items
        self.output_dir = output_dir
        self.frequency_text = frequency_text
        self.dbc_path = dbc_path
        self.unit_chart_path = unit_chart_path
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        can_db = None
        try:
            channel_unit_chart = load_channel_unit_chart(self.unit_chart_path)
            if any(item.detected_type == "CAN" for item in self.items):
                can_db = load_can_database(self.dbc_path)
        except Exception as exc:
            self.signals.convert_fatal_error.emit(str(exc))
            return

        total = len(self.items)
        for index, item in enumerate(self.items, start=1):
            try:
                args = build_args_for_settings(item.settings, self.frequency_text)
                source_log = item.preview_log.copy() if item.preview_log is not None else None
                output_path = build_output_path(item.path, self.output_dir, item.output_stem)
                written_files = process_log_file(
                    item.path,
                    item.detected_type,
                    output_path,
                    args,
                    can_db=can_db,
                    settings=item.settings,
                    status_callback=lambda message, current_id=item.item_id: self.signals.convert_item_status.emit(
                        current_id,
                        message,
                    ),
                    source_data_log=source_log,
                    output_stem=item.output_stem,
                    channel_unit_chart=channel_unit_chart,
                )
            except Exception as exc:
                self.signals.convert_item_error.emit(item.item_id, str(exc))
            else:
                self.signals.convert_item_complete.emit(item.item_id, written_files)
            self.signals.convert_progress.emit(index, total)

        self.signals.convert_finished.emit()


def soft_shadow(widget, blur=44, y_offset=14):
    effect = QtWidgets.QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(APP_SHADOW)
    widget.setGraphicsEffect(effect)


class FloatingCard(QtWidgets.QFrame):
    def __init__(self, title="", subtitle="", parent=None):
        super().__init__(parent)
        self.setObjectName("FloatingCard")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        soft_shadow(self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        self.body_layout = layout

        if title:
            title_label = QtWidgets.QLabel(title)
            title_label.setObjectName("CardTitle")
            layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QtWidgets.QLabel(subtitle)
            subtitle_label.setObjectName("CardSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

    def add_widget(self, widget, stretch=0):
        self.body_layout.addWidget(widget, stretch)

    def add_layout(self, layout):
        self.body_layout.addLayout(layout)


class InfoChip(QtWidgets.QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("InfoChip")


class FileQueueWidget(QtWidgets.QTreeWidget):
    files_dropped = QtCore.Signal(list)
    delete_pressed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("QueueTree")
        self.setColumnCount(4)
        self.setHeaderLabels(["File", "Type", "Duration", "Size"])
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(False)
        self.setUniformRowHeights(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
        self.setDropIndicatorShown(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.setTextElideMode(QtCore.Qt.ElideMiddle)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    paths.append(url.toLocalFile())
            if paths:
                self.files_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class SegmentTableWidget(QtWidgets.QTableWidget):
    delete_pressed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setObjectName("SegmentTable")
        self.setHorizontalHeaderLabels(["Start", "End", "Length"])
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.AnyKeyPressed
        )
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.setMinimumHeight(220)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class PlotPreviewWidget(QtWidgets.QWidget):
    range_selected = QtCore.Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(420)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.times = []
        self.values = []
        self.unit = ""
        self.title = "Speed Preview"
        self.subtitle = "Select a file to preview."
        self.placeholder = "Select a single file to preview."
        self.segment_ranges = []
        self.selected_segment_index = None
        self.selection_range = None
        self._drag_start = None
        self._drag_end = None

    def clear_preview(self, placeholder, subtitle=""):
        self.times = []
        self.values = []
        self.unit = ""
        self.title = "Speed Preview"
        self.subtitle = subtitle
        self.placeholder = placeholder
        self.segment_ranges = []
        self.selected_segment_index = None
        self.selection_range = None
        self._drag_start = None
        self._drag_end = None
        self.update()

    def set_preview(
        self,
        times,
        values,
        *,
        title,
        subtitle,
        unit,
        segment_ranges,
        selected_segment_index=None,
        selection_range=None,
    ):
        self.times = times
        self.values = values
        self.title = title
        self.subtitle = subtitle
        self.unit = unit
        self.placeholder = ""
        self.segment_ranges = list(segment_ranges)
        self.selected_segment_index = selected_segment_index
        self.selection_range = selection_range
        self._drag_start = None
        self._drag_end = None
        self.update()

    def _outer_rect(self):
        return self.rect().adjusted(14, 14, -14, -14)

    def _plot_rect(self):
        outer = self._outer_rect()
        return QtCore.QRectF(
            outer.left() + 22,
            outer.top() + 82,
            outer.width() - 44,
            outer.height() - 116,
        )

    def _inner_rect(self):
        return self._plot_rect().adjusted(66, 16, -26, -34)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(APP_CARD))

        outer = self._outer_rect()
        painter.setPen(QtGui.QPen(QtGui.QColor(APP_BORDER), 1))
        painter.setBrush(QtGui.QColor(APP_PLOT_BG))
        painter.drawRoundedRect(outer, 28, 28)

        title_rect = QtCore.QRectF(outer.left() + 22, outer.top() + 18, outer.width() - 44, 26)
        painter.setPen(QtGui.QColor(APP_TEXT))
        title_font = painter.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(title_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self.title)

        subtitle_rect = QtCore.QRectF(outer.left() + 22, outer.top() + 46, outer.width() - 44, 20)
        painter.setPen(QtGui.QColor(APP_MUTED))
        subtitle_font = painter.font()
        subtitle_font.setPointSize(10)
        subtitle_font.setBold(False)
        painter.setFont(subtitle_font)
        painter.drawText(subtitle_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self.subtitle)

        plot_rect = self._plot_rect()
        painter.setPen(QtGui.QPen(QtGui.QColor(APP_BORDER_SOFT), 1))
        painter.setBrush(QtGui.QColor(APP_CARD_ALT))
        painter.drawRoundedRect(plot_rect, 24, 24)

        if not self.times or not self.values:
            painter.setPen(QtGui.QColor(APP_MUTED))
            painter.drawText(plot_rect, QtCore.Qt.AlignCenter, self.placeholder)
            return

        time_min = min(self.times)
        time_max = max(self.times)
        value_min = min(self.values)
        value_max = max(self.values)
        if math.isclose(time_min, time_max):
            time_max = time_min + 1.0
        if math.isclose(value_min, value_max):
            value_max = value_min + 1.0

        inner = self._inner_rect()

        def map_x(value):
            ratio = (value - time_min) / max(1e-9, (time_max - time_min))
            return inner.left() + (inner.width() * ratio)

        def map_y(value):
            ratio = (value - value_min) / max(1e-9, (value_max - value_min))
            return inner.bottom() - (inner.height() * ratio)

        painter.setPen(QtGui.QPen(QtGui.QColor(APP_PLOT_GRID), 1))
        for row_index in range(5):
            y_pos = inner.top() + (inner.height() * row_index / 4.0)
            painter.drawLine(QtCore.QPointF(inner.left(), y_pos), QtCore.QPointF(inner.right(), y_pos))
        for column_index in range(6):
            x_pos = inner.left() + (inner.width() * column_index / 5.0)
            painter.drawLine(QtCore.QPointF(x_pos, inner.top()), QtCore.QPointF(x_pos, inner.bottom()))

        for index, (start_time, end_time) in enumerate(self.segment_ranges):
            x_start = map_x(start_time)
            x_end = map_x(end_time)
            fill_color = APP_SEGMENT_SELECTED if index == self.selected_segment_index else APP_SEGMENT
            painter.fillRect(
                QtCore.QRectF(x_start, inner.top(), max(3.0, x_end - x_start), inner.height()),
                fill_color,
            )
            border_color = QtGui.QColor(SEGMENT_COLORS[index % len(SEGMENT_COLORS)])
            border_alpha = 210 if index == self.selected_segment_index else 160
            border_color.setAlpha(border_alpha)
            pen_width = 1.7 if index == self.selected_segment_index else 1.1
            painter.setPen(QtGui.QPen(border_color, pen_width))
            painter.drawLine(QtCore.QPointF(x_start, inner.top()), QtCore.QPointF(x_start, inner.bottom()))
            painter.drawLine(QtCore.QPointF(x_end, inner.top()), QtCore.QPointF(x_end, inner.bottom()))

        if self.selection_range is not None:
            x_start = map_x(self.selection_range[0])
            x_end = map_x(self.selection_range[1])
            painter.fillRect(
                QtCore.QRectF(x_start, inner.top(), max(3.0, x_end - x_start), inner.height()),
                APP_SELECTION_FILL,
            )

        if self._drag_start is not None and self._drag_end is not None:
            x_start = min(self._drag_start, self._drag_end)
            x_end = max(self._drag_start, self._drag_end)
            painter.fillRect(
                QtCore.QRectF(x_start, inner.top(), max(3.0, x_end - x_start), inner.height()),
                APP_SELECTION_FILL,
            )

        path = QtGui.QPainterPath()
        path.moveTo(map_x(self.times[0]), map_y(self.values[0]))
        for time_value, sample_value in zip(self.times[1:], self.values[1:]):
            path.lineTo(map_x(time_value), map_y(sample_value))

        painter.setPen(QtGui.QPen(QtGui.QColor(APP_ACCENT_ALT), 2.2))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawPath(path)

        painter.setPen(QtGui.QColor(APP_MUTED))
        axis_font = painter.font()
        axis_font.setPointSize(9)
        painter.setFont(axis_font)
        y_axis_rect = QtCore.QRectF(plot_rect.left() + 10, inner.top() - 4, inner.left() - plot_rect.left() - 18, inner.height() + 8)
        y_label_values = (
            (inner.top(), value_max),
            (inner.top() + inner.height() / 2.0, (value_max + value_min) / 2.0),
            (inner.bottom(), value_min),
        )
        for y_pos, axis_value in y_label_values:
            label_rect = QtCore.QRectF(y_axis_rect.left(), y_pos - 10, y_axis_rect.width(), 20)
            painter.drawText(
                label_rect,
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                "%.1f" % axis_value,
            )
        painter.drawText(
            QtCore.QRectF(inner.left(), plot_rect.bottom() - 18, 120, 16),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            format_seconds(time_min),
        )
        painter.drawText(
            QtCore.QRectF(inner.right() - 130, plot_rect.bottom() - 18, 130, 16),
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
            format_seconds(time_max),
        )
        painter.drawText(
            QtCore.QRectF(plot_rect.right() - 100, plot_rect.top() + 12, 80, 16),
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
            self.unit or "value",
        )

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton or not self.times:
            return
        plot_rect = self._inner_rect()
        if not plot_rect.contains(event.position()):
            return
        self._drag_start = event.position().x()
        self._drag_end = event.position().x()
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag_start is None:
            return
        self._drag_end = event.position().x()
        self.update()

    def mouseReleaseEvent(self, _event):
        if self._drag_start is None or self._drag_end is None or not self.times:
            self._drag_start = None
            self._drag_end = None
            self.update()
            return

        inner = self._inner_rect()
        time_min = min(self.times)
        time_max = max(self.times)

        def clamp_x(value):
            return max(inner.left(), min(value, inner.right()))

        start_x = clamp_x(self._drag_start)
        end_x = clamp_x(self._drag_end)
        self._drag_start = None
        self._drag_end = None

        if abs(end_x - start_x) < 6:
            self.update()
            return

        def x_to_time(x_value):
            ratio = (x_value - inner.left()) / max(1e-9, inner.width())
            return time_min + ((time_max - time_min) * ratio)

        start_time = min(x_to_time(start_x), x_to_time(end_x))
        end_time = max(x_to_time(start_x), x_to_time(end_x))
        self.selection_range = (start_time, end_time)
        self.range_selected.emit(start_time, end_time)
        self.update()


class MetadataEditor(QtWidgets.QWidget):
    field_edited = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.widgets = {}
        self._loading = False
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(12)

        half = math.ceil(len(METADATA_FIELDS) / 2)
        for index, (field_name, label_text) in enumerate(METADATA_FIELDS):
            column_group = 0 if index < half else 1
            row = index if index < half else index - half
            base_column = column_group * 2

            label = QtWidgets.QLabel(label_text)
            label.setObjectName("FieldLabel")
            layout.addWidget(label, row, base_column)

            if field_name in TEXT_EDIT_FIELDS:
                widget = QtWidgets.QPlainTextEdit()
                widget.setFixedHeight(70)
                widget.textChanged.connect(lambda name=field_name: self._emit_edit(name))
            else:
                widget = QtWidgets.QLineEdit()
                widget.textEdited.connect(lambda _text, name=field_name: self._emit_edit(name))
            widget.setObjectName("FieldInput")
            self.widgets[field_name] = widget
            layout.addWidget(widget, row, base_column + 1)

        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

    def _emit_edit(self, field_name):
        if self._loading:
            return
        self.field_edited.emit(field_name)

    def set_values(self, values):
        self._loading = True
        try:
            for field_name, widget in self.widgets.items():
                value = values.get(field_name, "")
                if isinstance(widget, QtWidgets.QPlainTextEdit):
                    widget.setPlainText(value)
                else:
                    widget.setText(value)
        finally:
            self._loading = False

    def field_value(self, field_name):
        widget = self.widgets[field_name]
        if isinstance(widget, QtWidgets.QPlainTextEdit):
            return widget.toPlainText()
        return widget.text()

    def set_enabled(self, enabled):
        for widget in self.widgets.values():
            widget.setEnabled(enabled)


class MotecQtWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.file_items = {}
        self.row_items = {}
        self.next_item_number = 1
        self.preview_token_counter = 0
        self.preview_request_id = None
        self.current_preview_id = None
        self.metadata_dirty_fields = set()
        self.undo_stack = []
        self.max_undo_steps = 40
        self.editor_loading = False
        self.segment_table_updating = False
        self.pending_segment_active = False
        self.pending_segment_values = [None, None]
        self.generation_running = False
        self.closed = False
        self.preview_pool = QtCore.QThreadPool(self)
        self.preview_pool.setMaxThreadCount(3)
        self.convert_pool = QtCore.QThreadPool(self)
        self.convert_pool.setMaxThreadCount(1)

        self.setWindowTitle("MoTeC Log Generator - Qt")
        self.setWindowIcon(load_app_icon())
        self.resize(1540, 980)
        self.setMinimumSize(1300, 820)
        self._apply_styles()
        self._build_ui()
        self._bind_shortcuts()
        self.statusBar().showMessage("Add logs to begin.")

    def _apply_styles(self):
        self.setStyleSheet(
            """
            QMainWindow {
                background: #0b0f14;
            }
            QWidget {
                color: #f4f7fb;
                font-family: "SF Pro Text", "Inter", "Segoe UI", "Helvetica Neue", "Arial";
                font-size: 13px;
            }
            QWidget#AppRoot {
                background: #0b0f14;
            }
            QFrame#FloatingCard {
                background: #161f2b;
                border: 1px solid #273647;
                border-radius: 28px;
            }
            QLabel {
                background: transparent;
            }
            QLabel#CardTitle {
                font-size: 17px;
                font-weight: 700;
                color: #f4f7fb;
            }
            QLabel#CardSubtitle {
                font-size: 11px;
                color: #94a7bd;
            }
            QLabel#SectionTitle {
                font-size: 18px;
                font-weight: 700;
                color: #f4f7fb;
            }
            QLabel#SectionSubtitle {
                font-size: 11px;
                color: #94a7bd;
            }
            QLabel#FieldLabel {
                color: #c6d2df;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#InfoChip {
                background: #1d2937;
                color: #c7d5e3;
                border: 1px solid #2f4256;
                border-radius: 15px;
                padding: 8px 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QLineEdit, QPlainTextEdit, QComboBox {
                background: #1b2633;
                color: #f4f7fb;
                border: 1px solid #314356;
                border-radius: 16px;
                padding: 10px 12px;
                selection-background-color: #2f7cff;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0;
                height: 0;
            }
            QPushButton, QToolButton {
                background: #1d2937;
                color: #f4f7fb;
                border: 1px solid #314356;
                border-radius: 18px;
                padding: 11px 16px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton:hover, QToolButton:hover {
                background: #243345;
            }
            QPushButton:disabled, QToolButton:disabled {
                background: #1a2330;
                color: #6d7e92;
                border-color: #283646;
            }
            QPushButton#PrimaryButton, QToolButton#PrimaryButton {
                background: #2f7cff;
                border: 1px solid #2f7cff;
                color: white;
            }
            QPushButton#PrimaryButton:hover, QToolButton#PrimaryButton:hover {
                background: #4a91ff;
            }
            QTreeWidget#QueueTree, QTableWidget#SegmentTable {
                background: #111821;
                alternate-background-color: #111821;
                border: 1px solid #273647;
                border-radius: 20px;
                outline: none;
                gridline-color: #273647;
            }
            QHeaderView::section {
                background: #1b2633;
                color: #94a7bd;
                border: none;
                border-right: 1px solid #273647;
                border-bottom: 1px solid #273647;
                padding: 10px 12px;
                font-size: 11px;
                font-weight: 700;
            }
            QTreeWidget::item, QTableWidget::item {
                padding: 10px 8px;
                border: none;
            }
            QTreeWidget::item:selected, QTableWidget::item:selected {
                background: #203247;
                color: #f4f7fb;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 12px;
                margin: 6px 0px;
            }
            QScrollBar::handle:vertical {
                background: #40566e;
                border-radius: 6px;
                min-height: 36px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 12px;
                margin: 0px 6px;
            }
            QScrollBar::handle:horizontal {
                background: #40566e;
                border-radius: 6px;
                min-width: 36px;
            }
            QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
                background: transparent;
                border: none;
            }
            QProgressBar {
                border: 1px solid #273647;
                border-radius: 13px;
                background: #111821;
                text-align: center;
                min-height: 14px;
            }
            QProgressBar::chunk {
                background: #2f7cff;
                border-radius: 12px;
            }
            QStatusBar {
                background: transparent;
                color: #94a7bd;
            }
            QSplitter::handle {
                background: transparent;
                width: 22px;
            }
            """
        )

    def _build_ui(self):
        central = QtWidgets.QWidget()
        central.setObjectName("AppRoot")
        self.setCentralWidget(central)
        root_layout = QtWidgets.QHBoxLayout(central)
        root_layout.setContentsMargins(22, 22, 22, 22)
        root_layout.setSpacing(18)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(22)
        root_layout.addWidget(splitter)

        left_content = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_content)
        left_layout.setContentsMargins(2, 0, 8, 0)
        left_layout.setSpacing(18)

        center_content = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center_content)
        center_layout.setContentsMargins(4, 0, 4, 0)
        center_layout.setSpacing(18)

        right_content = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_content)
        right_layout.setContentsMargins(8, 0, 2, 0)
        right_layout.setSpacing(18)

        left_scroll = self._scroll_wrapper(left_content)
        right_scroll = self._scroll_wrapper(right_content)
        left_scroll.setMinimumWidth(440)
        right_scroll.setMinimumWidth(470)
        splitter.addWidget(left_scroll)
        splitter.addWidget(center_content)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 30)
        splitter.setStretchFactor(1, 40)
        splitter.setStretchFactor(2, 30)
        splitter.setSizes([460, 720, 500])

        self.queue_card = FloatingCard(
            "Source Files",
            "Preview-first queue with auto-loaded sessions and drag-and-drop support.",
        )
        left_layout.addWidget(self.queue_card, 1)

        queue_controls = QtWidgets.QHBoxLayout()
        queue_controls.setSpacing(10)
        self.add_button = QtWidgets.QToolButton()
        self.add_button.setObjectName("PrimaryButton")
        self.add_button.setText("Add")
        self.add_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        add_menu = QtWidgets.QMenu(self)
        add_menu.addAction("Files...", self._add_files)
        add_menu.addAction("Folder...", self._add_folder)
        self.add_button.setMenu(add_menu)
        self.add_button.setMinimumWidth(86)

        self.undo_button = QtWidgets.QPushButton("Undo")
        self.undo_button.clicked.connect(self._on_undo)
        self.undo_button.setMinimumWidth(86)

        queue_controls.addWidget(self.add_button)
        queue_controls.addWidget(self.undo_button)
        queue_controls.addStretch(1)
        self.queue_card.add_layout(queue_controls)

        self.queue_widget = FileQueueWidget()
        self.queue_widget.itemSelectionChanged.connect(self._on_queue_selection_changed)
        self.queue_widget.files_dropped.connect(self._handle_dropped_paths)
        self.queue_widget.delete_pressed.connect(self._remove_selected_files)
        self.queue_card.add_widget(self.queue_widget, 1)

        self.queue_hint = QtWidgets.QLabel(
            "Rows dim while previews load. Delete removes selected files. Undo keeps loaded previews in memory when it can."
        )
        self.queue_hint.setObjectName("CardSubtitle")
        self.queue_hint.setWordWrap(True)
        self.queue_card.add_widget(self.queue_hint)

        self.settings_card = FloatingCard("Conversion Settings", "Shared output and decoder options for the queue.")
        left_layout.addWidget(self.settings_card)

        settings_grid = QtWidgets.QGridLayout()
        settings_grid.setHorizontalSpacing(10)
        settings_grid.setVerticalSpacing(10)
        self.settings_card.add_layout(settings_grid)

        self.frequency_edit = QtWidgets.QLineEdit("Auto")
        self.output_edit = QtWidgets.QLineEdit()
        self.dbc_edit = QtWidgets.QLineEdit()
        self.dbc_edit.textChanged.connect(self._on_dbc_path_changed)
        self.unit_chart_edit = QtWidgets.QLineEdit()
        self.unit_chart_edit.editingFinished.connect(self._on_unit_chart_path_changed)

        output_button = QtWidgets.QPushButton("...")
        output_button.setMinimumWidth(42)
        output_button.clicked.connect(self._browse_output)
        dbc_button = QtWidgets.QPushButton("...")
        dbc_button.setMinimumWidth(42)
        dbc_button.clicked.connect(self._browse_dbc)
        self.unit_chart_button = QtWidgets.QPushButton("...")
        self.unit_chart_button.setMinimumWidth(42)
        self.unit_chart_button.clicked.connect(self._browse_unit_chart)

        settings_grid.addWidget(self._label("Frequency"), 0, 0)
        settings_grid.addWidget(self.frequency_edit, 0, 1, 1, 2)
        settings_grid.addWidget(self._label("Output"), 1, 0)
        settings_grid.addWidget(self.output_edit, 1, 1)
        settings_grid.addWidget(output_button, 1, 2)
        settings_grid.addWidget(self._label("DBC"), 2, 0)
        settings_grid.addWidget(self.dbc_edit, 2, 1)
        settings_grid.addWidget(dbc_button, 2, 2)
        settings_grid.addWidget(self._label("Channel Chart"), 3, 0)
        settings_grid.addWidget(self.unit_chart_edit, 3, 1)
        settings_grid.addWidget(self.unit_chart_button, 3, 2)
        settings_grid.setColumnStretch(1, 1)

        self.progress_card = FloatingCard("Progress", "Convert the queue when your preview and metadata look right.")
        left_layout.addWidget(self.progress_card)

        self.progress_status = QtWidgets.QLabel("Ready")
        self.progress_status.setObjectName("SectionTitle")
        self.progress_summary = QtWidgets.QLabel("Add logs, preview speed, trim and split, then convert.")
        self.progress_summary.setObjectName("CardSubtitle")
        self.progress_summary.setWordWrap(True)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.convert_button = QtWidgets.QPushButton("Convert")
        self.convert_button.setObjectName("PrimaryButton")
        self.convert_button.setMinimumHeight(46)
        self.convert_button.clicked.connect(self._generate)

        self.progress_card.add_widget(self.progress_status)
        self.progress_card.add_widget(self.progress_summary)
        self.progress_card.add_widget(self.progress_bar)
        self.progress_card.add_widget(self.convert_button)

        self.preview_header = FloatingCard()
        center_layout.addWidget(self.preview_header)
        self.preview_title = QtWidgets.QLabel("Select a file to preview")
        self.preview_title.setObjectName("SectionTitle")
        self.preview_subtitle = QtWidgets.QLabel("The graph will default to the best speed channel it can find.")
        self.preview_subtitle.setObjectName("SectionSubtitle")
        self.preview_subtitle.setWordWrap(True)
        self.preview_header.add_widget(self.preview_title)
        self.preview_header.add_widget(self.preview_subtitle)

        self.preview_card = FloatingCard()
        center_layout.addWidget(self.preview_card, 1)

        preview_controls = QtWidgets.QGridLayout()
        preview_controls.setHorizontalSpacing(12)
        preview_controls.setVerticalSpacing(10)
        self.preview_card.add_layout(preview_controls)

        self.preview_channel_combo = QtWidgets.QComboBox()
        self.preview_channel_combo.currentTextChanged.connect(self._on_preview_channel_changed)
        self.motion_channel_combo = QtWidgets.QComboBox()
        self.motion_channel_combo.currentTextChanged.connect(self._on_motion_channel_changed)

        preview_controls.addWidget(self._label("Preview Channel"), 0, 0)
        preview_controls.addWidget(self._label("Motion Channel"), 0, 1)
        preview_controls.addWidget(self.preview_channel_combo, 1, 0)
        preview_controls.addWidget(self.motion_channel_combo, 1, 1)
        preview_controls.setColumnStretch(0, 1)
        preview_controls.setColumnStretch(1, 1)

        self.plot_widget = PlotPreviewWidget()
        self.plot_widget.range_selected.connect(self._on_plot_range_selected)
        self.preview_card.add_widget(self.plot_widget, 1)

        preview_footer = QtWidgets.QHBoxLayout()
        preview_footer.setSpacing(10)
        self.selection_chip = InfoChip("No range selected")
        self.motion_chip = InfoChip("Waiting for file")
        self.preview_note_chip = InfoChip("Preview hidden for multi-select.")
        preview_footer.addWidget(self.selection_chip)
        preview_footer.addWidget(self.motion_chip)
        preview_footer.addWidget(self.preview_note_chip, 1)
        self.preview_card.add_layout(preview_footer)

        self.split_card = FloatingCard(
            "Trim & Split",
            "Split detects movement automatically. Select a row and drag on the graph to adjust it, or use + to add one.",
        )
        right_layout.addWidget(self.split_card)

        self.segment_table = SegmentTableWidget()
        self.segment_table.itemSelectionChanged.connect(self._on_segment_selection_changed)
        self.segment_table.cellChanged.connect(self._on_segment_cell_changed)
        self.segment_table.delete_pressed.connect(self._remove_selected_segment)
        self.split_card.add_widget(self.segment_table)

        split_buttons = QtWidgets.QHBoxLayout()
        split_buttons.setSpacing(8)
        self.add_segment_button = QtWidgets.QPushButton("+")
        self.add_segment_button.setMinimumWidth(44)
        self.add_segment_button.clicked.connect(self._add_segment_row)
        self.remove_segment_button = QtWidgets.QPushButton("-")
        self.remove_segment_button.setMinimumWidth(44)
        self.remove_segment_button.clicked.connect(self._remove_selected_segment)
        self.auto_split_button = QtWidgets.QPushButton("Split")
        self.auto_split_button.setMinimumWidth(78)
        self.auto_split_button.clicked.connect(self._auto_split_selected)
        self.reset_segments_button = QtWidgets.QPushButton("Full")
        self.reset_segments_button.setMinimumWidth(70)
        self.reset_segments_button.clicked.connect(self._reset_selected_ranges)
        self.make_files_button = QtWidgets.QPushButton("Make Files")
        self.make_files_button.setObjectName("PrimaryButton")
        self.make_files_button.setMinimumWidth(110)
        self.make_files_button.clicked.connect(self._apply_segment_plan_to_queue)

        split_buttons.addWidget(self.add_segment_button)
        split_buttons.addWidget(self.remove_segment_button)
        split_buttons.addWidget(self.auto_split_button)
        split_buttons.addWidget(self.reset_segments_button)
        split_buttons.addWidget(self.make_files_button)
        self.split_card.add_layout(split_buttons)

        self.segment_hint = QtWidgets.QLabel(
            "Double-click Start or End to type exact times. The Length column updates automatically."
        )
        self.segment_hint.setObjectName("CardSubtitle")
        self.segment_hint.setWordWrap(True)
        self.split_card.add_widget(self.segment_hint)

        self.metadata_card = FloatingCard(
            "MoTeC Parameters",
            "All MoTeC metadata fields are available here. Multi-select applies edited fields to every selected file.",
        )
        right_layout.addWidget(self.metadata_card, 1)

        self.metadata_editor = MetadataEditor()
        self.metadata_editor.field_edited.connect(self._mark_metadata_dirty)
        self.metadata_card.add_widget(self.metadata_editor)

        self.metadata_apply_button = QtWidgets.QPushButton("Apply Metadata")
        self.metadata_apply_button.setObjectName("PrimaryButton")
        self.metadata_apply_button.setMinimumHeight(46)
        self.metadata_apply_button.clicked.connect(self._apply_metadata_to_selected)
        self.metadata_card.add_widget(self.metadata_apply_button)

        self._set_editor_enabled(False, single_preview=False)
        self._update_undo_button_state()

    def _scroll_wrapper(self, widget):
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        widget.setAttribute(QtCore.Qt.WA_StyledBackground, False)
        widget.setAutoFillBackground(False)
        viewport_palette = scroll.viewport().palette()
        viewport_palette.setColor(QtGui.QPalette.Window, QtGui.QColor(APP_BG))
        scroll.viewport().setPalette(viewport_palette)
        scroll.viewport().setAutoFillBackground(True)
        return scroll

    def _label(self, text):
        label = QtWidgets.QLabel(text)
        label.setObjectName("FieldLabel")
        return label

    def _bind_shortcuts(self):
        undo_shortcut = QtGui.QShortcut(QtGui.QKeySequence.Undo, self)
        undo_shortcut.activated.connect(self._on_undo)

    def _new_item_id(self):
        item_id = "qt-item-%04d" % self.next_item_number
        self.next_item_number += 1
        return item_id

    def _loaded_preview_bytes(self, items=None):
        total_bytes = 0
        for item in items or self.file_items.values():
            if item.preview_log is None:
                continue
            try:
                total_bytes += os.path.getsize(item.path)
            except OSError:
                continue
        return total_bytes

    def _capture_queue_snapshot(self):
        keep_loaded_previews = self._loaded_preview_bytes() <= UNDO_PREVIEW_LIMIT_BYTES
        ordered_items = []
        for index in range(self.queue_widget.topLevelItemCount()):
            row_item = self.queue_widget.topLevelItem(index)
            item_id = row_item.data(0, QtCore.Qt.UserRole)
            if item_id not in self.file_items:
                continue
            source_item = self.file_items[item_id]
            copied_item = copy.copy(source_item)
            copied_item.settings = source_item.settings.copy()
            copied_item.outputs = list(source_item.outputs)
            copied_item.preview_channels = list(source_item.preview_channels)
            copied_item.preview_loading = False
            copied_item.preview_token = 0
            if not keep_loaded_previews:
                copied_item.preview_log = None
                copied_item.preview_channels = []
            if copied_item.preview_log is None and not copied_item.preview_error and copied_item.status != "Done":
                copied_item.status = "Queued"
                copied_item.detail = ""
            ordered_items.append(copied_item)
        return {
            "items": ordered_items,
            "selected_ids": [row.data(0, QtCore.Qt.UserRole) for row in self.queue_widget.selectedItems()],
            "current_id": self.current_preview_id,
            "next_item_number": self.next_item_number,
            "keep_loaded_previews": keep_loaded_previews,
        }

    def _push_undo_state(self):
        self.undo_stack.append(self._capture_queue_snapshot())
        if len(self.undo_stack) > self.max_undo_steps:
            self.undo_stack = self.undo_stack[-self.max_undo_steps :]
        self._update_undo_button_state()

    def _restore_snapshot(self, snapshot):
        self.file_items.clear()
        self.row_items.clear()
        self.queue_widget.clear()
        self.next_item_number = snapshot["next_item_number"]
        self.preview_request_id = None
        self.current_preview_id = None
        self.pending_segment_active = False
        self.pending_segment_values = [None, None]

        for item in snapshot["items"]:
            self.file_items[item.item_id] = item
            row_item = QtWidgets.QTreeWidgetItem()
            row_item.setData(0, QtCore.Qt.UserRole, item.item_id)
            self.queue_widget.addTopLevelItem(row_item)
            self.row_items[item.item_id] = row_item
            self._refresh_queue_row(item.item_id)

        selected_ids = set(snapshot["selected_ids"])
        first_selected = None
        for item_id, row_item in self.row_items.items():
            if item_id in selected_ids:
                row_item.setSelected(True)
                if first_selected is None:
                    first_selected = row_item
        if first_selected is not None:
            self.queue_widget.setCurrentItem(first_selected)
            self.queue_widget.scrollToItem(first_selected)

        self._on_queue_selection_changed()
        self._update_undo_button_state()

        if not snapshot["keep_loaded_previews"]:
            for item in self.file_items.values():
                if item.preview_log is None and not item.preview_error:
                    self._load_preview_for_item(item, show_placeholder=False)

    def _update_undo_button_state(self):
        self.undo_button.setEnabled((not self.generation_running) and bool(self.undo_stack))

    def _on_undo(self):
        focus_widget = self.focusWidget()
        if isinstance(focus_widget, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit, QtWidgets.QComboBox)):
            return
        if not self.undo_stack:
            return
        snapshot = self.undo_stack.pop()
        self._restore_snapshot(snapshot)
        self.progress_summary.setText("Undid the last queue change.")

    def _row_state_color(self, item):
        if item.preview_loading or (item.preview_log is None and not item.preview_error):
            return APP_MUTED
        if item.status == "Error" or item.preview_error:
            return APP_ERROR
        if item.status not in {"Preview Ready", "Queued", "Done"}:
            return APP_ACCENT_ALT
        return APP_TEXT

    def _refresh_queue_row(self, item_id):
        if item_id not in self.file_items or item_id not in self.row_items:
            return
        item = self.file_items[item_id]
        row_item = self.row_items[item_id]
        row_item.setText(0, item.name)
        row_item.setText(1, item.detected_type or "-")
        row_item.setText(2, item.duration_text)
        row_item.setText(3, item.size_text)
        row_item.setToolTip(0, "%s\n%s" % (item.status, item.detail or item.path))
        color = QtGui.QColor(self._row_state_color(item))
        brush = QtGui.QBrush(color)
        for column in range(4):
            row_item.setForeground(column, brush)

    def _selected_items(self):
        selected = []
        for row_item in self.queue_widget.selectedItems():
            item_id = row_item.data(0, QtCore.Qt.UserRole)
            if item_id in self.file_items:
                selected.append(self.file_items[item_id])
        return selected

    def _handle_dropped_paths(self, paths):
        if paths:
            self._push_undo_state()
        for path in paths:
            self._add_input_path(path)

    def _add_files(self):
        paths, _selected_filter = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select supported logs",
            "",
            "Supported Logs (*.csv *.xrk *.xrz *.log);;CSV Logs (*.csv);;AIM Logs (*.xrk *.xrz);;CAN Logs (*.log)",
        )
        if paths:
            self._push_undo_state()
        for path in paths:
            self._add_input_path(path)

    def _add_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select a folder containing log files")
        if not folder:
            return
        self._push_undo_state()
        for file_path in flatten_input_paths([folder]):
            self._add_input_path(file_path)

    def _add_input_path(self, path):
        if not path:
            return

        expanded_path = os.path.expanduser(path)
        if os.path.isdir(expanded_path):
            for file_path in flatten_input_paths([expanded_path]):
                self._add_input_path(file_path)
            return

        if not os.path.isfile(expanded_path):
            return

        detected_type = detect_log_type(expanded_path)
        if detected_type is None:
            return

        if any(item.path == expanded_path and item.derived_from is None for item in self.file_items.values()):
            return

        item = QtFileItem(
            item_id=self._new_item_id(),
            path=expanded_path,
            detected_type=detected_type,
        )
        self.file_items[item.item_id] = item
        row_item = QtWidgets.QTreeWidgetItem()
        row_item.setData(0, QtCore.Qt.UserRole, item.item_id)
        self.queue_widget.addTopLevelItem(row_item)
        self.row_items[item.item_id] = row_item
        self._refresh_queue_row(item.item_id)

        if not self.output_edit.text().strip():
            self.output_edit.setText(os.path.dirname(expanded_path))

        self._load_preview_for_item(item, show_placeholder=False)

    def _remove_selected_files(self):
        selected_items = self._selected_items()
        if not selected_items:
            return
        self._push_undo_state()
        for item in selected_items:
            row_item = self.row_items.pop(item.item_id, None)
            if row_item is not None:
                index = self.queue_widget.indexOfTopLevelItem(row_item)
                self.queue_widget.takeTopLevelItem(index)
            self.file_items.pop(item.item_id, None)
        self._on_queue_selection_changed()

    def _browse_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Select output folder")
        if path:
            self.output_edit.setText(path)

    def _browse_dbc(self):
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select DBC file",
            "",
            "DBC Files (*.dbc);;All Files (*.*)",
        )
        if path:
            self.dbc_edit.setText(path)

    def _browse_unit_chart(self):
        path, _selected = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select channel chart",
            "",
            "CSV Files (*.csv);;All Files (*.*)",
        )
        if path:
            self.unit_chart_edit.setText(path)
            self._on_unit_chart_path_changed()

    def _on_dbc_path_changed(self):
        dbc_path = self.dbc_edit.text().strip()
        for item in self.file_items.values():
            if item.detected_type != "CAN":
                continue
            item.preview_log = None
            item.preview_channels = []
            item.preview_error = ""
            item.preview_loading = False
            item.status = "Queued"
            self._refresh_queue_row(item.item_id)
            if dbc_path:
                self._load_preview_for_item(item, force_reload=True, show_placeholder=(item.item_id == self.current_preview_id))

    def _on_unit_chart_path_changed(self):
        for item in self.file_items.values():
            self.preview_token_counter += 1
            item.preview_token = self.preview_token_counter
            item.preview_log = None
            item.preview_channels = []
            item.preview_error = ""
            item.preview_loading = False
            if item.status == "Preview Ready":
                item.status = "Queued"
                item.detail = ""
            self._refresh_queue_row(item.item_id)

        selected_items = self._selected_items()
        if len(selected_items) == 1:
            self._load_preview_for_item(selected_items[0], force_reload=True, show_placeholder=True)

    def _load_preview_for_item(self, item, force_reload=False, show_placeholder=True):
        if item.preview_log is not None and not force_reload:
            if show_placeholder:
                self._show_single_item_preview(item)
            return

        if item.detected_type == "CAN" and not self.dbc_edit.text().strip():
            item.preview_loading = False
            item.status = "Queued"
            if show_placeholder:
                item.preview_error = "A DBC file is required to preview CAN logs."
                self.plot_widget.clear_preview(item.preview_error, "Provide a DBC to decode CAN channels.")
                self.progress_summary.setText(item.preview_error)
            else:
                item.preview_error = ""
            self._refresh_queue_row(item.item_id)
            return

        if item.preview_loading:
            if show_placeholder:
                self.plot_widget.clear_preview("Loading preview for %s..." % item.name, item.path)
            return

        item.preview_loading = True
        item.status = "Loading Preview"
        item.preview_error = ""
        self.preview_token_counter += 1
        item.preview_token = self.preview_token_counter
        self._refresh_queue_row(item.item_id)

        if show_placeholder:
            self.preview_request_id = item.item_id
            self.plot_widget.clear_preview("Loading preview for %s..." % item.name, item.path)

        task = PreviewLoadTask(
            item.item_id,
            item.preview_token,
            item.path,
            item.detected_type,
            self.dbc_edit.text().strip(),
            self.unit_chart_edit.text().strip(),
        )
        task.signals.preview_finished.connect(self._finish_preview_load)
        self.preview_pool.start(task)

    @QtCore.Slot(str, int, object, str)
    def _finish_preview_load(self, item_id, preview_token, preview_log, error_message):
        if self.closed or item_id not in self.file_items:
            return

        item = self.file_items[item_id]
        if item.preview_token != preview_token:
            return

        item.preview_loading = False
        if error_message:
            item.preview_log = None
            item.preview_channels = []
            item.preview_error = error_message
            item.status = "Error"
            self._refresh_queue_row(item_id)
            if self.preview_request_id == item_id:
                self.plot_widget.clear_preview(error_message, item.path)
                self.progress_summary.setText(error_message)
            return

        item.preview_log = preview_log
        item.preview_channels = preview_log.channel_names()
        item.duration = preview_log.duration()
        item.status = "Preview Ready"
        item.preview_error = ""

        if item.settings.preview_channel not in item.preview_channels:
            item.settings.preview_channel = preview_log.infer_speed_channel() or (item.preview_channels[0] if item.preview_channels else "")
        if item.settings.motion_channel not in item.preview_channels:
            item.settings.motion_channel = preview_log.infer_speed_channel() or item.settings.preview_channel
        if not item.settings.segment_ranges and item.derived_from is not None:
            item.settings.segment_ranges = [(preview_log.start(), preview_log.end())]

        self._refresh_queue_row(item_id)
        selected_items = self._selected_items()
        if len(selected_items) == 1 and selected_items[0].item_id == item_id:
            self._populate_metadata_editor(selected_items)
            self._show_single_item_preview(item)

    def _set_editor_enabled(self, enabled, single_preview):
        trim_enabled = enabled and single_preview
        metadata_enabled = enabled
        preview_enabled = enabled and single_preview

        self.metadata_editor.set_enabled(metadata_enabled)
        self.metadata_apply_button.setEnabled(metadata_enabled)
        self.preview_channel_combo.setEnabled(preview_enabled)
        self.motion_channel_combo.setEnabled(preview_enabled)
        for button in (
            self.add_segment_button,
            self.remove_segment_button,
            self.auto_split_button,
            self.reset_segments_button,
            self.make_files_button,
        ):
            button.setEnabled(trim_enabled)
        self.segment_table.setEnabled(trim_enabled)

    def _mark_metadata_dirty(self, field_name):
        self.metadata_dirty_fields.add(field_name)

    def _populate_metadata_editor(self, selected_items):
        self.editor_loading = True
        try:
            if not selected_items:
                self.metadata_editor.set_values({field_name: "" for field_name, _label in METADATA_FIELDS})
                self.preview_channel_combo.blockSignals(True)
                self.motion_channel_combo.blockSignals(True)
                self.preview_channel_combo.clear()
                self.motion_channel_combo.clear()
                self.preview_channel_combo.blockSignals(False)
                self.motion_channel_combo.blockSignals(False)
                self.pending_segment_active = False
                self.pending_segment_values = [None, None]
                self._populate_segment_table([])
                return

            values = {}
            for field_name, _label in METADATA_FIELDS:
                unique_values = {getattr(item.settings, field_name) for item in selected_items}
                values[field_name] = unique_values.pop() if len(unique_values) == 1 else ""
            self.metadata_editor.set_values(values)

            if len(selected_items) == 1:
                item = selected_items[0]
                self.preview_channel_combo.blockSignals(True)
                self.motion_channel_combo.blockSignals(True)
                self.preview_channel_combo.clear()
                self.motion_channel_combo.clear()
                self.preview_channel_combo.addItems(item.preview_channels)
                self.motion_channel_combo.addItems(item.preview_channels)
                if item.settings.preview_channel:
                    self.preview_channel_combo.setCurrentText(item.settings.preview_channel)
                if item.settings.motion_channel:
                    self.motion_channel_combo.setCurrentText(item.settings.motion_channel)
                self.preview_channel_combo.blockSignals(False)
                self.motion_channel_combo.blockSignals(False)
                self._populate_segment_table(item.settings.segment_ranges)
            else:
                self.pending_segment_active = False
                self.pending_segment_values = [None, None]
                self.preview_channel_combo.blockSignals(True)
                self.motion_channel_combo.blockSignals(True)
                self.preview_channel_combo.clear()
                self.motion_channel_combo.clear()
                self.preview_channel_combo.blockSignals(False)
                self.motion_channel_combo.blockSignals(False)
                self._populate_segment_table([])
        finally:
            self.editor_loading = False

    def _on_queue_selection_changed(self):
        selected_items = self._selected_items()
        self.metadata_dirty_fields.clear()

        if len(selected_items) != 1:
            self.current_preview_id = None
        if len(selected_items) != 1:
            self.pending_segment_active = False
            self.pending_segment_values = [None, None]

        if not selected_items:
            self.preview_title.setText("Select a file to preview")
            self.preview_subtitle.setText("The graph will default to the best speed channel it can find.")
            self.preview_note_chip.setText("Select one file to preview.")
            self.motion_chip.setText("Waiting for file")
            self.selection_chip.setText("No range selected")
            self._populate_metadata_editor([])
            self.plot_widget.clear_preview("Select one file to preview.", "Preview is hidden until one file is selected.")
            self._set_editor_enabled(False, single_preview=False)
            return

        self._populate_metadata_editor(selected_items)

        if len(selected_items) > 1:
            self.preview_title.setText("%d files selected" % len(selected_items))
            self.preview_subtitle.setText("Preview is hidden while multiple files are selected. Metadata edits apply to every selected file.")
            self.preview_note_chip.setText("Multi-select metadata mode")
            self.motion_chip.setText("Preview hidden")
            self.selection_chip.setText("No range selected")
            self.plot_widget.clear_preview(
                "%d files selected." % len(selected_items),
                "Preview is hidden while you batch-edit metadata.",
            )
            self._set_editor_enabled(True, single_preview=False)
            return

        item = selected_items[0]
        self.preview_title.setText(item.name)
        self.preview_subtitle.setText(item.path)
        self.preview_note_chip.setText("Single-file preview mode")
        self._set_editor_enabled(True, single_preview=True)
        self._load_preview_for_item(item, show_placeholder=True)

    def _selected_segment_descriptor(self):
        row = self.segment_table.currentRow()
        if row < 0:
            return None
        segment_count = len(self._current_item_segment_ranges())
        if self.pending_segment_active and row == segment_count:
            return ("pending", None)
        if row >= segment_count:
            return None
        return ("existing", row)

    def _current_item_segment_ranges(self):
        selected_items = self._selected_items()
        if len(selected_items) != 1:
            return []
        return list(selected_items[0].settings.segment_ranges)

    def _populate_segment_table(self, segment_ranges):
        selected_descriptor = self._selected_segment_descriptor()
        self.segment_table_updating = True
        self.segment_table.blockSignals(True)
        self.segment_table.setRowCount(0)
        try:
            for row_index, (start_time, end_time) in enumerate(segment_ranges):
                self.segment_table.insertRow(row_index)
                self._set_segment_row_values(row_index, start_time, end_time)

            if self.pending_segment_active:
                row_index = self.segment_table.rowCount()
                self.segment_table.insertRow(row_index)
                start_value, end_value = self.pending_segment_values
                self._set_segment_row_values(row_index, start_value, end_value, allow_blank=True)
        finally:
            self.segment_table.blockSignals(False)
            self.segment_table_updating = False

        if selected_descriptor is not None:
            if selected_descriptor[0] == "pending" and self.pending_segment_active:
                target_row = len(segment_ranges)
            else:
                target_row = selected_descriptor[1]
            if 0 <= target_row < self.segment_table.rowCount():
                self.segment_table.setCurrentCell(target_row, 0)
                self.segment_table.selectRow(target_row)
                return

        if self.segment_table.rowCount() > 0:
            self.segment_table.setCurrentCell(0, 0)
            self.segment_table.selectRow(0)

    def _set_segment_row_values(self, row_index, start_time, end_time, allow_blank=False):
        values = [start_time, end_time]
        for column, raw_value in enumerate(values):
            if raw_value is None and allow_blank:
                text = ""
            elif raw_value is None:
                text = "-"
            else:
                text = "%.2f" % float(raw_value)
            item = QtWidgets.QTableWidgetItem(text)
            if column == 2:
                item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.segment_table.setItem(row_index, column, item)

        if start_time is None or end_time is None:
            length_text = ""
        else:
            length_text = "%.2f" % abs(float(end_time) - float(start_time))
        length_item = QtWidgets.QTableWidgetItem(length_text)
        length_item.setFlags(length_item.flags() & ~QtCore.Qt.ItemIsEditable)
        self.segment_table.setItem(row_index, 2, length_item)

    def _parse_segment_cell(self, row, column):
        item = self.segment_table.item(row, column)
        if item is None:
            return None
        text = item.text().strip()
        if not text:
            return None
        return float(text)

    def _row_index_for_range(self, segment_ranges, target_range):
        if not segment_ranges:
            return None
        best_index = min(
            range(len(segment_ranges)),
            key=lambda index: (
                abs(segment_ranges[index][0] - target_range[0])
                + abs(segment_ranges[index][1] - target_range[1])
            ),
        )
        return best_index

    def _apply_segment_range(self, item, descriptor, new_range, summary_message):
        if item.preview_log is None:
            return

        start_time, end_time = sorted((float(new_range[0]), float(new_range[1])))
        self._push_undo_state()

        segment_ranges = list(item.settings.segment_ranges)
        if descriptor is None or descriptor[0] == "pending":
            segment_ranges.append((start_time, end_time))
        else:
            segment_ranges[descriptor[1]] = (start_time, end_time)

        item.settings.segment_ranges = normalize_segment_ranges(
            segment_ranges,
            item.preview_log.start(),
            item.preview_log.end(),
        )
        self.pending_segment_active = False
        self.pending_segment_values = [None, None]
        self._populate_segment_table(item.settings.segment_ranges)
        nearest_row = self._row_index_for_range(item.settings.segment_ranges, (start_time, end_time))
        if nearest_row is not None and nearest_row < self.segment_table.rowCount():
            self.segment_table.setCurrentCell(nearest_row, 0)
            self.segment_table.selectRow(nearest_row)
        self._show_single_item_preview(item)
        self.progress_summary.setText(summary_message)
        self.selection_chip.setText("Selection %s - %s" % (format_seconds(start_time), format_seconds(end_time)))

    def _on_segment_selection_changed(self):
        selected_items = self._selected_items()
        if len(selected_items) != 1:
            return
        item = selected_items[0]
        if item.preview_log is None:
            return
        self._show_single_item_preview(item)

    def _on_segment_cell_changed(self, row, column):
        if self.segment_table_updating or column not in (0, 1):
            return

        selected_items = self._selected_items()
        if len(selected_items) != 1 or selected_items[0].preview_log is None:
            return
        item = selected_items[0]
        descriptor = self._selected_segment_descriptor()
        if descriptor is None:
            return

        try:
            value = self._parse_segment_cell(row, column)
        except ValueError:
            self.progress_summary.setText("Segment times must be numeric seconds.")
            self._populate_segment_table(item.settings.segment_ranges)
            return

        if descriptor[0] == "pending":
            self.pending_segment_active = True
            self.pending_segment_values[column] = value
            if None not in self.pending_segment_values:
                self._apply_segment_range(item, descriptor, tuple(self.pending_segment_values), "Added a new segment.")
            else:
                self._populate_segment_table(item.settings.segment_ranges)
                pending_row = self.segment_table.rowCount() - 1
                if pending_row >= 0:
                    self.segment_table.setCurrentCell(pending_row, column)
            return

        if value is None:
            self.progress_summary.setText("Existing segments need both a start and an end.")
            self._populate_segment_table(item.settings.segment_ranges)
            return

        current_range = list(item.settings.segment_ranges[descriptor[1]])
        current_range[column] = value
        self._apply_segment_range(item, descriptor, tuple(current_range), "Updated the selected segment.")

    def _add_segment_row(self):
        selected_items = self._selected_items()
        if len(selected_items) != 1 or selected_items[0].preview_log is None:
            return
        self.pending_segment_active = True
        self.pending_segment_values = [None, None]
        self._populate_segment_table(selected_items[0].settings.segment_ranges)
        pending_row = self.segment_table.rowCount() - 1
        if pending_row >= 0:
            self.segment_table.setCurrentCell(pending_row, 0)
            self.segment_table.selectRow(pending_row)
        self.progress_summary.setText("New segment row added. Drag on the graph or type times directly into Start and End.")

    def _remove_selected_segment(self):
        selected_items = self._selected_items()
        if len(selected_items) != 1 or selected_items[0].preview_log is None:
            return
        item = selected_items[0]
        descriptor = self._selected_segment_descriptor()
        if descriptor is None:
            return

        if descriptor[0] == "pending":
            self.pending_segment_active = False
            self.pending_segment_values = [None, None]
            self._populate_segment_table(item.settings.segment_ranges)
            self._show_single_item_preview(item)
            self.progress_summary.setText("Cleared the pending segment row.")
            return

        if descriptor[1] >= len(item.settings.segment_ranges):
            return

        self._push_undo_state()
        item.settings.segment_ranges.pop(descriptor[1])
        self._populate_segment_table(item.settings.segment_ranges)
        self._show_single_item_preview(item)
        self.progress_summary.setText("Removed one planned segment.")

    def _auto_split_selected(self):
        selected_items = self._selected_items()
        if len(selected_items) != 1 or selected_items[0].preview_log is None:
            return
        item = selected_items[0]
        motion_channel = item.preview_log.get_channel(item.settings.motion_channel)
        split_ranges = detect_split_ranges(motion_channel)
        if not split_ranges:
            self.progress_summary.setText("No motion data was found for an automatic split.")
            return
        self._push_undo_state()
        self.pending_segment_active = False
        self.pending_segment_values = [None, None]
        item.settings.segment_ranges = split_ranges
        self._populate_segment_table(item.settings.segment_ranges)
        self._show_single_item_preview(item)
        self.progress_summary.setText("Split found %d segment(s)." % len(split_ranges))

    def _reset_selected_ranges(self):
        selected_items = self._selected_items()
        if len(selected_items) != 1 or selected_items[0].preview_log is None:
            return
        item = selected_items[0]
        self._push_undo_state()
        self.pending_segment_active = False
        self.pending_segment_values = [None, None]
        item.settings.segment_ranges = []
        self._populate_segment_table(item.settings.segment_ranges)
        self._show_single_item_preview(item)
        self.progress_summary.setText("Reset the file to the full session.")

    def _apply_segment_plan_to_queue(self):
        selected_items = self._selected_items()
        if len(selected_items) != 1 or selected_items[0].preview_log is None:
            return
        item = selected_items[0]
        segment_ranges = normalize_segment_ranges(
            item.settings.segment_ranges or [(item.preview_log.start(), item.preview_log.end())],
            item.preview_log.start(),
            item.preview_log.end(),
        )
        if not segment_ranges:
            self.progress_summary.setText("There are no valid segments to create.")
            return

        self._push_undo_state()
        source_row = self.row_items[item.item_id]
        insertion_index = self.queue_widget.indexOfTopLevelItem(source_row)
        created_ids = []

        for segment_number, (start_time, end_time) in enumerate(segment_ranges, start=1):
            segment_log = item.preview_log.extract_segment(start_time, end_time, rebase_time=True)
            segment_settings = item.settings.copy()
            segment_settings.segment_ranges = [(segment_log.start(), segment_log.end())]
            output_stem = "%s_part%d" % ((item.output_stem or Path(item.path).stem), segment_number)
            display_base = Path(item.name).stem if "." in item.name else item.name
            display_name = "%s %d" % (display_base, segment_number) if len(segment_ranges) > 1 else "%s Trim" % display_base
            estimated_size_bytes = None
            try:
                source_size_bytes = os.path.getsize(item.path)
            except OSError:
                source_size_bytes = None
            if source_size_bytes is not None and item.duration and item.duration > 0:
                duration_ratio = max(0.0, min(1.0, segment_log.duration() / item.duration))
                estimated_size_bytes = max(1, int(round(source_size_bytes * duration_ratio)))

            new_item = QtFileItem(
                item_id=self._new_item_id(),
                path=item.path,
                detected_type=item.detected_type,
                status="Preview Ready",
                duration=segment_log.duration(),
                preview_log=segment_log,
                preview_channels=segment_log.channel_names(),
                settings=segment_settings,
                display_name=display_name,
                output_stem=output_stem,
                derived_from=item.path,
                display_size_bytes=estimated_size_bytes,
            )
            self.file_items[new_item.item_id] = new_item
            row_item = QtWidgets.QTreeWidgetItem()
            row_item.setData(0, QtCore.Qt.UserRole, new_item.item_id)
            self.queue_widget.insertTopLevelItem(insertion_index + len(created_ids), row_item)
            self.row_items[new_item.item_id] = row_item
            self._refresh_queue_row(new_item.item_id)
            created_ids.append(new_item.item_id)

        original_row = self.row_items.pop(item.item_id, None)
        if original_row is not None:
            original_index = self.queue_widget.indexOfTopLevelItem(original_row)
            self.queue_widget.takeTopLevelItem(original_index)
        self.file_items.pop(item.item_id, None)

        self.queue_widget.clearSelection()
        first_row = None
        for created_id in created_ids:
            row_item = self.row_items[created_id]
            row_item.setSelected(True)
            if first_row is None:
                first_row = row_item
        if first_row is not None:
            self.queue_widget.setCurrentItem(first_row)
            self.queue_widget.scrollToItem(first_row)
        self._on_queue_selection_changed()
        self.progress_summary.setText("Created %d queue item(s) from %s." % (len(created_ids), item.name))

    def _show_single_item_preview(self, item):
        if item.preview_log is None:
            if item.preview_error:
                self.plot_widget.clear_preview(item.preview_error, item.path)
            return

        preview_channel = item.preview_log.get_channel(item.settings.preview_channel)
        motion_channel = item.preview_log.get_channel(item.settings.motion_channel)
        times, values = preview_series_for_channel(preview_channel)

        selected_descriptor = self._selected_segment_descriptor()
        selected_segment_index = None
        if selected_descriptor is not None and selected_descriptor[0] == "existing":
            selected_segment_index = selected_descriptor[1]

        active_start, active_end = detect_active_range(motion_channel)
        subtitle = (
            "Motion: %s  •  Active window: %s"
            % (
                item.settings.motion_channel or item.settings.preview_channel,
                "waiting"
                if active_start is None or active_end is None
                else "%s - %s" % (format_seconds(active_start), format_seconds(active_end)),
            )
        )
        self.plot_widget.set_preview(
            times,
            values,
            title=item.settings.preview_channel or "Preview",
            subtitle=subtitle,
            unit=preview_channel.units if preview_channel and preview_channel.units else "value",
            segment_ranges=item.settings.segment_ranges,
            selected_segment_index=selected_segment_index,
            selection_range=self.plot_widget.selection_range,
        )
        self.current_preview_id = item.item_id
        self.motion_chip.setText(
            "Segments %d  •  Active %s"
            % (
                max(1, len(item.settings.segment_ranges)),
                "waiting"
                if active_start is None or active_end is None
                else "%s - %s" % (format_seconds(active_start), format_seconds(active_end)),
            )
        )
        if self.pending_segment_active:
            self.preview_note_chip.setText("Pending row selected")
        elif selected_segment_index is not None:
            self.preview_note_chip.setText("Selected row follows graph drag")
        else:
            self.preview_note_chip.setText("Drag on the graph to define a range")

    def _on_preview_channel_changed(self, text):
        if self.editor_loading or not text:
            return
        selected_items = self._selected_items()
        if len(selected_items) != 1:
            return
        item = selected_items[0]
        item.settings.preview_channel = text
        self._show_single_item_preview(item)

    def _on_motion_channel_changed(self, text):
        if self.editor_loading or not text:
            return
        selected_items = self._selected_items()
        if len(selected_items) != 1:
            return
        item = selected_items[0]
        item.settings.motion_channel = text
        self._show_single_item_preview(item)

    def _on_plot_range_selected(self, start_time, end_time):
        selected_items = self._selected_items()
        if len(selected_items) != 1 or selected_items[0].preview_log is None:
            return
        item = selected_items[0]
        descriptor = self._selected_segment_descriptor()
        if descriptor is None:
            self.pending_segment_active = True
            self.pending_segment_values = [None, None]
            descriptor = ("pending", None)
        message = "Adjusted the selected segment." if descriptor[0] == "existing" else "Added a new segment."
        self._apply_segment_range(item, descriptor, (start_time, end_time), message)

    def _apply_metadata_to_selected(self):
        selected_items = self._selected_items()
        if not selected_items or not self.metadata_dirty_fields:
            return
        self._push_undo_state()
        for item in selected_items:
            for field_name in self.metadata_dirty_fields:
                setattr(item.settings, field_name, self.metadata_editor.field_value(field_name))
        self.metadata_dirty_fields.clear()
        self.progress_summary.setText("Applied metadata to %d file(s)." % len(selected_items))

    def _update_item_status(self, item_id, status_text):
        if item_id not in self.file_items:
            return
        item = self.file_items[item_id]
        item.status = status_text
        self._refresh_queue_row(item_id)
        self.progress_summary.setText("%s: %s" % (item.name, status_text))

    def _mark_item_complete(self, item_id, outputs):
        if item_id not in self.file_items:
            return
        item = self.file_items[item_id]
        item.outputs = list(outputs)
        item.status = "Done"
        item.detail = ", ".join(outputs)
        self._refresh_queue_row(item_id)

    def _mark_item_error(self, item_id, error_message):
        if item_id not in self.file_items:
            return
        item = self.file_items[item_id]
        item.status = "Error"
        item.detail = error_message
        self._refresh_queue_row(item_id)
        self.progress_summary.setText("%s failed: %s" % (item.name, error_message))

    def _validate_before_generate(self):
        if not self.file_items:
            QtWidgets.QMessageBox.warning(self, "No Files", "Add at least one file before converting.")
            return False

        try:
            resolve_frequency(self.frequency_edit.text())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid Frequency", str(exc))
            return False

        can_items = [item for item in self.file_items.values() if item.detected_type == "CAN"]
        if can_items and not self.dbc_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "DBC Required", "A DBC file is required when CAN logs are in the queue.")
            return False

        try:
            load_channel_unit_chart(self.unit_chart_edit.text().strip())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid Channel Chart", str(exc))
            return False

        self._apply_metadata_to_selected()
        return True

    def _set_running_state(self, running):
        self.generation_running = running
        enabled = not running
        self.add_button.setEnabled(enabled)
        self.convert_button.setEnabled(enabled)
        self.undo_button.setEnabled(enabled and bool(self.undo_stack))
        self.frequency_edit.setEnabled(enabled)
        self.output_edit.setEnabled(enabled)
        self.dbc_edit.setEnabled(enabled)
        self.unit_chart_edit.setEnabled(enabled)
        self.unit_chart_button.setEnabled(enabled)
        self.metadata_apply_button.setEnabled(enabled and bool(self._selected_items()))
        if running:
            self._set_editor_enabled(False, single_preview=False)
        else:
            selected_items = self._selected_items()
            self._set_editor_enabled(bool(selected_items), single_preview=(len(selected_items) == 1))

    def _generate(self):
        if self.generation_running or not self._validate_before_generate():
            return

        items = list(self.file_items.values())
        self._set_running_state(True)
        self.progress_bar.setValue(0)
        self.progress_status.setText("Preparing queue...")
        self.progress_summary.setText("Building the conversion queue.")

        task = ConvertQueueTask(
            items,
            self.output_edit.text().strip(),
            self.frequency_edit.text(),
            self.dbc_edit.text().strip(),
            self.unit_chart_edit.text().strip(),
        )
        task.signals.convert_item_status.connect(self._update_item_status)
        task.signals.convert_item_complete.connect(self._mark_item_complete)
        task.signals.convert_item_error.connect(self._mark_item_error)
        task.signals.convert_progress.connect(self._update_generation_progress)
        task.signals.convert_finished.connect(self._finish_generation)
        task.signals.convert_fatal_error.connect(self._finish_generation_with_error)
        self.convert_pool.start(task)

    def _update_generation_progress(self, completed, total):
        progress = int((completed / max(1, total)) * 100)
        self.progress_bar.setValue(progress)
        self.progress_status.setText("Converted %d of %d file(s)" % (completed, total))

    def _finish_generation_with_error(self, error_message):
        self._set_running_state(False)
        self.progress_status.setText("Generation stopped")
        self.progress_summary.setText(error_message)
        QtWidgets.QMessageBox.warning(self, "Generation Error", error_message)

    def _finish_generation(self):
        self._set_running_state(False)
        done_count = sum(1 for item in self.file_items.values() if item.status == "Done")
        error_count = sum(1 for item in self.file_items.values() if item.status == "Error")
        self.progress_bar.setValue(100)
        self.progress_status.setText("Finished")
        self.progress_summary.setText("Completed %d file(s) with %d error(s)." % (done_count, error_count))

    def closeEvent(self, event):
        self.closed = True
        self.preview_pool.clear()
        self.convert_pool.clear()
        self.preview_pool.waitForDone(50)
        self.convert_pool.waitForDone(50)
        super().closeEvent(event)


def create_application():
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return app


def build_argument_parser():
    parser = argparse.ArgumentParser(description="PySide6-based MoTeC log converter UI.")
    parser.add_argument("files", nargs="*", help="Optional log files to preload into the UI.")
    parser.add_argument(
        "--quit-after-ms",
        type=int,
        default=0,
        help="Automatically quit after the given number of milliseconds. Useful for smoke tests.",
    )
    return parser


def main(argv=None):
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    app = create_application()
    window = MotecQtWindow()
    window.show()

    for file_path in args.files:
        window._add_input_path(file_path)

    if args.quit_after_ms > 0:
        QtCore.QTimer.singleShot(args.quit_after_ms, app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
