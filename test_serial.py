import serial

ser = serial.Serial("COM3", 115200)

while True:
    line = ser.readline().decode().strip()
    print(line)

