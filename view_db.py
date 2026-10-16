import sqlite3

conn = sqlite3.connect("battery_data.db")
cursor = conn.cursor()

cursor.execute("""
SELECT *
FROM battery_data
ORDER BY timestamp DESC
LIMIT 20
""")

rows = cursor.fetchall()

for row in rows:
    print(row)

conn.close()