from libTSCANAPI import *

configs = [
    {
        'FChannel': 0,
        'rate_baudrate': 500,
        'data_baudrate': 2000,
        'enable_120hm': True,
        'is_fd': True,
    }
]

hwhandle = TSMasterDevice(
    configs=configs,
    is_include_tx=True,
    hwserial=b""
)

print("设备已打开：", hwhandle)

msg = TLIBCAN(
    FIdxChn=0,
    FDLC=8,
    FIdentifier=0x123,
    FProperties=1,
    FData=[1, 2, 3, 4, 5, 6, 7, 8],
)

ret = hwhandle.send_msg(msg)
print("send_msg 返回值:", ret)

hwhandle.shut_down()
print("设备已关闭")