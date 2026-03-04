import sys
from dataclasses import dataclass
from typing import List, Optional, Union, Tuple

from ctypes import c_int32, c_char_p, c_size_t, byref

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from libTSCANAPI import TSMasterDevice, TSUDS
from libTSCANAPI.TSCommon import (
    tscan_scan_devices,
    tscan_get_device_info,
    tsapp_connect,
    tsapp_disconnect_by_handle,
    tscan_get_can_channel_count,
)

try:
    import openpyxl  # type: ignore
except ImportError:  # pragma: no cover - runtime-only guard
    openpyxl = None


@dataclass
class UDSCommand:
    index: int
    can_id: int
    service_id: Union[int, str]
    sub_service_id: int
    data_bytes: List[int]
    need_response: bool
    expected_response: Optional[List[int]]
    wait_ms: int


class UDSMainWindow(QMainWindow):
    """
    Simple generic UDS runner based on:

    - libTSCANAPI.TSMasterDevice for CAN/CAN FD
    - libTSCANAPI.TSUDS for ISO-TP / UDS framing

    Excel format (per row):
        0: index
        1: RX ID (hex like 0x7E8 or decimal)
        2: TX ID (hex or decimal)
        3: SID (hex or decimal)
        4: data bytes (space / comma separated, hex or decimal)
        5: need response (1/0, yes/no/true/false)
        6: expected response bytes (optional, same format as data)
        7: wait time in ms (int)
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Generic UDS Tool (TOSUN + Qt)")
        self.resize(1000, 600)

        self.device: Optional[TSMasterDevice] = None
        self.uds: Optional[TSUDS] = None
        self.ecu_id: Optional[int] = None  # ECU ID from Excel header row
        self.commands: List[UDSCommand] = []
        self.current_index: int = 0
        self.running: bool = False
        self.selected_serial: Optional[str] = None

        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # Top toolbar area
        top_layout = QHBoxLayout()
        main_layout.addLayout(top_layout)

        # Device list (auto-scanned) and channel list
        self.device_combo = QComboBox()
        self.device_combo.setMinimumContentsLength(30)
        self.device_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        top_layout.addWidget(QLabel("Device:"))
        top_layout.addWidget(self.device_combo, stretch=2)

        self.channel_combo = QComboBox()
        top_layout.addWidget(QLabel("Channel:"))
        top_layout.addWidget(self.channel_combo, stretch=0)

        # CAN / CAN FD selection
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["CAN", "CAN FD"])
        top_layout.addWidget(QLabel("Bus type:"))
        top_layout.addWidget(self.mode_combo)

        # Timing parameters
        timing_layout = QGridLayout()
        main_layout.addLayout(timing_layout)

        self.p2_spin = QSpinBox()
        self.p2_spin.setRange(1, 10000)
        self.p2_spin.setValue(50)  # default P2 = 50 ms
        timing_layout.addWidget(QLabel("P2 (ms):"), 0, 0)
        timing_layout.addWidget(self.p2_spin, 0, 1)

        self.p2_ext_spin = QSpinBox()
        self.p2_ext_spin.setRange(1, 60000)
        self.p2_ext_spin.setValue(5000)
        timing_layout.addWidget(QLabel("P2* (ms):"), 0, 2)
        timing_layout.addWidget(self.p2_ext_spin, 0, 3)

        self.s3_server_spin = QSpinBox()
        self.s3_server_spin.setRange(1, 60000)
        self.s3_server_spin.setValue(5000)
        timing_layout.addWidget(QLabel("S3 server (ms):"), 1, 0)
        timing_layout.addWidget(self.s3_server_spin, 1, 1)

        self.s3_client_spin = QSpinBox()
        self.s3_client_spin.setRange(1, 60000)
        self.s3_client_spin.setValue(2000)
        timing_layout.addWidget(QLabel("S3 client (ms):"), 1, 2)
        timing_layout.addWidget(self.s3_client_spin, 1, 3)

        # Flash driver / application file selectors
        file_layout = QGridLayout()
        main_layout.addLayout(file_layout)

        self.flash_driver_path: Optional[str] = None
        self.app_path: Optional[str] = None

        self.flash_driver_label = QLabel("Flash driver: (none)")
        self.flash_driver_btn = QPushButton("Browse FlashDriver (.hex)")
        file_layout.addWidget(self.flash_driver_label, 0, 0, 1, 2)
        file_layout.addWidget(self.flash_driver_btn, 0, 2)

        self.app_label = QLabel("Application: (none)")
        self.app_btn = QPushButton("Browse App (.s19)")
        file_layout.addWidget(self.app_label, 1, 0, 1, 2)
        file_layout.addWidget(self.app_btn, 1, 2)

        # Command table
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            [
                "Index",
                "CAN ID",
                "Service ID",
                "Sub ID",
                "Data",
                "Need Resp",
                "Expected Resp",
                "Wait (ms)",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        main_layout.addWidget(self.table, stretch=1)

        # Bottom buttons
        bottom_layout = QHBoxLayout()
        main_layout.addLayout(bottom_layout)

        self.load_button = QPushButton("Load Excel")
        self.connect_button = QPushButton("Connect")
        self.run_button = QPushButton("Run All")
        self.run_button.setEnabled(False)

        bottom_layout.addWidget(self.load_button)
        bottom_layout.addWidget(self.connect_button)
        bottom_layout.addWidget(self.run_button)
        bottom_layout.addStretch()

        # Log view
        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(
            ["Index", "Request", "Response", "Status"]
        )
        self.log_table.horizontalHeader().setStretchLastSection(True)
        main_layout.addWidget(self.log_table, stretch=1)

        # Actions
        self.load_button.clicked.connect(self.load_excel)
        self.connect_button.clicked.connect(self.connect_device)
        self.run_button.clicked.connect(self.toggle_run)
        self.flash_driver_btn.clicked.connect(self.choose_flash_driver)
        self.app_btn.clicked.connect(self.choose_app)
        self.device_combo.currentIndexChanged.connect(self.on_device_changed)

        # Menu shortcuts
        file_menu = self.menuBar().addMenu("&File")
        act_open = QAction("Open Excel...", self)
        act_open.triggered.connect(self.load_excel)
        file_menu.addAction(act_open)

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # Timer to sequence commands without blocking UI
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.process_next_command)

        # Timer to monitor ECU messages (any CAN frame with ECU ID)
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self.poll_ecu_messages)

        # Timer to scan devices periodically
        self.device_scan_timer = QTimer(self)
        self.device_scan_timer.timeout.connect(self.scan_devices)
        self.device_scan_timer.start(1000)

    # ---------- Excel parsing ----------

    def load_excel(self) -> None:
        if openpyxl is None:
            QMessageBox.critical(
                self,
                "Missing dependency",
                "Python package 'openpyxl' is required.\n\n"
                "Please install it in your environment:\n\n"
                "    pip install openpyxl",
            )
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Excel File",
            "",
            "Excel Files (*.xlsx *.xlsm *.xltx *.xltm);;All Files (*)",
        )
        if not file_path:
            return

        try:
            wb = openpyxl.load_workbook(file_path)
            sheet = wb.active
        except Exception as exc:  # pragma: no cover - UI path
            QMessageBox.critical(self, "Error", f"Failed to open Excel: {exc}")
            return

        self.commands.clear()
        self.table.setRowCount(0)
        self.ecu_id = None

        row_iter = list(sheet.iter_rows(values_only=True))

        header_skipped = False

        for row in row_iter:
            if all(cell is None for cell in row):
                continue

            # ECU ID header row, e.g. ["ECU_ID", "0x7E8", ...]
            first_cell = row[0]
            if (
                self.ecu_id is None
                and isinstance(first_cell, str)
                and first_cell.strip().lower().replace(" ", "_") in ("ecu_id", "ecu")
            ):
                if len(row) < 2 or row[1] is None:
                    QMessageBox.warning(self, "Skip row", "ECU_ID row missing value.")
                    continue
                try:
                    self.ecu_id = self._parse_int(row[1])
                except ValueError as exc:
                    QMessageBox.warning(self, "Skip row", f"Invalid ECU_ID value: {exc}")
                continue

            # Optional textual header row before commands
            if not header_skipped and first_cell is not None and not self._is_int_like(first_cell):
                header_skipped = True
                continue

            try:
                cmd = self._parse_row(row)
            except ValueError as exc:
                QMessageBox.warning(self, "Skip row", f"Skip row due to error: {exc}")
                continue
            self.commands.append(cmd)
            self._append_command_to_table(cmd)

        if self.commands:
            self.run_button.setEnabled(self.device is not None)

    @staticmethod
    def _is_int_like(value) -> bool:
        try:
            int(str(value).strip(), 0)
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_int(value) -> int:
        return int(str(value).strip(), 0)

    @staticmethod
    def _parse_service_field(value) -> Union[int, str]:
        """
        Service field can be:
        - numeric (SID)
        - special keywords: 'uploadflashdriver', 'uploadapp'
        """
        if value is None:
            raise ValueError("ServiceID is empty")
        text = str(value).strip()
        lower = text.lower()
        if lower in ("uploadflashdriver", "upload_flash_driver", "uploadflash", "flashdriver"):
            return "uploadflashdriver"
        if lower in ("uploadapp", "upload_application", "upload_sw", "uploadsoftware"):
            return "uploadapp"
        # otherwise treat as numeric SID
        return int(text, 0)

    @staticmethod
    def _parse_byte_list(value) -> List[int]:
        if value is None:
            return []
        text = str(value).strip()
        if not text:
            return []
        # allow separators: space, comma, semicolon
        for sep in [",", ";"]:
            text = text.replace(sep, " ")
        parts = [p for p in text.split() if p]
        return [int(p, 0) & 0xFF for p in parts]

    @staticmethod
    def _parse_bool(value) -> bool:
        if value is None:
            return False
        text = str(value).strip().lower()
        return text in ("1", "y", "yes", "true", "t", "需要", "是")

    def _parse_row(self, row) -> UDSCommand:
        # New row format:
        # 0: index
        # 1: CAN ID (request ID)
        # 2: Service ID (SID or special keyword)
        # 3: Sub-service ID
        # 4: data bytes
        # 5: need response
        # 6: expected response bytes
        # 7: wait time (ms)
        if len(row) < 7:
            raise ValueError("row must have at least 7 columns")

        index = int(row[0]) if row[0] is not None else len(self.commands) + 1
        can_id = self._parse_int(row[1])
        service_id = self._parse_service_field(row[2])
        sub_service_id = self._parse_int(row[3])
        data_bytes = self._parse_byte_list(row[4])
        need_response = self._parse_bool(row[5])
        expected_response = (
            self._parse_byte_list(row[6]) if len(row) > 6 and row[6] is not None else None
        )
        wait_ms = int(row[7]) if len(row) > 7 and row[7] is not None else 0

        return UDSCommand(
            index=index,
            can_id=can_id,
            service_id=service_id,
            sub_service_id=sub_service_id,
            data_bytes=data_bytes,
            need_response=need_response,
            expected_response=expected_response,
            wait_ms=wait_ms,
        )

    def _append_command_to_table(self, cmd: UDSCommand) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        def set_item(col: int, text: str) -> None:
            item = QTableWidgetItem(text)
            if col == 0:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, col, item)

        set_item(0, str(cmd.index))
        set_item(1, hex(cmd.can_id))
        if isinstance(cmd.service_id, int):
            set_item(2, hex(cmd.service_id))
        else:
            set_item(2, str(cmd.service_id))
        set_item(3, hex(cmd.sub_service_id))
        set_item(4, " ".join(f"{b:02X}" for b in cmd.data_bytes))
        set_item(5, "Yes" if cmd.need_response else "No")
        set_item(
            6,
            ""
            if not cmd.expected_response
            else " ".join(f"{b:02X}" for b in cmd.expected_response),
        )
        set_item(7, str(cmd.wait_ms))

    # ---------- Device / UDS handling ----------

    def connect_device(self) -> None:
        if self.device is not None:
            # simple toggle: disconnect and clean up
            self.device.shut_down()
            self.device = None
            self.uds = None
            self.monitor_timer.stop()
            self.connect_button.setText("Connect")
            self.run_button.setEnabled(False)
            return

        # Ensure a device is selected
        idx = self.device_combo.currentIndex()
        if idx < 0:
            QMessageBox.warning(self, "No device", "Please select a device first.")
            return
        serial_str = self.device_combo.itemData(idx)
        if not serial_str:
            QMessageBox.warning(self, "No device", "Selected device has no serial.")
            return

        if self.channel_combo.count() == 0:
            QMessageBox.warning(self, "No channel", "No channel available for this device.")
            return

        # Channel shown to user is 1-based; API expects 0-based
        channel_display = int(self.channel_combo.currentText())
        channel = max(0, channel_display - 1)
        is_fd = self.mode_combo.currentText() == "CAN FD"

        configs = [
            {
                "FChannel": channel,
                "rate_baudrate": 500,
                "data_baudrate": 2000,
                "enable_120hm": True,
                "is_fd": is_fd,
            }
        ]

        try:
            self.device = TSMasterDevice(
                configs=configs,
                hwserial=serial_str.encode("utf8"),
                is_include_tx=True,
            )
        except Exception as exc:  # pragma: no cover - UI path
            QMessageBox.critical(self, "Connect failed", f"Failed to connect device:\n{exc}")
            self.device = None
            return

        # TSUDS uses CAN FD frame; we keep dlc=8 by default as requested.
        timeout_sec = max(self.p2_spin.value(), self.p2_ext_spin.value()) / 1000.0
        respond_id = self.ecu_id if self.ecu_id is not None else 0x708
        self.uds = TSUDS(
            HwHandle=self.device.HwHandle,
            channel=channel,
            dlc=8,
            request_id=0x700,  # default, will override per command when sending
            respond_id=respond_id,
            is_fd=is_fd,
            is_std=True,
            timeout=timeout_sec,
        )

        self.connect_button.setText("Disconnect")
        self.run_button.setEnabled(bool(self.commands))
        # Start ECU monitor if we know ECU ID
        if self.ecu_id is not None:
            self.monitor_timer.start(10)

    def toggle_run(self) -> None:
        if not self.device or not self.uds or not self.commands:
            QMessageBox.warning(self, "Not ready", "Please connect device and load Excel first.")
            return
        if self.running:
            self.running = False
            self.timer.stop()
            self.run_button.setText("Run All")
        else:
            self.running = True
            self.current_index = 0
            self.log_table.setRowCount(0)
            self.run_button.setText("Stop")
            self.timer.start(0)

    def process_next_command(self) -> None:
        if not self.running or not self.uds:
            self.timer.stop()
            return

        if self.current_index >= len(self.commands):
            self.running = False
            self.timer.stop()
            self.run_button.setText("Run All")
            return

        cmd = self.commands[self.current_index]
        self.current_index += 1
        status_text = "OK"
        response_bytes: Optional[List[int]] = None

        # Update TSUDS IDs for this command
        self.uds.request_id = cmd.can_id
        if self.ecu_id is not None:
            self.uds.respond_id = self.ecu_id

        try:
            # Special upload commands
            if isinstance(cmd.service_id, str):
                if cmd.service_id == "uploadflashdriver":
                    status_text = self.handle_upload_flash_driver()
                elif cmd.service_id == "uploadapp":
                    status_text = self.handle_upload_app()
                else:
                    status_text = f"Unknown service keyword: {cmd.service_id}"
            else:
                # Normal UDS request: [SID] + [SubId] + data
                request_bytes = [
                    cmd.service_id & 0xFF,
                    cmd.sub_service_id & 0xFF,
                ] + [b & 0xFF for b in cmd.data_bytes]
                self.uds.tstp_can_send_request(request_bytes)
                if cmd.need_response:
                    ret, data = self.uds.receive_can_Response()
                    if ret == 0:
                        response_bytes = data
                        if cmd.expected_response:
                            if data[: len(cmd.expected_response)] == cmd.expected_response:
                                status_text = "Match"
                            else:
                                status_text = "Mismatch"
                        else:
                            status_text = "Received"
                    else:
                        status_text = f"Error {ret}"
        except Exception as exc:  # pragma: no cover - UI path
            status_text = f"Exception: {exc}"

        self._append_log(cmd, request_bytes, response_bytes, status_text)

        # Wait time before next command
        delay = max(0, cmd.wait_ms)
        if self.running:
            self.timer.start(delay)

    def _append_log(
        self,
        cmd: UDSCommand,
        request: List[int],
        response: Optional[List[int]],
        status: str,
    ) -> None:
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)

        def set_item(col: int, text: str) -> None:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.log_table.setItem(row, col, item)

        set_item(0, str(cmd.index))
        set_item(1, " ".join(f"{b:02X}" for b in request))
        set_item(2, "" if response is None else " ".join(f"{b:02X}" for b in response))
        set_item(3, status)

    def poll_ecu_messages(self) -> None:
        """Poll raw CAN/CAN FD messages and log any with ECU ID."""
        if not self.device or self.ecu_id is None:
            return
        try:
            channel = int(self.channel_combo.currentText())
        except Exception:
            channel = 0

        # Non-blocking single poll
        msg = self.device.recv(channel=channel, timeout=0.0)
        if msg is None:
            return

        arb_id = getattr(msg, "arbitration_id", None)
        data = getattr(msg, "data", b"")
        if arb_id != self.ecu_id:
            return

        # Log ECU message as a special row (index "-")
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)

        def set_item(col: int, text: str) -> None:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.log_table.setItem(row, col, item)

        set_item(0, "-")
        set_item(1, "")
        set_item(2, " ".join(f"{b:02X}" for b in bytes(data)))
        set_item(3, "ECU MSG")

    # ---------- Device scan / channel list ----------

    def scan_devices(self) -> None:
        """
        Periodically scan devices and update the device combo.
        Once a device is selected, the list will not change unless that
        device disappears.
        """
        dev_count = c_int32(0)
        try:
            tscan_scan_devices(byref(dev_count))
            print(f"[UDS GUI] scan_devices: dev_count = {dev_count.value}")
        except Exception as e:
            print(f"[UDS GUI] scan_devices exception: {e}")
            return

        found = {}
        for i in range(dev_count.value):
            man = c_char_p()
            prod = c_char_p()
            serial = c_char_p()
            try:
                tscan_get_device_info(i, man, prod, serial)
            except Exception:
                continue
            man_s = man.value.decode("utf8") if man.value else ""
            prod_s = prod.value.decode("utf8") if prod.value else ""
            serial_s = serial.value.decode("utf8") if serial.value else ""
            if not serial_s:
                continue
            display = f"{prod_s or man_s} [{serial_s}]"
            found[serial_s] = display

        # If we have a selected device and it still exists, keep list stable
        if self.selected_serial and self.selected_serial in found:
            return

        # Otherwise rebuild list from current devices
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for serial_s, display in found.items():
            self.device_combo.addItem(display, serial_s)
        self.device_combo.blockSignals(False)

        # Reset selection state
        if self.device_combo.count() > 0:
            self.device_combo.setCurrentIndex(0)
            data = self.device_combo.itemData(0)
            self.selected_serial = str(data) if data else None
            # When there is only one device, combo may auto-select without
            # emitting currentIndexChanged. Trigger channel query explicitly.
            self.on_device_changed(self.device_combo.currentIndex())
        else:
            self.selected_serial = None
            self.channel_combo.clear()

    def on_device_changed(self, index: int) -> None:
        """When user selects a device, remember it and query channel count."""
        if index < 0:
            self.selected_serial = None
            self.channel_combo.clear()
            return

        data = self.device_combo.itemData(index)
        if not data:
            self.selected_serial = None
            self.channel_combo.clear()
            return

        serial_str = str(data)
        self.selected_serial = serial_str

        # Probe channel count by connecting briefly
        handle = c_size_t(0)
        try:
            ret = tsapp_connect(serial_str.encode("utf8"), byref(handle))
            print(f"[UDS GUI] tsapp_connect ret={ret}, handle={handle.value}")
        except Exception as e:
            print(f"[UDS GUI] tsapp_connect exception: {e}")
            return
        if ret not in (0, 5):
            QMessageBox.warning(self, "Connect failed", f"tsapp_connect returned {ret}")
            return

        chan_count = c_int32(0)
        try:
            tscan_get_can_channel_count(handle, byref(chan_count))
            print(f"[UDS GUI] tscan_get_can_channel_count: {chan_count.value}")
        except Exception as e:
            print(f"[UDS GUI] tscan_get_can_channel_count exception: {e}")
            chan_count.value = 0

        try:
            tsapp_disconnect_by_handle(handle)
        except Exception:
            pass

        # Populate channel combo with 1-based indices
        self.channel_combo.clear()
        n = chan_count.value if chan_count.value > 0 else 1
        for ch in range(1, n + 1):
            self.channel_combo.addItem(str(ch))

    # ---------- File selection ----------

    def choose_flash_driver(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Flash Driver (.hex)",
            "",
            "HEX Files (*.hex);;All Files (*)",
        )
        if not file_path:
            return
        self.flash_driver_path = file_path
        self.flash_driver_label.setText(f"Flash driver: {file_path}")

    def choose_app(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Application (.s19)",
            "",
            "S19 Files (*.s19);;All Files (*)",
        )
        if not file_path:
            return
        self.app_path = file_path
        self.app_label.setText(f"Application: {file_path}")

    # ---------- Flash functions (34/36/37) ----------

    @staticmethod
    def _build_contiguous_image(segments: List[Tuple[int, bytes]]) -> Tuple[int, bytes]:
        """
        Build a contiguous image from (address, data) segments.
        Gaps are filled with 0xFF.
        Returns (start_address, image_bytes).
        """
        if not segments:
            raise ValueError("No data segments found")
        start = min(addr for addr, _ in segments)
        end = max(addr + len(data) for addr, data in segments)
        size = end - start
        image = bytearray([0xFF] * size)
        for addr, data in segments:
            offset = addr - start
            image[offset : offset + len(data)] = data
        return start, bytes(image)

    @staticmethod
    def _parse_intel_hex(path: str) -> List[Tuple[int, bytes]]:
        segments: List[Tuple[int, bytes]] = []
        base = 0
        current_addr = None
        current_data = bytearray()

        def flush():
            nonlocal current_addr, current_data
            if current_addr is not None and current_data:
                segments.append((current_addr, bytes(current_data)))
            current_addr = None
            current_data = bytearray()

        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith(":"):
                    continue
                length = int(line[1:3], 16)
                addr = int(line[3:7], 16)
                rectype = int(line[7:9], 16)
                data_str = line[9 : 9 + 2 * length]
                # checksum = line[9 + 2 * length : 11 + 2 * length]
                if rectype == 0:  # data
                    full_addr = base + addr
                    if current_addr is None:
                        current_addr = full_addr
                        current_data.extend(bytes.fromhex(data_str))
                    else:
                        expected = current_addr + len(current_data)
                        if full_addr != expected:
                            flush()
                            current_addr = full_addr
                            current_data.extend(bytes.fromhex(data_str))
                        else:
                            current_data.extend(bytes.fromhex(data_str))
                elif rectype == 4:  # extended linear address
                    flush()
                    base = int(data_str, 16) << 16
                elif rectype == 1:  # EOF
                    break
        flush()
        return segments

    @staticmethod
    def _parse_s19(path: str) -> List[Tuple[int, bytes]]:
        segments: List[Tuple[int, bytes]] = []
        current_addr = None
        current_data = bytearray()

        def flush():
            nonlocal current_addr, current_data
            if current_addr is not None and current_data:
                segments.append((current_addr, bytes(current_data)))
            current_addr = None
            current_data = bytearray()

        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("S"):
                    continue
                rectype = line[1]
                if rectype not in ("1", "2", "3"):
                    continue
                count = int(line[2:4], 16)
                if rectype == "1":
                    addr_len = 2
                elif rectype == "2":
                    addr_len = 3
                else:
                    addr_len = 4
                addr_start = 4
                addr_end = addr_start + addr_len * 2
                addr = int(line[addr_start:addr_end], 16)
                data_start = addr_end
                data_end = 4 + count * 2 - 2  # exclude checksum
                data_str = line[data_start:data_end]
                data = bytes.fromhex(data_str)

                if current_addr is None:
                    current_addr = addr
                    current_data.extend(data)
                else:
                    expected = current_addr + len(current_data)
                    if addr != expected:
                        flush()
                        current_addr = addr
                        current_data.extend(data)
                    else:
                        current_data.extend(data)
        flush()
        return segments

    def _uds_request_expect(self, payload: List[int], expect_sid: Optional[int] = None) -> str:
        """
        Send a UDS request (application payload bytes) and wait for positive response.
        Returns status text.
        """
        if not self.uds:
            return "UDS not ready"
        self.uds.tstp_can_send_request(payload)
        ret, data = self.uds.receive_can_Response()
        if ret != 0:
            return f"Error {ret}"
        if expect_sid is not None and data:
            if data[0] != (expect_sid + 0x40) & 0xFF:
                return f"Unexpected SID: 0x{data[0]:02X}"
        return "OK"

    def _perform_download(self, image: bytes, start_addr: int) -> str:
        """
        Perform RequestDownload (0x34), TransferData (0x36), RequestTransferExit (0x37)
        according to the constraints given.
        """
        if not self.uds:
            return "UDS not ready"

        total_len = len(image)
        # 0x34 payload: [SID, dataFormat=0x00, addrLenFmt=0x44, addr(4), size(4)]
        addr_bytes = start_addr.to_bytes(4, "big")
        size_bytes = total_len.to_bytes(4, "big")
        payload_34 = [0x34, 0x00, 0x44] + list(addr_bytes) + list(size_bytes)
        status = self._uds_request_expect(payload_34, expect_sid=0x34)
        if status != "OK":
            return f"0x34 failed: {status}"

        # 0x36 TransferData: block size 0x100 bytes per block
        block_size = 0x100
        seq = 1
        offset = 0
        while offset < total_len:
            chunk = image[offset : offset + block_size]
            payload_36 = [0x36, seq & 0xFF] + list(chunk)
            status = self._uds_request_expect(payload_36, expect_sid=0x36)
            if status != "OK":
                return f"0x36 failed at block {seq}: {status}"
            offset += len(chunk)
            seq = (seq + 1) & 0xFF

        # 0x37 RequestTransferExit without checksum
        payload_37 = [0x37]
        status = self._uds_request_expect(payload_37, expect_sid=0x37)
        if status != "OK":
            return f"0x37 failed: {status}"
        return "OK"

    def handle_upload_flash_driver(self) -> str:
        if not self.flash_driver_path:
            return "Flash driver file not selected"
        try:
            segments = self._parse_intel_hex(self.flash_driver_path)
            start, image = self._build_contiguous_image(segments)
        except Exception as exc:
            return f"Parse HEX failed: {exc}"
        return self._perform_download(image, start)

    def handle_upload_app(self) -> str:
        if not self.app_path:
            return "App file not selected"
        try:
            segments = self._parse_s19(self.app_path)
            start, image = self._build_contiguous_image(segments)
        except Exception as exc:
            return f"Parse S19 failed: {exc}"
        return self._perform_download(image, start)


def main() -> None:
    app = QApplication(sys.argv)
    win = UDSMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

