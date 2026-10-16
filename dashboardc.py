import customtkinter as ctk
import psutil
import serial

# -----------------------------
# ESP32 Serial Connection
# -----------------------------
try:
    ser = serial.Serial("COM3", 115200, timeout=1)
except:
    ser = None

# -----------------------------
# Battery Health Data
# -----------------------------
DESIGN_CAPACITY = 54880
FULL_CHARGE_CAPACITY = 35249
CYCLE_COUNT = 629

BATTERY_HEALTH = round(
    (FULL_CHARGE_CAPACITY / DESIGN_CAPACITY) * 100,
    2
)

# -----------------------------
# GUI Setup
# -----------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.title("Battery Optimization System")
app.geometry("1200x750")

title = ctk.CTkLabel(
    app,
    text="Battery Optimization System",
    font=("Arial", 32, "bold")
)
title.pack(pady=15)

main_frame = ctk.CTkFrame(app)
main_frame.pack(fill="both", expand=True, padx=20, pady=10)

left_frame = ctk.CTkFrame(main_frame)
left_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

right_frame = ctk.CTkFrame(main_frame)
right_frame.pack(side="right", fill="both", expand=True, padx=10, pady=10)

# -----------------------------
# LEFT PANEL
# -----------------------------
battery_label = ctk.CTkLabel(
    left_frame,
    text="Battery %: --",
    font=("Arial", 24)
)
battery_label.pack(pady=10)

battery_bar = ctk.CTkProgressBar(
    left_frame,
    width=300
)
battery_bar.pack(pady=10)

charging_label = ctk.CTkLabel(
    left_frame,
    text="Charging: --",
    font=("Arial", 24)
)
charging_label.pack(pady=10)

health_label = ctk.CTkLabel(
    left_frame,
    text=f"Battery Health: {BATTERY_HEALTH}%",
    font=("Arial", 24)
)
health_label.pack(pady=10)

cycle_label = ctk.CTkLabel(
    left_frame,
    text=f"Cycle Count: {CYCLE_COUNT}",
    font=("Arial", 24)
)
cycle_label.pack(pady=10)

design_label = ctk.CTkLabel(
    left_frame,
    text=f"Design Capacity: {DESIGN_CAPACITY} mWh",
    font=("Arial", 18)
)
design_label.pack(pady=5)

full_label = ctk.CTkLabel(
    left_frame,
    text=f"Full Charge Capacity: {FULL_CHARGE_CAPACITY} mWh",
    font=("Arial", 18)
)
full_label.pack(pady=5)

status_label = ctk.CTkLabel(
    left_frame,
    text="Checking...",
    font=("Arial", 22, "bold")
)
status_label.pack(pady=20)

# -----------------------------
# RIGHT PANEL
# -----------------------------
sensor_title = ctk.CTkLabel(
    right_frame,
    text="Sensor Monitoring",
    font=("Arial", 28, "bold")
)
sensor_title.pack(pady=15)

battery_temp_label = ctk.CTkLabel(
    right_frame,
    text="Battery Temp: -- °C",
    font=("Arial", 22)
)
battery_temp_label.pack(pady=8)

ambient_temp_label = ctk.CTkLabel(
    right_frame,
    text="Ambient Temp: -- °C",
    font=("Arial", 22)
)
ambient_temp_label.pack(pady=8)

voltage_label = ctk.CTkLabel(
    right_frame,
    text="Voltage: -- V",
    font=("Arial", 22)
)
voltage_label.pack(pady=8)

current_label = ctk.CTkLabel(
    right_frame,
    text="Current: -- mA",
    font=("Arial", 22)
)
current_label.pack(pady=8)

power_label = ctk.CTkLabel(
    right_frame,
    text="Power: -- mW",
    font=("Arial", 22)
)
power_label.pack(pady=8)

stress_label = ctk.CTkLabel(
    right_frame,
    text="Battery Stress: -- %",
    font=("Arial", 22)
)
stress_label.pack(pady=8)

mode_label = ctk.CTkLabel(
    right_frame,
    text="Charging Mode: --",
    font=("Arial", 22)
)
mode_label.pack(pady=8)

