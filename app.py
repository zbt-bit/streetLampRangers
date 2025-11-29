import pandas as pd
import joblib
import requests
import random # Imported for the read_health_sensor simulation
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from sklearn.ensemble import RandomForestClassifier # Needed for type hint in determine_lamp_action

# --- CONFIGURATION (WeatherAPI + Self-Diagnosis Failsafe) ---
app = Flask(__name__)
CORS(app) 
MODEL_FILE = 'street_lamp_model.joblib'
FEATURES = ['humidity', 'cloudcover', 'visibility', 'uvindex', 'day_of_year', 'temp', 'precip']

# Self-Diagnosis Failsafe Threshold
MAX_SAFE_TEMP_C = 55.0 # Typical max safe internal temperature for embedded systems

# Weather API Setup
WEATHER_API_KEY = '10012805f62e4da08c702836252911' 
LOCATION = 'Kuala Lumpur'
WEATHER_URL = f"http://api.weatherapi.com/v1/forecast.json?key={WEATHER_API_KEY}&q={LOCATION}&days=1&aqi=no&alerts=no"


def read_health_sensor():
    """
    Simulates reading the internal temperature sensor attached to the Edge device.
    In a real system, this would read from a hardware interface (GPIO, I2C, etc.).
    """
    # Placeholder: Randomly simulate a temperature for testing the failsafe
    current_temp = random.uniform(30.0, 65.0) 
    
    is_overheated = current_temp > MAX_SAFE_TEMP_C
    
    return {
        'current_temp_c': round(current_temp, 1),
        'max_safe_temp_c': MAX_SAFE_TEMP_C,
        'is_overheated': is_overheated
    }


# Load the ML model (trained to predict 1=MAX/Rain/Fog, 0=DIMMER/OFF/Clear)
try:
    model = joblib.load(MODEL_FILE)
    print(f"Model '{MODEL_FILE}' loaded successfully.")
except FileNotFoundError:
    print(f"Error: Model file not found. Please run train_model.py first.")
    model = None
except Exception as e:
    print(f"Error loading model: {e}")
    model = None


# --- HELPER FUNCTIONS ---

def calculate_day_of_year(dt):
    """Calculates the day of the year (1-366)."""
    return dt.timetuple().tm_yday

def is_currently_night(sunrise_time_str, sunset_time_str):
    """Determines if the current time is between sunset and sunrise."""
    today = datetime.now()
    
    try:
        # WeatherAPI uses I:M p format (e.g., 07:15 AM)
        sunrise_dt = datetime.strptime(f"{today.strftime('%Y-%m-%d')} {sunrise_time_str}", '%Y-%m-%d %I:%M %p')
        sunset_dt = datetime.strptime(f"{today.strftime('%Y-%m-%d')} {sunset_time_str}", '%Y-%m-%d %I:%M %p')
    except ValueError:
        print("Warning: Failed to parse sunrise/sunset times. Using safe default.")
        current_hour = today.hour
        return current_hour < 7 or current_hour >= 19

    # Check if current time is BEFORE sunrise OR AFTER sunset
    is_night = today < sunrise_dt or today >= sunset_dt
    
    return is_night


# --- CORE LOGIC: API FETCH AND MAPPING ---

def fetch_real_time_weather():
    """Fetches real-time weather and astronomical data from WeatherAPI."""
    
    try:
        response = requests.get(WEATHER_URL, timeout=10)
        response.raise_for_status() 
        data = response.json()
        
        # 1. Extract necessary data points (WeatherAPI structure)
        current = data['current']
        astro = data['forecast']['forecastday'][0]['astro']
        
        # 2. Determine time-based features
        current_date = datetime.fromtimestamp(current['last_updated_epoch'])
        is_night = is_currently_night(astro['sunrise'], astro['sunset'])

        # 3. MAPPING: Map API fields to ML model features
        ml_features = {
            'temp': current['temp_c'], 
            'humidity': current['humidity'],
            # Use 'precip_mm' for precipitation
            'precip': current['precip_mm'], 
            # Use 'cloud' for cloudcover (WeatherAPI uses %)
            'cloudcover': current['cloud'], 
            # Use 'vis_km' for visibility (cap at 10.0, the max in the training data)
            'visibility': min(current['vis_km'], 10.0), 
            'uvindex': current['uv'],
            'day_of_year': calculate_day_of_year(current_date),
            'is_night_time': is_night 
        }
        
        return {
            'status': 'success',
            'data': ml_features,
            'astronomy': {
                'sunrise': astro['sunrise'], 
                'sunset': astro['sunset']
            }
        }

    except requests.exceptions.RequestException as e:
        print(f"Weather API Error: {e}")
        return {'status': 'error', 'message': f'Could not connect to WeatherAPI: {e}'}
    except KeyError as e:
        print(f"Weather API Parsing Error: Missing key {e}")
        return {'status': 'error', 'message': f'Failed to parse weather data structure: Missing key {e}'}


