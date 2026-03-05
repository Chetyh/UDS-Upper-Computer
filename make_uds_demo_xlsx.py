from pathlib import Path

from openpyxl import Workbook


def main() -> None:
    """Generate a demo UDS script xlsx matching uds_gui.py format."""
    wb = Workbook()
    ws = wb.active
    ws.title = "UDS_Script"

    # Row 1: ECU ID header
    ws.append(["ECU_ID", "0x7E8"])

    # Row 2: column headers
    ws.append(
        [
            "Index",
            "CAN_ID",
            "ServiceID",
            "SubServiceID",
            "Data",
            "NeedResponse",
            "ExpectedResponse",
            "FrameType",
            "WaitMs",
        ]
    )

    # Row 3: diagnostic session control
    ws.append(
        [
            1,
            "0x7E0",
            "0x10",
            "0x01",
            "",
            1,
            "0x50 0x01",
            "STD",
            100,
        ]
    )

    # Row 4: read data by identifier (VIN example DID)
    ws.append(
        [
            2,
            "0x7E0",
            "0x22",
            "0xF1",
            "0x90",
            1,
            "0x62 0xF1 0x90",
            "STD",
            100,
        ]
    )

    # Row 5: multi-byte expected response example
    ws.append(
        [
            3,
            "0x7E0",
            "0x19",
            "0x02",
            "0xFF",
            1,
            "0x59 0x02 0xFF 0x00",
            "EXT",
            100,
        ]
    )

    # Row 6: tester present
    ws.append(
        [
            4,
            "0x7E0",
            "0x3E",
            "0x00",
            "",
            0,
            "",
            "STD",
            1000,
        ]
    )

    # Row 7: upload flash driver macro
    ws.append(
        [
            10,
            "0x7E0",
            "uploadflashdriver",
            "0x00",
            "",
            0,
            "",
            "STD",
            0,
        ]
    )

    # Row 8: upload application macro
    ws.append(
        [
            20,
            "0x7E0",
            "uploadapp",
            "0x00",
            "",
            0,
            "",
            "STD",
            0,
        ]
    )

    out_path = Path(__file__).with_name("uds_commands_demo.xlsx")
    wb.save(out_path)
    print(f"Created demo script: {out_path}")


if __name__ == "__main__":
    main()

