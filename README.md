# Adaptive Battery Optimization System Using ESP32 for Intelligent Charging and Battery Health Monitoring

## Overview
This project is a desktop-based battery monitoring and charging optimization system designed to monitor battery telemetry, estimate stress, and decide whether to allow or stop charging. The current codebase focuses on a Windows desktop application that reads telemetry from an ESP32 over serial, analyzes the readings, displays live diagnostics, logs data to SQLite, and supports CSV export and historical plots.

The official application entry point is `dashboardcl.py`. It is the main dashboard and the script intended to be launched for normal project use.

The project also includes a machine learning component that trains a `GradientBoostingRegressor` model to estimate stress from battery and sensor measurements. The application uses this model when available, with a fallback to a rule-based heuristic if the model is missing or fails to predict.

Important note: this repository currently contains the Python desktop application and supporting scripts, but it does not include a verified ESP32 firmware source tree or a proven hardware wiring implementation. The ESP32 communication, relay logic, and sensor interfaces are implemented as software assumptions in the Python application and should be checked against your actual hardware setup before publication or deployment.

## Problem Statement and Objectives
Laptop batteries degrade over time due to heat, charge frequency, and aggressive charging patterns. The objective of this system is to:

- monitor battery and ambient temperature,
- estimate battery stress and health status,
- decide whether charging should be allowed based on thresholds,
- maintain a history of telemetry for later analysis,
- provide a recommendation interface for repair or replacement,
- support future adaptation using a trained predictive model.

## Features Actually Implemented
The following are present in the source code and verified from the project files:

- Live serial telemetry reading from an ESP32 device at COM port 115200
- Python GUI built with `customtkinter`
- Battery percentage and charging-state monitoring using `psutil`
- Temperature, voltage, current, and power display
- Stress estimation using rule-based logic and optional ML prediction
- Charge mode classification: FAST, NORMAL, TRICKLE, or OFF
- Relay decision logic to stop or resume charging based on thresholds
- SQLite database logging
- CSV export from the telemetry database
- Historical graph view using matplotlib
- Battery health summary using design capacity and full-charge capacity ratios
- Remaining-useful-life (RUL) and degradation heuristic
- Repair-shop and replacement-battery search helpers that open browser searches
- Training script for a GradientBoostingRegressor model
- Model serialization via `joblib` as `battery_model.pkl`

The project does not currently contain a verified firmware sketch or hardware test evidence for the following components:

- INA219 implementation
- DS18B20 implementation
- IRLZ44N MOSFET driver
- relay board integration details
- ESP32 pin wiring and physical validation

Those items are expected in the project description but not confirmed by source code in this repository.

## System Architecture
The project is organized as a desktop monitoring system with two main logical parts:

1. ESP32 data acquisition layer
   - Reads sensors (expected battery and ambient measurements)
   - Sends comma-separated telemetry values over serial to the computer
   - Receives control commands from the PC to enable or disable charging logic

2. Python desktop application
   - Connects to the ESP32 on a COM port
   - Parses the serial stream
   - Reads the local laptop battery status
   - Runs charge-mode and stress logic
   - Sends control commands back to the ESP32
   - Stores readings in SQLite
   - Displays live telemetry and trend graphs
   - Exports CSV data and shows recommendations

## Hardware Components
The repository does not include a verified hardware description file or firmware source. The following hardware is referenced in code and comments but not fully verified in the repo:

- ESP32 microcontroller
- INA219 current/voltage/power monitor (mentioned in comments, not verified as code)
- DS18B20 temperature sensors (mentioned in comments, not verified as code)
- Relay-based laptop charger control
- IRLZ44N MOSFET used as a relay driver (referenced in comments, not verified as code)
- Laptop battery system being monitored

The source code uses the serial protocol and charge control decisions but does not provide a completed firmware implementation or final pin mapping.

## Software Stack
The Python application uses the following libraries:

- Python 3
- `customtkinter` for the desktop interface
- `psutil` for laptop battery state
- `pyserial` for serial communication
- `matplotlib` for plots
- `sqlite3` for local storage
- `joblib` and `numpy` for model loading
- `pandas` and `scikit-learn` for model training
- `beautifulsoup4` for battery-report parsing utility

## Communication Flow
The main dashboard script expects a serial data frame of the following format from the ESP32:

```text
battery_temp,ambient_temp,voltage,current,power
```

Example:

```text
32.4,26.1,11.82,850.0,10047.0
```

The PC sends command strings back to the ESP32 in this format:

```text
REL:<ON|OFF>|MODE:<FAST|NORMAL|TRICKLE|OFF>
```

Example:

```text
REL:ON|MODE:NORMAL
```

The code in `dashboardcl.py` sends this command when a sensor update arrives and no manual override is active.

## Machine Learning Implementation
The ML pipeline is implemented in `train_model.py` and uses a `GradientBoostingRegressor` from scikit-learn.

### Training data
- File: `battery_training_data.csv`
- Target/output column: `stress`
- Input features:
  - `battery_percent`
  - `battery_temp`
  - `ambient_temp`
  - `current`
  - `voltage`

### Model behavior
The model is trained with:

```python
GradientBoostingRegressor(n_estimators=100, random_state=42)
```

The trained model is serialized as:

- `battery_model.pkl`

The main GUI loads this model using `joblib.load(...)` and uses it for an AI-style predicted stress value. If the model is not available or prediction fails, the application falls back to rule-based stress logic.

### Verification status
The repository does not include a documented training/validation report, cross-validation, confusion matrix, or published accuracy values. No claim of model performance should be made without a proper evaluation dataset and verification step.

## Database Schema
The main dashboard creates a table named `telemetry` in `battery_telemetry.db` with the schema:

