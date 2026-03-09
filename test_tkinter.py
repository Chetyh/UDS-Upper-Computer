"""
Test script: connect TOSUN device using tkinter GUI.
Replicates the exact same logic as uds_gui.py's scan + connect flow.
"""
import tkinter as tk
from tkinter import ttk, messagebox
from ctypes import c_int32, c_char_p, c_size_t, byref

from libTSCANAPI.TSCommon import (
    initialize_lib_tscan,
    tscan_scan_devices,
    tscan_get_device_info,
    tsapp_connect,
    tsapp_disconnect_by_handle,
    tscan_get_can_channel_count,
    tsapp_configure_baudrate_can,
)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("TOSUN Test (tkinter)")
        root.geometry("600x400")

        self.handle = c_size_t(0)
        self.connected = False
        self.serial_map: dict[int, str] = {}

        # Device row
        tk.Label(root, text="Device:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(root, textvariable=self.device_var, width=40, state="readonly")
        self.device_combo.grid(row=0, column=1, padx=5, pady=5)
        self.device_combo.bind("<<ComboboxSelected>>", self.on_device_selected)

        scan_btn = tk.Button(root, text="Scan", command=self.scan_devices)
        scan_btn.grid(row=0, column=2, padx=5, pady=5)

        # Channel row
        tk.Label(root, text="Channel:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.channel_var = tk.StringVar()
        self.channel_combo = ttk.Combobox(root, textvariable=self.channel_var, width=10, state="readonly")
        self.channel_combo.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        # Connect button
        self.connect_btn = tk.Button(root, text="Connect", command=self.connect_device)
        self.connect_btn.grid(row=2, column=1, padx=5, pady=10)

        # Log
        self.log = tk.Text(root, height=15, width=70)
        self.log.grid(row=3, column=0, columnspan=3, padx=5, pady=5)

        self.log_msg("[TKINTER] App started. Click Scan to find devices.")

    def log_msg(self, msg: str):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        print(msg)

    def scan_devices(self):
        dev_count = c_int32(0)
        ret = tscan_scan_devices(byref(dev_count))
        self.log_msg(f"[TKINTER] tscan_scan_devices ret={ret}, dev_count={dev_count.value}")

        self.serial_map.clear()
        items = []
        for i in range(dev_count.value):
            man = c_char_p()
            prod = c_char_p()
            serial = c_char_p()
            ret = tscan_get_device_info(i, man, prod, serial)
            man_s = man.value.decode("utf8") if man.value else ""
            prod_s = prod.value.decode("utf8") if prod.value else ""
            serial_s = serial.value.decode("utf8") if serial.value else ""
            self.log_msg(f"[TKINTER] Device {i}: prod={prod_s}, serial={serial_s}")
            if serial_s:
                display = f"{prod_s or man_s} [{serial_s}]"
                self.serial_map[len(items)] = serial_s
                items.append(display)

        self.device_combo["values"] = items
        if items:
            self.device_combo.current(0)
            self.on_device_selected(None)

    def on_device_selected(self, _event):
        idx = self.device_combo.current()
        if idx < 0 or idx not in self.serial_map:
            return
        serial_str = self.serial_map[idx]
        self.log_msg(f"[TKINTER] Selected device serial: {serial_str}")

        # Probe channel count
        handle = c_size_t(0)
        ret = tsapp_connect(serial_str.encode("utf8"), byref(handle))
        self.log_msg(f"[TKINTER] (probe) tsapp_connect ret={ret}, handle={handle.value}")

        if ret not in (0, 5):
            self.log_msg(f"[TKINTER] (probe) Connect FAILED, error code {ret}")
            self.channel_combo["values"] = []
            return

        chan_count = c_int32(0)
        ret2 = tscan_get_can_channel_count(handle, byref(chan_count))
        self.log_msg(f"[TKINTER] tscan_get_can_channel_count ret={ret2}, count={chan_count.value}")

        tsapp_disconnect_by_handle(handle)

        n = max(1, chan_count.value)
        self.channel_combo["values"] = [str(ch) for ch in range(1, n + 1)]
        self.channel_combo.current(0)

    def connect_device(self):
        if self.connected:
            tsapp_disconnect_by_handle(self.handle)
            self.connected = False
            self.connect_btn.config(text="Connect")
            self.log_msg("[TKINTER] Disconnected.")
            return

        idx = self.device_combo.current()
        if idx < 0 or idx not in self.serial_map:
            messagebox.showwarning("No device", "Please scan and select a device first.")
            return

        serial_str = self.serial_map[idx]

        if not self.channel_combo.get():
            messagebox.showwarning("No channel", "No channel available.")
            return

        channel_display = int(self.channel_combo.get())
        channel = max(0, channel_display - 1)

        self.handle = c_size_t(0)
        ret = tsapp_connect(serial_str.encode("utf8"), byref(self.handle))
        self.log_msg(f"[TKINTER] tsapp_connect ret={ret}, handle={self.handle.value}")

        if ret not in (0, 5):
            self.log_msg(f"[TKINTER] CONNECT FAILED with error code {ret}")
            messagebox.showerror("Connect failed", f"tsapp_connect returned {ret}")
            return

        ret = tsapp_configure_baudrate_can(self.handle, channel, 500, True)
        self.log_msg(f"[TKINTER] tsapp_configure_baudrate_can ret={ret}")

        self.connected = True
        self.connect_btn.config(text="Disconnect")
        self.log_msg("[TKINTER] Connected successfully!")


def main():
    initialize_lib_tscan(True, True, False)
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
