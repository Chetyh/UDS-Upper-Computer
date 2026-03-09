"""
Test script: connect TOSUN device using raw DLL calls (no GUI framework).
Replicates the exact same logic as uds_gui.py's scan + connect flow.
"""
import time
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


def main():
    # Step 1: initialize (same as __init__.py does on import)
    print("[RAW] initialize_lib_tscan(True, True, False)")
    initialize_lib_tscan(True, True, False)

    # Step 2: scan devices
    dev_count = c_int32(0)
    ret = tscan_scan_devices(byref(dev_count))
    print(f"[RAW] tscan_scan_devices ret={ret}, dev_count={dev_count.value}")

    if dev_count.value == 0:
        print("[RAW] No devices found, exiting.")
        return

    # Step 3: get device info for each device
    devices = []
    for i in range(dev_count.value):
        man = c_char_p()
        prod = c_char_p()
        serial = c_char_p()
        ret = tscan_get_device_info(i, man, prod, serial)
        man_s = man.value.decode("utf8") if man.value else ""
        prod_s = prod.value.decode("utf8") if prod.value else ""
        serial_s = serial.value.decode("utf8") if serial.value else ""
        print(f"[RAW] Device {i}: man={man_s}, prod={prod_s}, serial={serial_s}, ret={ret}")
        if serial_s:
            devices.append(serial_s)

    if not devices:
        print("[RAW] No device with serial found, exiting.")
        return

    serial_str = devices[0]
    print(f"[RAW] Using first device serial: {serial_str}")

    # Step 4: connect (same as uds_gui.py on_device_changed -> tsapp_connect)
    handle = c_size_t(0)
    ret = tsapp_connect(serial_str.encode("utf8"), byref(handle))
    print(f"[RAW] tsapp_connect ret={ret}, handle={handle.value}")

    if ret not in (0, 5):
        print(f"[RAW] CONNECT FAILED with error code {ret}")
        return

    # Step 5: get CAN channel count
    chan_count = c_int32(0)
    ret = tscan_get_can_channel_count(handle, byref(chan_count))
    print(f"[RAW] tscan_get_can_channel_count ret={ret}, count={chan_count.value}")

    # Step 6: configure channel 0 as CAN 500k (same as uds_gui connect_device)
    ret = tsapp_configure_baudrate_can(handle, 0, 500, True)
    print(f"[RAW] tsapp_configure_baudrate_can ret={ret}")

    print("[RAW] Connection successful! Keeping alive for 3 seconds...")
    time.sleep(3)

    # Step 7: disconnect
    ret = tsapp_disconnect_by_handle(handle)
    print(f"[RAW] tsapp_disconnect_by_handle ret={ret}")
    print("[RAW] Done.")


if __name__ == "__main__":
    main()
