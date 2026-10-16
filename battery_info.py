import psutil

battery = psutil.sensors_battery()

print("Battery Percentage:", battery.percent)
print("Plugged In:", battery.power_plugged)
print("Seconds Left:", battery.secsleft)