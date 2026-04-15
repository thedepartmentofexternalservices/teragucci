"""
Quality control panel widget.

Provides the sharpness ↔ temporal stability slider and advanced
codec/quality settings for the user to control the streaming quality.
"""

import logging
from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QComboBox, QSpinBox, QCheckBox, QGroupBox, QFormLayout,
    QDoubleSpinBox, QPushButton,
)

from common.messages import QualitySettings

logger = logging.getLogger(__name__)


class QualityControlPanel(QWidget):
    """
    Panel with quality controls:
    - Main slider: Sharpness ↔ Temporal Stability
    - Codec selector (H.264 / H.265 / JPEG fallback)
    - Chroma subsampling (YUV420 / YUV422 / YUV444)
    - Max FPS
    - Max bandwidth
    - Lossless mode toggle
    - Audio toggle and bitrate
    """

    settings_changed = Signal(object)  # QualitySettings
    mic_mute_toggled = Signal()         # user clicked mic toggle

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building = True
        self._settings = QualitySettings()
        self._setup_ui()
        self._building = False

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # === Main Quality Slider ===
        slider_group = QGroupBox("Quality Balance")
        slider_layout = QVBoxLayout(slider_group)

        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Smooth Motion"))
        label_row.addStretch()
        label_row.addWidget(QLabel("Sharp Image"))
        slider_layout.addLayout(label_row)

        self._quality_slider = QSlider(Qt.Horizontal)
        self._quality_slider.setRange(0, 100)
        self._quality_slider.setValue(50)
        self._quality_slider.setTickPosition(QSlider.TicksBelow)
        self._quality_slider.setTickInterval(25)
        self._quality_slider.setToolTip(
            "Left: Favor smooth motion & high FPS (lower image quality)\n"
            "Right: Favor sharp/crisp image (may reduce FPS)"
        )
        self._quality_slider.valueChanged.connect(self._on_quality_changed)
        slider_layout.addWidget(self._quality_slider)

        self._quality_label = QLabel("Balanced")
        self._quality_label.setAlignment(Qt.AlignCenter)
        self._quality_label.setStyleSheet("font-weight: bold; font-size: 12px;")
        slider_layout.addWidget(self._quality_label)

        layout.addWidget(slider_group)

        # === Video Settings ===
        video_group = QGroupBox("Video")
        video_layout = QFormLayout(video_group)

        self._codec_combo = QComboBox()
        self._codec_combo.addItems(["H.264", "H.265", "AV1", "JPEG (Fallback)"])
        self._codec_combo.currentIndexChanged.connect(self._on_setting_changed)
        video_layout.addRow("Codec:", self._codec_combo)

        self._chroma_combo = QComboBox()
        self._chroma_combo.addItems(["YUV 4:4:4 (Best Quality)", "YUV 4:2:2", "YUV 4:2:0 (Smallest)"])
        self._chroma_combo.currentIndexChanged.connect(self._on_setting_changed)
        video_layout.addRow("Color:", self._chroma_combo)

        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 120)
        self._fps_spin.setValue(60)
        self._fps_spin.setSuffix(" fps")
        self._fps_spin.valueChanged.connect(self._on_setting_changed)
        video_layout.addRow("Max FPS:", self._fps_spin)

        self._bandwidth_spin = QDoubleSpinBox()
        self._bandwidth_spin.setRange(1.0, 1000.0)
        self._bandwidth_spin.setValue(50.0)
        self._bandwidth_spin.setSuffix(" Mbps")
        self._bandwidth_spin.setDecimals(1)
        self._bandwidth_spin.valueChanged.connect(self._on_setting_changed)
        video_layout.addRow("Max Bandwidth:", self._bandwidth_spin)

        self._lossless_check = QCheckBox("Lossless Mode (4:4:4, high bandwidth)")
        self._lossless_check.stateChanged.connect(self._on_setting_changed)
        video_layout.addRow(self._lossless_check)

        layout.addWidget(video_group)

        # === Audio Settings ===
        audio_group = QGroupBox("Audio")
        audio_layout = QFormLayout(audio_group)

        self._audio_check = QCheckBox("Enable Audio Streaming")
        self._audio_check.setChecked(True)
        self._audio_check.stateChanged.connect(self._on_setting_changed)
        audio_layout.addRow(self._audio_check)

        self._audio_bitrate_spin = QSpinBox()
        self._audio_bitrate_spin.setRange(32, 320)
        self._audio_bitrate_spin.setValue(128)
        self._audio_bitrate_spin.setSuffix(" kbps")
        self._audio_bitrate_spin.valueChanged.connect(self._on_setting_changed)
        audio_layout.addRow("Audio Bitrate:", self._audio_bitrate_spin)

        layout.addWidget(audio_group)

        # === Microphone Settings ===
        mic_group = QGroupBox("Microphone")
        mic_layout = QFormLayout(mic_group)

        self._mic_check = QCheckBox("Send microphone to remote")
        self._mic_check.setChecked(True)
        self._mic_check.setToolTip(
            "When enabled, your microphone audio is streamed to the remote machine.\n"
            "Disable to mute yourself without affecting remote audio playback."
        )
        self._mic_check.stateChanged.connect(self._on_mic_check_changed)
        mic_layout.addRow(self._mic_check)

        self._mic_device_label = QLabel("No microphone detected")
        self._mic_device_label.setStyleSheet("color: #8b8ba3; font-size: 11px;")
        mic_layout.addRow("Device:", self._mic_device_label)

        layout.addWidget(mic_group)

        # === Preset Buttons ===
        presets_layout = QHBoxLayout()

        for name, bias, codec_idx, chroma_idx, fps, bw in [
            ("Low Bandwidth", 0.2, 0, 2, 30, 5.0),
            ("Balanced", 0.5, 0, 1, 60, 50.0),
            ("Best Quality", 0.9, 0, 0, 30, 100.0),
            ("Lossless", 1.0, 0, 0, 30, 200.0),
        ]:
            btn = QPushButton(name)
            btn.clicked.connect(partial(self._apply_preset, bias, codec_idx, chroma_idx, fps, bw,
                                        name == "Lossless"))
            presets_layout.addWidget(btn)

        layout.addLayout(presets_layout)
        layout.addStretch()

    def _on_mic_check_changed(self, state):
        if not self._building:
            self.mic_mute_toggled.emit()

    def update_mic_status(self, available: bool, muted: bool, device_name: str = ""):
        """Called by MainWindow to reflect the session's current mic state."""
        self._building = True
        self._mic_check.setChecked(not muted)
        self._mic_check.setEnabled(available)
        if not available:
            self._mic_device_label.setText("No microphone detected")
            self._mic_device_label.setStyleSheet("color: #8b8ba3; font-size: 11px;")
        else:
            self._mic_device_label.setText(device_name or "Default input device")
            color = "#00c878" if not muted else "#8b8ba3"
            self._mic_device_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._building = False

    def _on_quality_changed(self, value: int):
        bias = value / 100.0
        if bias < 0.25:
            label = "Smooth Motion"
        elif bias < 0.45:
            label = "Prefer Smooth"
        elif bias < 0.55:
            label = "Balanced"
        elif bias < 0.75:
            label = "Prefer Sharp"
        else:
            label = "Maximum Sharpness"
        self._quality_label.setText(label)
        self._emit_settings()

    def _on_setting_changed(self, *args):
        self._emit_settings()

    def _apply_preset(self, bias, codec_idx, chroma_idx, fps, bw, lossless):
        self._building = True
        self._quality_slider.setValue(int(bias * 100))
        self._codec_combo.setCurrentIndex(codec_idx)
        self._chroma_combo.setCurrentIndex(chroma_idx)
        self._fps_spin.setValue(fps)
        self._bandwidth_spin.setValue(bw)
        self._lossless_check.setChecked(lossless)
        self._building = False
        self._emit_settings()

    def _emit_settings(self):
        if self._building:
            return

        codec_map = {0: "h264", 1: "h265", 2: "av1", 3: "jpeg"}
        chroma_map = {0: "yuv444", 1: "yuv422", 2: "yuv420"}

        self._settings = QualitySettings(
            quality_bias=self._quality_slider.value() / 100.0,
            max_fps=self._fps_spin.value(),
            max_bandwidth_mbps=self._bandwidth_spin.value(),
            codec=codec_map.get(self._codec_combo.currentIndex(), "h264"),
            chroma=chroma_map.get(self._chroma_combo.currentIndex(), "yuv444"),
            force_lossless=self._lossless_check.isChecked(),
            enable_audio=self._audio_check.isChecked(),
            audio_bitrate_kbps=self._audio_bitrate_spin.value(),
        )
        self.settings_changed.emit(self._settings)

    @property
    def settings(self) -> QualitySettings:
        return self._settings

    def load_from_profile(self, profile):
        """Load settings from a ConnectionProfile."""
        self._building = True
        self._quality_slider.setValue(int(profile.quality_bias * 100))

        codec_reverse = {"h264": 0, "h265": 1, "av1": 2, "jpeg": 3}
        self._codec_combo.setCurrentIndex(codec_reverse.get(profile.preferred_codec, 0))

        chroma_reverse = {"yuv444": 0, "yuv422": 1, "yuv420": 2}
        self._chroma_combo.setCurrentIndex(chroma_reverse.get(profile.preferred_chroma, 0))

        self._fps_spin.setValue(profile.max_fps)
        self._bandwidth_spin.setValue(profile.max_bandwidth_mbps)
        self._audio_check.setChecked(profile.enable_audio)
        self._building = False
        self._emit_settings()