relay_label = ctk.CTkLabel(
    right_frame,
    text="Relay Decision: --",
    font=("Arial", 22)
)
relay_label.pack(pady=8)

recommendation_label = ctk.CTkLabel(
    right_frame,
    text="Recommendation: --",
    font=("Arial", 18),
    wraplength=400
)
recommendation_label.pack(pady=15)

# -----------------------------
# UPDATE FUNCTION
# -----------------------------
def update_data():

    battery = psutil.sensors_battery()

    if battery:

        battery_label.configure(
            text=f"Battery %: {battery.percent}%"
        )

        battery_bar.set(
            battery.percent / 100
        )

        charging_label.configure(
            text=f"Charging: {battery.power_plugged}"
        )

        if BATTERY_HEALTH >= 80:
            status = "Battery Condition: GOOD"

        elif BATTERY_HEALTH >= 70:
            status = "Battery Condition: FAIR"

        else:
            status = "Battery Condition: POOR"

        status_label.configure(text=status)

    if ser:

        try:

            line = ser.readline().decode().strip()

            values = line.split(",")

            if len(values) == 5:

                battery_temp = float(values[0])
                ambient_temp = float(values[1])
                voltage = float(values[2])
                current = float(values[3])
                power = float(values[4])

                battery_temp_label.configure(
                    text=f"Battery Temp: {battery_temp:.2f} °C"
                )

                ambient_temp_label.configure(
                    text=f"Ambient Temp: {ambient_temp:.2f} °C"
                )

                voltage_label.configure(
                    text=f"Voltage: {voltage:.2f} V"
                )

                current_label.configure(
                    text=f"Current: {current:.2f} mA"
                )

                power_label.configure(
                    text=f"Power: {power:.2f} mW"
                )

                current_ma = abs(current)

                # ---------------------
                # Charging Mode
                # ---------------------
                if current_ma > 1000:
                    charging_mode = "FAST CHARGING"

                elif current_ma > 300:
                    charging_mode = "NORMAL CHARGING"

                elif current_ma > 50:
                    charging_mode = "TRICKLE CHARGING"

                else:
                    charging_mode = "NOT CHARGING"

                mode_label.configure(
                    text=f"Charging Mode: {charging_mode}"
                )

                # ---------------------
                # Stress Index
                # ---------------------
                if current_ma > 1000:
                    current_score = 100
                elif current_ma > 500:
                    current_score = 60
                else:
                    current_score = 20

                if battery_temp > 45:
                    temp_score = 100
                elif battery_temp > 35:
                    temp_score = 60
                else:
                    temp_score = 20

                if battery and battery.percent > 90:
                    battery_score = 100
                elif battery and battery.percent > 80:
                    battery_score = 60
                else:
                    battery_score = 20

                stress = int(
                    current_score * 0.5 +
                    temp_score * 0.3 +
                    battery_score * 0.2
                )

                stress_label.configure(
                    text=f"Battery Stress: {stress}%"
                )

                # ---------------------
                # Relay Decision
                # ---------------------
                relay_decision = "ON"

                if battery:

                    if battery.percent >= 90:
                        relay_decision = "OFF"

                    elif battery_temp > 45:
                        relay_decision = "OFF"

                    elif stress > 80:
                        relay_decision = "OFF"

                    elif battery.percent <= 20:
                        relay_decision = "ON"

                relay_label.configure(
                    text=f"Relay Decision: {relay_decision}"
                )

                # ---------------------
                # Recommendations
                # ---------------------
                recommendation = "Operating Normally"

                if stress > 80:
                    recommendation = (
                        "High battery stress detected"
                    )

                if battery_temp > 45:
                    recommendation = (
                        "High temperature detected"
                    )

                if charging_mode == "FAST CHARGING":
                    recommendation = (
                        "Frequent fast charging may reduce battery lifespan"
                    )

                recommendation_label.configure(
                    text=f"Recommendation: {recommendation}"
                )

        except:
            pass

    app.after(1000, update_data)

# -----------------------------
# START
# -----------------------------
update_data()
app.mainloop()