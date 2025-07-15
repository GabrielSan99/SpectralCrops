import pigpio
import time

WHITE_LED = 17
RED_LED = 27
YELLOW_LED = 22
BLUE_LED = 23

pi = pigpio.pi() # Connect to pigpio daemon
pi.set_mode(WHITE_LED, pigpio.OUTPUT) # Set pin as output

while True:
    pi.write(WHITE_LED, 1) # Set pin high
    time.sleep(1)  
    pi.write(WHITE_LED, 0) # Set pin low
    time.sleep(1)
    pi.write(RED_LED, 1) # Set pin high
    time.sleep(1)  
    pi.write(RED_LED, 0) # Set pin low
    time.sleep(1)
    pi.write(YELLOW_LED, 1) # Set pin high
    time.sleep(1)  
    pi.write(YELLOW_LED, 0) # Set pin low
    time.sleep(1)
    pi.write(BLUE_LED, 1) # Set pin high
    time.sleep(1)  
    pi.write(BLUE_LED, 0) # Set pin low
    time.sleep(1)