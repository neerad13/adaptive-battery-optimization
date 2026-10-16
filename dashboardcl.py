"""
Adaptive Battery Optimisation System - Desktop Dashboard
==========================================================
Reads live telemetry (battery temp, ambient temp, voltage, current, power)
from an ESP32 over serial, computes charging-mode / stress / relay decisions,
SENDS those decisions back to the ESP32, logs everything to SQLite, and
presents it all in a multi-page CustomTkinter GUI with live graphs,
a battery-health / RUL estimator, a "find repair shops" helper and a
"buy a replacement battery" helper.

Dependencies:
    pip install customtkinter psutil pyserial matplotlib pillow

ESP32 <-> PC serial protocol
----------------------------
PC expects to RECEIVE one CSV line per sample from the ESP32:
    battery_temp,ambient_temp,voltage,current,power
    e.g.  "32.4,26.1,11.82,850.0,10047.0\n"

PC SENDS decisions back to the ESP32 as a single pipe-delimited line:
    REL:<ON|OFF>|MODE:<FAST|NORMAL|TRICKLE|OFF>
    e.g.  "REL:ON|MODE:NORMAL\n"

Update the ESP32 firmware's serial-read routine to parse this line and
drive the MOSFET/relay + charge-tapering circuit accordingly. If you
change the protocol, update `SerialManager.build_command()` below to match.
"""

import os
import time
import queue
import sqlite3
import threading
import webbrowser
from collections import deque
from datetime import datetime

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox

import psutil
import serial
import serial.tools.list_ports

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import joblib
import numpy as np

# =====================================================================
# CONFIG
# =====================================================================
DEFAULT_PORT = "COM3"
BAUD_RATE = 115200

DESIGN_CAPACITY = 54880        # mWh
FULL_CHARGE_CAPACITY = 35249   # mWh
CYCLE_COUNT = 629
RELAY_STATE = "ON"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "battery_telemetry.db")
HISTORY_LEN = 90                # points kept on the live graphs (~90s at 1s/sample)
MODEL_PATH = "battery_model.pkl"

try:
    ML_MODEL = joblib.load(MODEL_PATH)
    MODEL_LOADED = True
except Exception:
    ML_MODEL = None
    MODEL_LOADED = False

# Thermal-exposure heuristic: cumulative "hot minutes" above this temp count
# against remaining useful life. Replace with your gradient-boosting model's
# output once trained; the RUL panel already reads from `estimate_rul()`.
THERMAL_EXPOSURE_THRESHOLD_C = 40.0

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

STATUS_COLORS = {
    "GOOD": "#2ecc71",
    "FAIR": "#f1c40f",
    "POOR": "#e74c3c",
}


# =====================================================================
# PERSISTENCE
# =====================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            battery_pct REAL,
            charging INTEGER,
            battery_temp REAL,
            ambient_temp REAL,
            voltage REAL,
            current REAL,
            power REAL,
            stress INTEGER,
            charging_mode TEXT,
            relay_decision TEXT,
            recommendation TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_reading(row):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO telemetry
            (timestamp, battery_pct, charging, battery_temp, ambient_temp,
             voltage, current, power, stress, charging_mode, relay_decision, recommendation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, row)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] log failed: {e}")


def export_csv(dest_path):
    import csv
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT * FROM telemetry ORDER BY id")
    cols = [d[0] for d in cur.description]
    with open(dest_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(cur.fetchall())
    conn.close()


# =====================================================================
# SERIAL LAYER (background thread, non-blocking for the GUI)
# =====================================================================
class SerialManager:
    def __init__(self, port, baud, inbound_queue):
        self.port = port
        self.baud = baud
        self.inbound_queue = inbound_queue
        self.ser = None
        self.running = False
        self.thread = None

    def connect(self):
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=1)
            return True, f"Connected to {self.port} @ {self.baud} baud"
        except Exception as e:
            self.ser = None
            return False, f"Could not open {self.port}: {e}"

    def start(self):
        if self.ser and not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()

    def _read_loop(self):
        while self.running:
            try:
                raw = self.ser.readline().decode(errors="ignore").strip()
                if raw:
                    self.inbound_queue.put(raw)
            except Exception:
                time.sleep(0.5)

    @staticmethod
    def build_command(relay_decision, charging_mode):
        mode_map = {
           "FAST CHARGING": "FAST",
           "NORMAL CHARGING": "NORMAL",
           "TRICKLE CHARGING": "TRICKLE",
           "NOT CHARGING": "OFF"
        }

        return f"REL:{relay_decision}|MODE:{mode_map.get(charging_mode,'OFF')}"

    def send_command(self, command_str):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{command_str}\n".encode())
                return True
            except Exception as e:
                print(f"[Serial] write failed: {e}")
                return False
        return False

    def stop(self):
        self.running = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass

    def is_connected(self):
        return self.ser is not None and self.ser.is_open


