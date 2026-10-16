import pandas as pd
import joblib
from sklearn.ensemble import GradientBoostingRegressor

df = pd.read_csv(
    "battery_training_data.csv",
    sep=","
)

print(df.head())

X = df[
    [
        "battery_percent",
        "battery_temp",
        "ambient_temp",
        "current",
        "voltage"
    ]
]

y = df["stress"]

model = GradientBoostingRegressor(
    n_estimators=100,
    random_state=42
)

model.fit(X, y)

joblib.dump(model, "battery_model.pkl")

print("Model trained and saved!")