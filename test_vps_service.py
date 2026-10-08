import requests
import json

VPS_URL = "http://localhost:8010"
API_KEY = "mipt-vps-secret-key-2024"

test_data = {
    "user_id": 123,
    "report_data": {
        "week_number": 1,
        "main_job_title": "Socket Installation",
        "daily_reports": [
            {
                "day": "Monday",
                "date": "2026-08-03",
                "description": "Installed sockets",
                "hours_worked": 8.0
            }
        ],
        "operations": [
            {
                "step_number": 1,
                "operation_description": "Marked locations",
                "tools_used": "Pencil, ruler"
            }
        ],
        "user_program": "COMPUTER_ENGINEERING",
        "company_name": "Tanzania Telecom",
        "additional_instructions": "Make it more technical"
    }
}

response = requests.post(
    f"{VPS_URL}/api/weekly/enhance",
    json=test_data,
    headers={"X-API-Key": API_KEY}
)

print(f"Status: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")