# =====================================================================
# DECISION LOGIC (unchanged rules from the original script, isolated
# into pure functions so they're easy to unit-test / swap for the ML
# model described in the patent abstract)
# =====================================================================
def compute_charging_mode(current_ma):
    if current_ma > 1000:
        return "FAST CHARGING"
    elif current_ma > 300:
        return "NORMAL CHARGING"
    elif current_ma > 50:
        return "TRICKLE CHARGING"
    return "NOT CHARGING"


def compute_stress(current_ma, battery_temp, battery_pct):
    current_score = 100 if current_ma > 1000 else 60 if current_ma > 500 else 20
    temp_score = 100 if battery_temp > 45 else 60 if battery_temp > 35 else 20
    battery_score = 100 if battery_pct is not None and battery_pct > 90 else \
                     60 if battery_pct is not None and battery_pct > 80 else 20
    return int(current_score * 0.5 + temp_score * 0.3 + battery_score * 0.2)


def compute_relay_decision(battery_pct, battery_temp, stress):
    global RELAY_STATE

    # Safety overrides
    if battery_temp > 45:
        RELAY_STATE = "OFF"
        return RELAY_STATE

    if stress > 80:
        RELAY_STATE = "OFF"
        return RELAY_STATE

    if battery_pct is not None:

        # Stop charging at 90%
        if battery_pct >= 90:
            RELAY_STATE = "OFF"

        # Resume charging only at 20%
        elif battery_pct <= 20:
            RELAY_STATE = "ON"

    return RELAY_STATE


def compute_recommendation(stress, battery_temp, charging_mode):
    recommendation = "Operating normally"
    if stress > 80:
        recommendation = "High battery stress detected"
    if battery_temp > 45:
        recommendation = "High temperature detected"
    if charging_mode == "FAST CHARGING":
        recommendation = "Frequent fast charging may reduce battery lifespan"
    return recommendation

def predict_stress(
    battery_pct,
    battery_temp,
    ambient_temp,
    current_ma,
    voltage
):
    if not MODEL_LOADED:
        return None

    try:

        sample = np.array([[
            battery_pct,
            battery_temp,
            ambient_temp,
            current_ma,
            voltage
        ]])

        prediction = float(
            ML_MODEL.predict(sample)[0]
        )

        return max(
            0,
            min(
                100,
                prediction
            )
        )

    except Exception:
        return None

def estimate_rul(health_pct, cycle_count, hot_minutes, max_cycles=1000):
    """
    Heuristic remaining-useful-life estimate combining capacity fade,
    cycle count and cumulative thermal exposure. This is a stand-in for
    the multi-signal gradient-boosting regressor described in the patent
    abstract -- swap the body of this function for a `model.predict(...)`
    call once the model is trained on your SQLite telemetry log.
    """
    cycle_fraction_used = min(cycle_count / max_cycles, 1.0)
    capacity_fraction_used = 1 - (health_pct / 100)
    thermal_penalty = min(hot_minutes / 500, 1.0) * 0.15

    degradation_index = (0.5 * capacity_fraction_used +
                          0.35 * cycle_fraction_used +
                          thermal_penalty)
    remaining_fraction = max(0.0, 1 - degradation_index)
    estimated_remaining_cycles = int(remaining_fraction * max_cycles)
    return degradation_index, estimated_remaining_cycles


