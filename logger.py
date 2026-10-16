import serial
import sqlite3
import psutil
from datetime import datetime

ser = serial.Serial("COM3", 115200)

conn = sqlite3.connect("battery_data.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS battery_data(
    timestamp TEXT,
    battery_percent REAL,
    charging_status TEXT,
    battery_temp REAL,
    ambient_temp REAL,
    voltage REAL,
    current REAL,
    power REAL
)
""")

conn.commit()

print("Logging Started...")

while True:

    line = ser.readline().decode().strip()

    if not line:
        continue

    values = line.split(",")

    if len(values) != 5:
        continue

    laptop_battery = psutil.sensors_battery()

    battery_percent = laptop_battery.percent
    charging_status = str(laptop_battery.power_plugged)

    battery_temp = float(values[0])
    ambient_temp = float(values[1])
    voltage = float(values[2])
    current = float(values[3])
    power = float(values[4])

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
    INSERT INTO battery_data
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        timestamp,
        battery_percent,
        charging_status,
        battery_temp,
        ambient_temp,
        voltage,
        current,
        power
    ))

    conn.commit()

    print(
        timestamp,
        battery_percent,
        charging_status,
        battery_temp,
        ambient_temp,
        voltage,
        current,
        power
    )