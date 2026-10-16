import sqlite3
import matplotlib.pyplot as plt

conn = sqlite3.connect("battery_data.db")
cursor = conn.cursor()

cursor.execute("""
SELECT timestamp,
       battery_temp,
       ambient_temp
FROM battery_data
ORDER BY timestamp
""")

rows = cursor.fetchall()

timestamps = []
battery_temps = []
ambient_temps = []

for row in rows:
    timestamps.append(row[0])
    battery_temps.append(row[1])
    ambient_temps.append(row[2])

conn.close()

plt.figure(figsize=(10,5))

plt.plot(battery_temps, label="Battery Temp")
plt.plot(ambient_temps, label="Ambient Temp")

plt.title("Temperature Monitoring")
plt.xlabel("Samples")
plt.ylabel("Temperature (°C)")

plt.legend()
plt.grid(True)

plt.show()