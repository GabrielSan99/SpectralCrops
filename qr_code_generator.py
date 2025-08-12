import qrcode

ssid = "SpectralCrops"
senha = "spectral123"
seguranca = "WPA"  # ou "WEP", ou "nopass" se não tiver senha

wifi_config = f"WIFI:T:{seguranca};S:{ssid};P:{senha};;"

qr = qrcode.make(wifi_config)
qr.save("wifi_qrcode.png")