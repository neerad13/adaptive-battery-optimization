from bs4 import BeautifulSoup
import re

file_path = r"C:\Users\neera\battery-report.html"

with open(file_path, "r", encoding="utf-8") as f:
    soup = BeautifulSoup(f, "html.parser")

text = soup.get_text(" ", strip=True)

design_match = re.search(
    r"DESIGN CAPACITY\s+([\d,]+)\s+mWh",
    text
)

full_match = re.search(
    r"FULL CHARGE CAPACITY\s+([\d,]+)\s+mWh",
    text
)

cycle_match = re.search(
    r"CYCLE COUNT\s+(\d+)",
    text
)

design_capacity = int(
    design_match.group(1).replace(",", "")
)

full_charge_capacity = int(
    full_match.group(1).replace(",", "")
)

cycle_count = int(
    cycle_match.group(1)
)

battery_health = (
    full_charge_capacity /
    design_capacity
) * 100

print()

print("Design Capacity:",
      design_capacity, "mWh")

print("Full Charge Capacity:",
      full_charge_capacity, "mWh")

print("Cycle Count:",
      cycle_count)

print("Battery Health:",
      round(battery_health, 2), "%")