# =====================================================================
# GUI
# =====================================================================
class BatteryDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Adaptive Battery Optimisation System")
        self.geometry("1360x820")
        self.minsize(1150, 700)

        init_db()

        self.inbound_queue = queue.Queue()
        self.serial_manager = SerialManager(DEFAULT_PORT, BAUD_RATE, self.inbound_queue)
        ok, msg = self.serial_manager.connect()
        self.serial_manager.start()

        self.battery_health = round((FULL_CHARGE_CAPACITY / DESIGN_CAPACITY) * 100, 2)
        self.hot_minutes = 0
        self.log_lines = deque(maxlen=200)
        self.log_lines.append(msg)

        # rolling history for graphs
        self.hist_time = deque(maxlen=HISTORY_LEN)
        self.hist_temp = deque(maxlen=HISTORY_LEN)
        self.hist_ambient = deque(maxlen=HISTORY_LEN)
        self.hist_voltage = deque(maxlen=HISTORY_LEN)
        self.hist_current = deque(maxlen=HISTORY_LEN)
        self.hist_stress = deque(maxlen=HISTORY_LEN)

        self.manual_override = ctk.BooleanVar(value=False)
        self.manual_relay_state = ctk.StringVar(value="ON")

        self.last_relay_decision = "ON"
        self.last_charging_mode = "NOT CHARGING"

        self._build_layout()
        self._build_pages()
        self.show_page("dashboard")

        self.after(1000, self.update_cycle)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------------
    # LAYOUT SHELL: sidebar + content area
    # ---------------------------------------------------------------
    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nswe")
        self.sidebar.grid_propagate(False)

        ctk.CTkLabel(self.sidebar, text="🔋 BatteryOS",
                     font=("Segoe UI", 22, "bold")).pack(pady=(24, 4), padx=20, anchor="w")
        ctk.CTkLabel(self.sidebar, text="Adaptive Charge Control",
                     font=("Segoe UI", 12), text_color="#9aa0a6").pack(pady=(0, 20), padx=20, anchor="w")

        nav_items = [
            ("dashboard", "📊  Dashboard"),
            ("history", "📈  History & Trends"),
            ("health", "🩺  Health & RUL"),
            ("repair", "🔧  Repair Shops"),
            ("replace", "🛒  Replace Battery"),
            ("settings", "⚙️  Settings & Logs"),
        ]
        self.nav_buttons = {}
        for key, label in nav_items:
            btn = ctk.CTkButton(self.sidebar, text=label, anchor="w",
                                 fg_color="transparent", hover_color="#2b2f36",
                                 font=("Segoe UI", 14), height=42,
                                 command=lambda k=key: self.show_page(k))
            btn.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[key] = btn

        self.conn_indicator = ctk.CTkLabel(self.sidebar, text="● Serial: --",
                                            font=("Segoe UI", 12), text_color="#9aa0a6")
        self.conn_indicator.pack(side="bottom", pady=20, padx=20, anchor="w")

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="#1a1d21")
        self.content.grid(row=0, column=1, sticky="nswe")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self.pages = {}

    def show_page(self, key):
        for page in self.pages.values():
            page.grid_forget()
        self.pages[key].grid(row=0, column=0, sticky="nswe")
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color="#2b6cb0" if k == key else "transparent")

    def _build_pages(self):
        self.pages["dashboard"] = self._page_dashboard()
        self.pages["history"] = self._page_history()
        self.pages["health"] = self._page_health()
        self.pages["repair"] = self._page_repair()
        self.pages["replace"] = self._page_replace()
        self.pages["settings"] = self._page_settings()

    # ---------------------------------------------------------------
    # PAGE: DASHBOARD (overview + live sensors + relay/AI decisions)
    # ---------------------------------------------------------------
    def _page_dashboard(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        page.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(page, text="Battery Overview", font=("Segoe UI", 26, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=24, pady=(20, 10))

        # --- Card: Battery %
        card1 = self._card(page, 1, 0)
        self.battery_pct_label = ctk.CTkLabel(card1, text="--%", font=("Segoe UI", 34, "bold"))
        self.battery_pct_label.pack(pady=(14, 0))
        ctk.CTkLabel(card1, text="Battery Charge", font=("Segoe UI", 13), text_color="#9aa0a6").pack()
        self.battery_bar = ctk.CTkProgressBar(card1, width=200)
        self.battery_bar.pack(pady=14)
        self.charging_label = ctk.CTkLabel(card1, text="Charging: --", font=("Segoe UI", 14))
        self.charging_label.pack(pady=(0, 14))

        # --- Card: Health
        card2 = self._card(page, 1, 1)
        self.status_dot = ctk.CTkLabel(card2, text="●", font=("Segoe UI", 34), text_color="#9aa0a6")
        self.status_dot.pack(pady=(14, 0))
        self.health_label = ctk.CTkLabel(card2, text=f"{self.battery_health}%", font=("Segoe UI", 22, "bold"))
        self.health_label.pack()
        ctk.CTkLabel(card2, text="Battery Health", font=("Segoe UI", 13), text_color="#9aa0a6").pack()
        self.status_label = ctk.CTkLabel(card2, text="Checking...", font=("Segoe UI", 15, "bold"))
        self.status_label.pack(pady=(6, 14))

        # --- Card: Cycles / Capacity
        card3 = self._card(page, 1, 2)
        ctk.CTkLabel(card3, text=f"{CYCLE_COUNT}", font=("Segoe UI", 34, "bold")).pack(pady=(14, 0))
        ctk.CTkLabel(card3, text="Charge Cycles", font=("Segoe UI", 13), text_color="#9aa0a6").pack()
        ctk.CTkLabel(card3, text=f"Design: {DESIGN_CAPACITY} mWh", font=("Segoe UI", 12)).pack(pady=(10, 0))
        ctk.CTkLabel(card3, text=f"Full charge: {FULL_CHARGE_CAPACITY} mWh", font=("Segoe UI", 12)).pack(pady=(0, 14))

        # --- Sensor panel
        sensor_frame = ctk.CTkFrame(page, corner_radius=14)
        sensor_frame.grid(row=2, column=0, columnspan=2, sticky="nswe", padx=(24, 12), pady=16)
        ctk.CTkLabel(sensor_frame, text="Live Sensor Telemetry (ESP32)", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 10))

        self.sensor_labels = {}
        sensor_defs = [
            ("battery_temp", "🌡️ Battery Temp", "-- °C"),
            ("ambient_temp", "🌤️ Ambient Temp", "-- °C"),
            ("voltage", "⚡ Voltage", "-- V"),
            ("current", "🔌 Current", "-- mA"),
            ("power", "💡 Power", "-- mW"),
            ("stress", "📉 Battery Stress", "-- %"),
        ]
        for i, (key, label, default) in enumerate(sensor_defs):
            r, c = divmod(i, 2)
            box = ctk.CTkFrame(sensor_frame, fg_color="#22262c", corner_radius=10)
            box.grid(row=r + 1, column=c, sticky="nswe", padx=18, pady=8)
            ctk.CTkLabel(box, text=label, font=("Segoe UI", 13), text_color="#9aa0a6").pack(anchor="w", padx=14, pady=(10, 0))
            val = ctk.CTkLabel(box, text=default, font=("Segoe UI", 20, "bold"))
            val.pack(anchor="w", padx=14, pady=(0, 10))
            self.sensor_labels[key] = val
        sensor_frame.grid_columnconfigure((0, 1), weight=1)

        # --- Decision panel
        decision_frame = ctk.CTkFrame(page, corner_radius=14)
        decision_frame.grid(row=2, column=2, sticky="nswe", padx=(12, 24), pady=16)
        ctk.CTkLabel(decision_frame, text="Charge Control Decisions", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=18, pady=(16, 10))

        self.mode_label = ctk.CTkLabel(decision_frame, text="Charging Mode: --", font=("Segoe UI", 15))        
        self.mode_label.pack(anchor="w", padx=18, pady=6)
        self.relay_label = ctk.CTkLabel(decision_frame, text="Relay Decision: --", font=("Segoe UI", 15))
        self.relay_label.pack(anchor="w", padx=18, pady=6)
        self.ai_label = ctk.CTkLabel(
        decision_frame,
        text="AI Predicted Stress: -- %",
        font=("Segoe UI", 15, "bold")
        )
        self.ai_label.pack(anchor="w", padx=18, pady=6)
        self.esp_sync_label = ctk.CTkLabel(decision_frame, text="ESP32 sync: --", font=("Segoe UI", 12), text_color="#9aa0a6")
        self.esp_sync_label.pack(anchor="w", padx=18, pady=(0, 6))
        self.recommendation_label = ctk.CTkLabel(decision_frame, text="Recommendation: --",
                                                  font=("Segoe UI", 13), wraplength=260, justify="left")
        self.recommendation_label.pack(anchor="w", padx=18, pady=(6, 10))

        ctk.CTkSwitch(decision_frame, text="Manual override", variable=self.manual_override,
                       command=self._on_override_toggle).pack(anchor="w", padx=18, pady=(10, 4))
        self.manual_relay_menu = ctk.CTkSegmentedButton(decision_frame, values=["ON", "OFF"],
                                                         variable=self.manual_relay_state,
                                                         command=lambda v: self._send_manual_command())
        self.manual_relay_menu.pack(anchor="w", padx=18, pady=(0, 16))
        self.manual_relay_menu.configure(state="disabled")

        return page

    def _card(self, parent, row, col):
        card = ctk.CTkFrame(parent, corner_radius=14)
        card.grid(row=row, column=col, sticky="nswe", padx=12, pady=8)
        return card

    def _on_override_toggle(self):
        enabled = self.manual_override.get()
        self.manual_relay_menu.configure(state="normal" if enabled else "disabled")
        if enabled:
            self._send_manual_command()

    def _send_manual_command(self):
        if self.manual_override.get():
            cmd = self.serial_manager.build_command(self.manual_relay_state.get(), self.last_charging_mode)
            sent = self.serial_manager.send_command(cmd)
            self._append_log(f"Manual override -> {cmd} ({'sent' if sent else 'FAILED'})")

    # ---------------------------------------------------------------
    # PAGE: HISTORY (live matplotlib graphs)
    # ---------------------------------------------------------------
    def _page_history(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(page, text="History & Trends", font=("Segoe UI", 26, "bold")).pack(
            anchor="w", padx=24, pady=(20, 10))

        fig = Figure(figsize=(10, 7), dpi=100, facecolor="#1a1d21")
        self.ax_temp = fig.add_subplot(311)
        self.ax_elec = fig.add_subplot(312)
        self.ax_stress = fig.add_subplot(313)

        for ax, title in [(self.ax_temp, "Temperature (°C)"),
                           (self.ax_elec, "Voltage (V) / Current (mA)"),
                           (self.ax_stress, "Battery Stress (%)")]:
            ax.set_facecolor("#22262c")
            ax.set_title(title, color="white", fontsize=11)
            ax.tick_params(colors="#9aa0a6", labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#3a3f47")
        fig.tight_layout(pad=2.5)

        self.history_canvas = FigureCanvasTkAgg(fig, master=page)
        self.history_canvas.get_tk_widget().pack(fill="both", expand=True, padx=24, pady=10)
        self.history_fig = fig
        return page

    def _redraw_history(self):
        x = list(range(len(self.hist_time)))
        self.ax_temp.cla()
        self.ax_elec.cla()
        self.ax_stress.cla()

        for ax, title in [(self.ax_temp, "Temperature (°C)"),
                           (self.ax_elec, "Voltage (V) / Current (mA)"),
                           (self.ax_stress, "Battery Stress (%)")]:
            ax.set_facecolor("#22262c")
            ax.set_title(title, color="white", fontsize=11)
            ax.tick_params(colors="#9aa0a6", labelsize=8)
            for spine in ax.spines.values():
                spine.set_color("#3a3f47")

        self.ax_temp.plot(x, list(self.hist_temp), color="#e74c3c", label="Battery")
        self.ax_temp.plot(x, list(self.hist_ambient), color="#3498db", label="Ambient")
        self.ax_temp.legend(fontsize=7, facecolor="#22262c", labelcolor="white")

        self.ax_elec.plot(x, list(self.hist_voltage), color="#2ecc71", label="Voltage")
        ax_current = self.ax_elec.twinx()
        ax_current.plot(x, list(self.hist_current), color="#f1c40f", label="Current")
        ax_current.tick_params(colors="#9aa0a6", labelsize=8)

        self.ax_stress.plot(x, list(self.hist_stress), color="#9b59b6")
        self.ax_stress.set_ylim(0, 100)

        self.history_fig.tight_layout(pad=2.5)
        self.history_canvas.draw_idle()

    # ---------------------------------------------------------------
    # PAGE: HEALTH & RUL
    # ---------------------------------------------------------------
    def _page_health(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(page, text="Battery Health & Remaining Useful Life", font=("Segoe UI", 26, "bold")).pack(
            anchor="w", padx=24, pady=(20, 10))

        info = ctk.CTkFrame(page, corner_radius=14)
        info.pack(fill="x", padx=24, pady=10)
        ctk.CTkLabel(info, text=f"Design capacity: {DESIGN_CAPACITY} mWh", font=("Segoe UI", 14)).pack(
            anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(info, text=f"Full-charge capacity: {FULL_CHARGE_CAPACITY} mWh", font=("Segoe UI", 14)).pack(
            anchor="w", padx=18, pady=4)
        ctk.CTkLabel(info, text=f"Cycle count: {CYCLE_COUNT}", font=("Segoe UI", 14)).pack(
            anchor="w", padx=18, pady=4)
        self.health_pct_big = ctk.CTkLabel(info, text=f"Capacity-ratio health: {self.battery_health}%",
                                            font=("Segoe UI", 18, "bold"))
        self.health_pct_big.pack(anchor="w", padx=18, pady=(4, 16))

        rul_frame = ctk.CTkFrame(page, corner_radius=14)
        rul_frame.pack(fill="x", padx=24, pady=10)
        ctk.CTkLabel(rul_frame, text="Multi-Signal RUL Estimate", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=18, pady=(16, 6))
        ctk.CTkLabel(rul_frame,
                     text="Fuses capacity fade, cycle count and cumulative thermal exposure "
                          "(placeholder heuristic — replace with the trained gradient-boosting "
                          "model once historical telemetry has been collected).",
                     font=("Segoe UI", 12), text_color="#9aa0a6", wraplength=800, justify="left").pack(
            anchor="w", padx=18, pady=(0, 10))

        self.degradation_bar = ctk.CTkProgressBar(rul_frame, width=400)
        self.degradation_bar.pack(anchor="w", padx=18, pady=6)
        self.rul_label = ctk.CTkLabel(rul_frame, text="Estimated remaining cycles: --", font=("Segoe UI", 15, "bold"))
        self.rul_label.pack(anchor="w", padx=18, pady=(6, 16))

        recs = ctk.CTkFrame(page, corner_radius=14)
        recs.pack(fill="x", padx=24, pady=10)
        ctk.CTkLabel(recs, text="Care Recommendations", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=18, pady=(16, 6))
        self.health_tips_label = ctk.CTkLabel(recs, text="--", font=("Segoe UI", 13),
                                               wraplength=800, justify="left")
        self.health_tips_label.pack(anchor="w", padx=18, pady=(0, 16))
        return page

    # ---------------------------------------------------------------
    # PAGE: REPAIR SHOPS
    # ---------------------------------------------------------------
    def _page_repair(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(page, text="Find Nearby Repair Shops", font=("Segoe UI", 26, "bold")).pack(
            anchor="w", padx=24, pady=(20, 10))
        ctk.CTkLabel(page,
                     text="This looks up laptop / battery repair shops near a location using your "
                          "default browser and Google Maps. (No maps API key is bundled with this "
                          "app — plug in the Google Places API here if you want results embedded "
                          "directly in the dashboard instead of opening a browser tab.)",
                     font=("Segoe UI", 12), text_color="#9aa0a6", wraplength=900, justify="left").pack(
            anchor="w", padx=24, pady=(0, 16))

        form = ctk.CTkFrame(page, corner_radius=14)
        form.pack(fill="x", padx=24, pady=10)
        ctk.CTkLabel(form, text="Location / city / pincode", font=("Segoe UI", 13)).pack(
            anchor="w", padx=18, pady=(16, 4))
        self.repair_location_entry = ctk.CTkEntry(form, width=320, placeholder_text="e.g. Bengaluru, Karnataka")
        self.repair_location_entry.pack(anchor="w", padx=18, pady=(0, 12))

        btn_row = ctk.CTkFrame(form, fg_color="transparent")
        btn_row.pack(anchor="w", padx=18, pady=(0, 16))
        ctk.CTkButton(btn_row, text="🔧 Laptop repair shops",
                       command=lambda: self._open_maps_search("laptop repair shop")).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="🔋 Battery replacement service",
                       command=lambda: self._open_maps_search("laptop battery replacement service")).pack(side="left")
        return page

    def _open_maps_search(self, query):
        location = self.repair_location_entry.get().strip()
        full_query = f"{query} near {location}" if location else query
        url = "https://www.google.com/maps/search/" + full_query.replace(" ", "+")
        webbrowser.open(url)
        self._append_log(f"Opened maps search: {full_query}")

    # ---------------------------------------------------------------
    # PAGE: REPLACE BATTERY
    # ---------------------------------------------------------------
    def _page_replace(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(page, text="Replacement Battery Options", font=("Segoe UI", 26, "bold")).pack(
            anchor="w", padx=24, pady=(20, 10))

        status_text = ("Your battery health is currently "
                        f"{self.battery_health}% of design capacity.")
        self.replace_status_label = ctk.CTkLabel(page, text=status_text, font=("Segoe UI", 14),
                                                  wraplength=900, justify="left")
        self.replace_status_label.pack(anchor="w", padx=24, pady=(0, 16))

        form = ctk.CTkFrame(page, corner_radius=14)
        form.pack(fill="x", padx=24, pady=10)
        ctk.CTkLabel(form, text="Laptop make / model (for a compatible match)", font=("Segoe UI", 13)).pack(
            anchor="w", padx=18, pady=(16, 4))
        self.laptop_model_entry = ctk.CTkEntry(form, width=320, placeholder_text="e.g. Dell Inspiron 15 5000")
        self.laptop_model_entry.pack(anchor="w", padx=18, pady=(0, 12))

        btn_row = ctk.CTkFrame(form, fg_color="transparent")
        btn_row.pack(anchor="w", padx=18, pady=(0, 16))
        ctk.CTkButton(btn_row, text="🛒 Search on Amazon",
                       command=lambda: self._open_shopping_search("amazon")).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="🛒 Search on Flipkart",
                       command=lambda: self._open_shopping_search("flipkart")).pack(side="left")

        ctk.CTkLabel(page,
                     text=f"Design capacity for matching: {DESIGN_CAPACITY} mWh "
                          f"(~{round(DESIGN_CAPACITY/1000, 1)} Wh)",
                     font=("Segoe UI", 12), text_color="#9aa0a6").pack(anchor="w", padx=24, pady=(4, 16))
        return page

    def _open_shopping_search(self, site):
        model = self.laptop_model_entry.get().strip()
        query = f"{model} replacement laptop battery" if model else "replacement laptop battery"
        if site == "amazon":
            url = "https://www.amazon.in/s?k=" + query.replace(" ", "+")
        else:
            url = "https://www.flipkart.com/search?q=" + query.replace(" ", "+")
        webbrowser.open(url)
        self._append_log(f"Opened {site} search: {query}")

    # ---------------------------------------------------------------
    # PAGE: SETTINGS & LOGS
    # ---------------------------------------------------------------
    def _page_settings(self):
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        ctk.CTkLabel(page, text="Settings & Logs", font=("Segoe UI", 26, "bold")).pack(
            anchor="w", padx=24, pady=(20, 10))

        conn_frame = ctk.CTkFrame(page, corner_radius=14)
        conn_frame.pack(fill="x", padx=24, pady=10)
        ctk.CTkLabel(conn_frame, text="Serial Connection", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=18, pady=(16, 8))

        row = ctk.CTkFrame(conn_frame, fg_color="transparent")
        row.pack(anchor="w", padx=18, pady=(0, 16))
        ports = [p.device for p in serial.tools.list_ports.comports()] or [DEFAULT_PORT]
        self.port_var = ctk.StringVar(value=self.serial_manager.port)
        ctk.CTkOptionMenu(row, values=ports, variable=self.port_var, width=140).pack(side="left", padx=(0, 10))
        ctk.CTkButton(row, text="🔄 Reconnect", command=self._reconnect_serial).pack(side="left", padx=(0, 10))
        ctk.CTkButton(row, text="⬇ Export telemetry CSV", command=self._export_csv).pack(side="left")

        log_frame = ctk.CTkFrame(page, corner_radius=14)
        log_frame.pack(fill="both", expand=True, padx=24, pady=10)
        ctk.CTkLabel(log_frame, text="Event Log", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=18, pady=(16, 8))
        self.log_box = ctk.CTkTextbox(log_frame, height=300)
        self.log_box.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.log_box.configure(state="disabled")
        return page

    def _reconnect_serial(self):
        self.serial_manager.stop()
        self.serial_manager = SerialManager(self.port_var.get(), BAUD_RATE, self.inbound_queue)
        ok, msg = self.serial_manager.connect()
        self.serial_manager.start()
        self._append_log(msg)

    def _export_csv(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                             filetypes=[("CSV files", "*.csv")],
                                             initialfile="battery_telemetry_export.csv")
        if path:
            try:
                export_csv(path)
                messagebox.showinfo("Export complete", f"Telemetry exported to:\n{path}")
            except Exception as e:
                messagebox.showerror("Export failed", str(e))

    def _append_log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {text}"
        self.log_lines.append(line)
        if hasattr(self, "log_box"):
            self.log_box.configure(state="normal")
            self.log_box.insert("end", line + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")

    # ---------------------------------------------------------------
    # MAIN UPDATE LOOP
    # ---------------------------------------------------------------
    def update_cycle(self):
        self._update_os_battery()
        self._drain_serial_queue()
        self.conn_indicator.configure(
            text="● Serial: connected" if self.serial_manager.is_connected() else "● Serial: disconnected",
            text_color="#2ecc71" if self.serial_manager.is_connected() else "#e74c3c")
        self.after(1000, self.update_cycle)

    def _update_os_battery(self):
        battery = psutil.sensors_battery()
        self.current_battery_pct = battery.percent if battery else None
        if battery:
            self.battery_pct_label.configure(text=f"{battery.percent}%")
            self.battery_bar.set(battery.percent / 100)
            self.charging_label.configure(text=f"Charging: {'Yes' if battery.power_plugged else 'No'}")

            if self.battery_health >= 80:
                status, color = "GOOD", STATUS_COLORS["GOOD"]
            elif self.battery_health >= 70:
                status, color = "FAIR", STATUS_COLORS["FAIR"]
            else:
                status, color = "POOR", STATUS_COLORS["POOR"]
            self.status_label.configure(text=f"Condition: {status}")
            self.status_dot.configure(text_color=color)

    def _drain_serial_queue(self):
        updated = False
        while not self.inbound_queue.empty():
            line = self.inbound_queue.get_nowait()
            if self._handle_serial_line(line):
                updated = True
        if updated:
            self._redraw_history()

    def _handle_serial_line(self, line):
        values = line.split(",")
        if len(values) != 5:
            return False
        try:
            battery_temp, ambient_temp, voltage, current, power = (float(v) for v in values)
        except ValueError:
            return False

        self.sensor_labels["battery_temp"].configure(text=f"{battery_temp:.2f} °C")
        self.sensor_labels["ambient_temp"].configure(text=f"{ambient_temp:.2f} °C")
        self.sensor_labels["voltage"].configure(text=f"{voltage:.2f} V")
        self.sensor_labels["current"].configure(text=f"{current:.2f} mA")
        self.sensor_labels["power"].configure(text=f"{power:.2f} mW")

        current_ma = abs(current)
        charging_mode = compute_charging_mode(current_ma)
        stress = compute_stress(current_ma, battery_temp, self.current_battery_pct)
        ai_stress = predict_stress(
           self.current_battery_pct,
           battery_temp,
           ambient_temp,
           current_ma,
           voltage
        )

        if ai_stress is None:
           ai_stress = stress
        relay_decision = compute_relay_decision(self.current_battery_pct, battery_temp, ai_stress)
        recommendation = compute_recommendation(ai_stress, battery_temp, charging_mode)
        self.sensor_labels["stress"].configure(text=f"{stress}%")
        self.ai_label.configure(text=f"AI Predicted Stress: {ai_stress:.1f}%")
        self.mode_label.configure(text=f"Charging Mode: {charging_mode}")
        self.relay_label.configure(text=f"Relay Decision: {relay_decision}")
        self.recommendation_label.configure(text=f"Recommendation: {recommendation}")

        if battery_temp > THERMAL_EXPOSURE_THRESHOLD_C:
            self.hot_minutes += 1 / 60

        self.last_charging_mode = charging_mode
        self.last_relay_decision = relay_decision

        # ---- SEND DECISION BACK TO THE ESP32 ----
        if not self.manual_override.get():
            command = self.serial_manager.build_command(relay_decision, charging_mode)
            sent = self.serial_manager.send_command(command)
            self.esp_sync_label.configure(
                text=f"ESP32 sync: {command} ({'OK' if sent else 'send failed'})")

        # ---- history buffers for graphs ----
        self.hist_time.append(time.time())
        self.hist_temp.append(battery_temp)
        self.hist_ambient.append(ambient_temp)
        self.hist_voltage.append(voltage)
        self.hist_current.append(current)
        self.hist_stress.append(stress)

        # ---- RUL / health page ----
        degradation_index, remaining_cycles = estimate_rul(self.battery_health, CYCLE_COUNT, self.hot_minutes)
        self.degradation_bar.set(degradation_index)
        self.rul_label.configure(text=f"Estimated remaining cycles: ~{remaining_cycles}")
        tips = []
        if self.battery_health < 70:
            tips.append("Battery health is below 70% — consider a replacement (see 'Replace Battery').")
        if self.hot_minutes > 60:
            tips.append("Sustained high temperatures detected — improve airflow / avoid charging on soft surfaces.")
        if charging_mode == "FAST CHARGING":
            tips.append("Prefer normal/trickle charging when not time-constrained to slow degradation.")
        if not tips:
            tips.append("No issues detected — battery is being managed within healthy limits.")
        self.health_tips_label.configure(text="  •  ".join(tips))

        # ---- persist ----
        log_reading((
            datetime.now().isoformat(timespec="seconds"),
            self.current_battery_pct, 1 if (psutil.sensors_battery() and psutil.sensors_battery().power_plugged) else 0,
            battery_temp, ambient_temp, voltage, current, power,
            stress, charging_mode, relay_decision, recommendation
        ))
        return True

    def on_close(self):
        self.serial_manager.stop()
        self.destroy()


if __name__ == "__main__":
    app = BatteryDashboard()
    app.mainloop()
