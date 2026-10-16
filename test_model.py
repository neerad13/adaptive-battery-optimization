# test_model.py

import joblib
import numpy as np

model = joblib.load("battery_model.pkl")

sample = np.array([
    [85, 38, 30, 600, 2.5]
])

prediction = model.predict(sample)

print("Predicted Stress:", prediction[0])