```sql
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
```

An older script, `logger.py`, creates a different table named `battery_data` in `battery_data.db` with a simpler schema for a legacy logging flow.

## Graphing and Export Features
Implemented features in the source:

- Live time-series plots for temperature, voltage, current, and stress
- Charting is handled with `matplotlib`
- Telemetry can be exported to CSV via the GUI
- The app also includes a separate static graph script for temperature history

## Charging-Control Logic
The charging logic is implemented in the main GUI script. It uses the following threshold logic:

- Current greater than 1000 mA => FAST CHARGING
- Current greater than 300 mA => NORMAL CHARGING
- Current greater than 50 mA => TRICKLE CHARGING
- Otherwise => NOT CHARGING

Safety control logic:

- If battery temperature exceeds 45 °C, relay is turned OFF
- If stress exceeds 80, relay is turned OFF
- If battery percentage reaches 90%, charging stops
- If battery percentage drops to 20%, charging may resume

This logic is in Python and is not validated against a real ESP32/relay assembly in this repository.

## Installation and Setup for Windows
### 1) Clone the project
```powershell
git clone <your-repository-url>
cd <your-project-folder>
```

### 2) Create a virtual environment
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### 3) Install dependencies
```powershell
python -m pip install customtkinter psutil pyserial matplotlib joblib numpy pandas scikit-learn beautifulsoup4
```

### 4) Run the application
```powershell
python dashboardcl.py
```

## ESP32 Firmware Upload and Configuration
This repository does not contain the actual ESP32 firmware source code or an upload script. To use the project with real hardware, you need to create or add an ESP32 firmware sketch that:

- reads the sensors,
- sends telemetry in the exact CSV order expected by Python,
- reads the command string sent by the PC,
- toggles the relay or MOSFET driver according to the received `REL` and `MODE` strings.

### Recommended checklist
- Use the correct serial port and Baud rate: `COM3`, `115200`
- Ensure the ESP32 sends one telemetry line per sample, newline-terminated
- Parse the incoming command from the PC with the `REL:...|MODE:...` format
- Drive the relay or MOSFET based on the `MODE` value
- Confirm the actual battery sensor values are being read correctly

The repository does not contain a verified firmware configuration file for this step.

## Serial Communication and Database Configuration
The default serial settings in the main app are:

- Port: `COM3`
- Baud rate: `115200`

The app uses `serial.Serial(port, baudrate, timeout=1)` and listens for incoming lines.

The database is stored next to the script in the project folder:

- `battery_telemetry.db`

The old utility script also creates a legacy database:

- `battery_data.db`

## How to Use the Application
1. Connect the ESP32 to the computer and ensure the COM port is available.
2. Start the application with:
   ```powershell
   python dashboardcl.py
   ```
3. If the port is not automatically correct, choose the port in the Settings page.
4. Observe the live sensor values and charging decisions.
5. Use the history page to inspect temperature, voltage, and stress trends.
6. Use the Health page to review the battery health summary and heuristic RUL estimate.
7. Use Repair Shops or Replacement Battery pages to open external web search links.
8. Export telemetry data as CSV from the Settings page.

## Troubleshooting
### Common issues
- `No module named customtkinter`: install dependencies in the active virtual environment.
- `Could not open COM3`: check the actual COM port in Device Manager and select the correct port in the app.
- Serial reads are empty: verify the ESP32 is sending newline-terminated CSV data.
- Model errors: confirm `battery_model.pkl` exists and is not corrupted.
- `battery_health.py` fails: it references a local file path `C:\Users\neera\battery-report.html` and is not portable across machines.

### Windows-specific notes
- Use `py` rather than `python` if multiple Python installs are present.
- Confirm the environment is activated before running the dashboard.
- Some scripts assume direct hardware access and must be run on a system with an available COM port.

## Repository Structure
```text
BatteryProject/
├── README.md
├── requirements.txt
├── .gitignore
├── dashboardcl.py
├── dashboardc.py
├── combined_monitor.py
├── logger.py
├── graph.py
├── view_db.py
├── battery_health.py
├── battery_info.py
├── train_model.py
├── test_model.py
├── test_serial.py
├── battery_training_data.csv
├── battery_model.pkl
├── battery_data.db
├── battery_telemetry.db
```

## Screenshots
Add these screenshots later for a professional GitHub presentation.

### Required placeholders
- [ ] Dashboard overview showing live telemetry and charge-control decisions
- [ ] History/trends page showing voltage and temperature graphs
- [ ] Health page showing battery health and remaining-useful-life estimate
- [ ] Settings page showing serial-port selection and CSV export
- [ ] Hardware setup photo showing ESP32, relay, and sensor wiring
- [ ] Final demo screenshot of the system operating live

## Future Improvements
- Add a complete firmware repository with verified ESP32 logic
- Replace the heuristic stress/RUL functions with a fully validated production model
- Add calibration and data quality checks for sensor readings
- Add battery-state persistence and nightly report generation
- Add automated tests for serial parsing and database writes
- Add Docker or packaging support for easier installation

## Acknowledgements
This project demonstrates a practical approach to battery monitoring, adaptive charging, and predictive maintenance using a mixed Python + embedded-systems workflow. It is intended as a standalone research and prototype project and should be validated against your unique hardware setup before being treated as a production-grade system.

## Notes and Limitations
This repository does not currently contain enough verified implementation details to claim fully completed hardware integration for INA219, DS18B20, relay drivers, or the physical ESP32 circuit. The code clearly implements the PC-side monitoring and control logic, but the embedded firmware and hardware design must be reviewed and validated separately before publication as a complete end-to-end system.
