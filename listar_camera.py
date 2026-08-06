# test_list.py
import mvIMPACT.acquire as impact

dev_mgr = impact.DeviceManager()
dev_mgr.updateDeviceList()

print(f"{dev_mgr.deviceCount()} dispositivos encontrados.")
for i in range(dev_mgr.deviceCount()):
    dev = dev_mgr.getDevice(i)
    print(f"[{i}] {dev.family.read()} - {dev.serial.read()} - {dev.product.read()}")
