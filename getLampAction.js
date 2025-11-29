/**
 * Sends the motion detection status to the AI Lamp Control Server 
 * and processes the resulting lamp action and system health.
 * * NOTE: The server (app.py) handles fetching time and weather data automatically 
 * using the configured WeatherAPI key. It only needs the motion status from the client.
 * * @param {boolean} isMotion - True if motion (car/person) is detected.
 */
async function getLampAction(isMotion) {
    const apiUrl = 'http://127.0.0.1:5000/api/control_lamp';
    
    // The server only expects 'is_motion_detected'
    const payload = {
        is_motion_detected: isMotion,
    };

    console.log("Sending request to server with motion status:", isMotion);

    try {
        const response = await fetch(apiUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            // Handle HTTP error statuses (4xx, 5xx)
            const errorBody = await response.json();
            console.error("API Error Response:", response.status, response.statusText, errorBody);
            return "ERROR: Server Response Issue";
        }

        const result = await response.json();

        // --- Log Server Response Details ---
        console.log("--- Server Response ---");
        
        // 1. Lamp Action Result
        console.log("Lamp Action:", result.lamp_action); 

        // 2. System Health Status (NEW)
        const health = result.system_health;
        console.log(`System Health: ${health.current_temp_c}°C (Max Safe: ${health.max_safe_temp_c}°C)`);
        if (health.is_overheated) {
            console.warn("CRITICAL ALERT: System Overheated! Failsafe is active.");
        }
        
        // 3. Inputs Used (For debugging/transparency)
        console.log("Server Inputs:", result.inputs_used);
        console.log("Weather Data Used:", result.weather_data_for_chart);
        
        return result.lamp_action;

    } catch (error) {
        // Handle network errors (e.g., server is down or unreachable)
        console.error("Network Error: Could not reach the server.", error);
        return "ERROR: Network or Server Unreachable";
    }
}

// --- Example Usage ---

// Scenario 1: Nighttime, Motion Detected (Should aim for DIMMER OUTPUT if healthy and clear)
console.log("\n--- Scenario 1: Night, Motion Detected ---");
getLampAction(true);

// Scenario 2: Nighttime, No Motion Detected (Should aim for OFF if healthy and clear)
console.log("\n--- Scenario 2: Night, No Motion Detected ---");
getLampAction(false);

// Note: The action for both scenarios will depend on the real-time weather and the 
// simulated internal temperature reported by the server.