def determine_lamp_action(weather_features, is_motion_detected, model_clf: RandomForestClassifier, health_status: dict):
    """
    Implements the hierarchical control logic (Time -> Failsafe -> Safety -> Efficiency).
    
    Args:
        weather_features: Dict of weather data.
        is_motion_detected: Boolean from camera/IR sensor.
        model_clf: The trained RandomForestClassifier model.
        health_status: Dict containing 'is_overheated' status.
    """
    
    # --- 1. TIME CHECK (Step 1) ---
    if not weather_features.get('is_night_time', False):
        return "OFF (Daytime)"

    # --- 2. FAILSAFE CHECK (NEW - HIGHEST PRIORITY) ---
    if health_status['is_overheated']:
        # Overheat detected! Trigger alert/failsafe. 
        # Forcing MAX OUTPUT ensures maintenance staff can easily identify the failing unit.
        print(f"*** SYSTEM ALERT: OVERHEAT DETECTED ({health_status['current_temp_c']}°C). FORCING MAX OUTPUT. ***")
        return "MAX OUTPUT (System Failsafe: Overheat)"


    # --- 3. WEATHER CHECK (Safety Override - Step 3) ---
    
    # Prepares features for the model (ONLY the 7 weather features trained)
    X_weather = pd.DataFrame([weather_features])[FEATURES]
    
    # Predicts if MAX OUTPUT is required (1) or not (0)
    max_light_required = model_clf.predict(X_weather)[0] 
    
    if max_light_required == 1:
        # Rainy or Foggy conditions predicted by ML model. Safety override.
        return "MAX OUTPUT (Safety Override: Rain/Fog)"

    # --- 4. MOTION CHECK (Efficiency - Steps 4, 5) ---
    # Good weather conditions (max_light_required == 0) and system is healthy
    if is_motion_detected:
        # Motion detected -> Turn on Dimmer Light
        return "DIMMER OUTPUT (Motion Detected)"
    else:
        # No motion detected -> Turn off light for max savings
        return "OFF (No Motion Detected)"


# --- FLASK ROUTE ---

@app.route('/api/control_lamp', methods=['POST'])
def control_lamp():
    if not model:
        return jsonify({'status': 'error', 'message': 'AI Model not loaded.'}), 500

    try:
        # 1. Get Motion Status
        client_data = request.get_json()
        is_motion_detected = client_data.get('is_motion_detected', False)

        # 2. Fetch Real-Time Weather Data
        weather_result = fetch_real_time_weather()
        if weather_result['status'] == 'error':
            return jsonify(weather_result), 500
        
        weather_features = weather_result['data']
        astronomy = weather_result['astronomy']
        
        # 3. Read System Health Sensor (NEW)
        health_status = read_health_sensor()


        # 4. Determine Final Lamp Action using Hierarchical Logic
        lamp_action = determine_lamp_action(
            weather_features, 
            is_motion_detected, 
            model, 
            health_status # Passing health status to the logic
        )
        
        # 5. Return the result
        return jsonify({
            'status': 'success',
            'lamp_action': lamp_action,
            'system_health': health_status, # NEW: System health status included
            'weather_data_for_chart': weather_features, 
            'inputs_used': {
                'is_night_time': weather_features.get('is_night_time', False),
                'is_motion_detected': is_motion_detected,
                'location': LOCATION,
                'sunrise': astronomy['sunrise'],
                'sunset': astronomy['sunset']
            }
        })
        

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({
            'status': 'error', 
            'message': f'An internal server error occurred: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
