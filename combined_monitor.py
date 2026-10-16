import serial
import psutil

ser = serial.Serial("COM3", 115200)

while True:

    line = ser.readline().decode().strip()

    if not line:
        continue

    values = line.split(",")

    if len(values) != 5:
        continue

    battery_temp = values[0]
    ambient_temp = values[1]
    voltage = values[2]
    current = values[3]
    power = values[4]

    laptop_battery = psutil.sensors_battery()

    print("\n========================")

    print("Laptop Battery %:",
          laptop_battery.percent)

    print("Charging:",
          laptop_battery.power_plugged)

    print("Battery Temp:",
          battery_temp)

    print("Ambient Temp:",
          ambient_temp)

    print("Voltage:",
          voltage)

    print("Current:",
          current)

    print("Power:",
          power)