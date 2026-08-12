import pigpio
import time

# 8 canais de LED de alto brilho (drivers PT4115, pino DIM/PWM).
# GPIO -> comprimento de onda (nm). 365 movido do GPIO18 -> GPIO16.
# DIM do PT4115 tem pull-up interno: 1 = aceso, 0 = apagado.
LED_BANDS = {
    "365": 16,  # UV (movido do GPIO18 -> GPIO16)
    "400": 19,
    "460": 20,
    "520": 21,
    "590": 22,
    "660": 23,
    "730": 24,
    "850": 25,  # IR
}

pi = pigpio.pi()  # Connect to pigpio daemon

# Todos como saida e apagados antes de comecar
for pin in LED_BANDS.values():
    pi.set_mode(pin, pigpio.OUTPUT)
    pi.write(pin, 0)

try:
    while True:
        for nm, pin in LED_BANDS.items():
            print(f"{nm} nm (GPIO {pin})")
            pi.write(pin, 1)
            time.sleep(1)
            pi.write(pin, 0)
            time.sleep(0.2)
finally:
    # sempre apaga tudo ao sair (Ctrl+C, erro), pra nao deixar LED aceso
    for pin in LED_BANDS.values():
        pi.write(pin, 0)
    pi.stop()
