import os
import requests
from dotenv import load_dotenv
from urllib.parse import urlencode
import json

# Load environment variables
load_dotenv()

# Oura API endpoints
AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE_URL = "https://api.ouraring.com/v2"

# OAuth configuration
CLIENT_ID = os.getenv("OURA_CLIENT_ID")
CLIENT_SECRET = os.getenv("OURA_CLIENT_SECRET")
REDIRECT_URI = "http://example.com/callback"  # You can use any localhost URL


def get_authorization_url():
    """Generate the URL to visit for OAuth authorization"""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "personal daily heartrate"
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(authorization_code):
    """Exchange authorization code for access and refresh tokens"""
    data = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    
    response = requests.post(TOKEN_URL, data=data)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
        return None


def refresh_access_token():
    """Use refresh token to get a new access token"""
    refresh_token = os.getenv("OURA_REFRESH_TOKEN")
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    }
    
    response = requests.post(TOKEN_URL, data=data)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error refreshing token: {response.status_code}")
        print(response.text)
        return None


def save_tokens_to_env(token_data):
    """Save tokens to .env file (you'll need to do this manually for now)"""
    print("Add these to your .env file:")
    print(f"OURA_ACCESS_TOKEN={token_data['access_token']}")
    print(f"OURA_REFRESH_TOKEN={token_data['refresh_token']}")


def make_api_request(endpoint):
    """Make authenticated request to Oura API"""
    access_token = os.getenv("OURA_ACCESS_TOKEN")
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    url = f"{API_BASE_URL}/{endpoint}"
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"API Error: {response.status_code}")
        print(response.text)
        return None


def get_hrv_data(start_date, end_date):
    """Get HRV data for date range (YYYY-MM-DD format)"""
    endpoint = f"usercollection/heartrate?start_date={start_date}&end_date={end_date}"
    return make_api_request(endpoint)

def get_sleep_hrv(start_date, end_date):
    endpoint = f"usercollection/sleep?start_date={start_date}&end_date={end_date}"
    return make_api_request(endpoint)


# === Interactive OAuth Setup (run these one by one) ===

# Step 1: Get authorization URL
auth_url = get_authorization_url()
print("Visit this URL to authorize:")
print(auth_url)

auth_url = get_authorization_url()
print("Full authorization URL:")
print(auth_url)
print("\nURL parts:")
print(f"Client ID: {CLIENT_ID}")
print(f"Redirect URI: {REDIRECT_URI}")

# Step 2: After visiting URL and getting redirected, extract the 'code' parameter
authorization_code = "GBKPHXQTRUJW5NSDVKDCV72NV5WN2X2V"

# Step 3: Exchange code for tokens
tokens = exchange_code_for_tokens(authorization_code)
print(tokens)

# Step 4: Save tokens to .env file
save_tokens_to_env(tokens)

# === After tokens are saved, test API access ===

# Test API access
user_info = make_api_request("usercollection/personal_info")
print(user_info)

# Get recent HRV data
hrv_data = get_hrv_data("2025-09-19", "2025-09-20")
print(hrv_data)

sleep_hrv = get_sleep_hrv("2025-09-18", "2025-09-19")
print(sleep_hrv)

def extract_hrv_timeseries(sleep_data):
    """Extract time series HRV data for Apple Health"""
    hrv_entries = []
    
    for night in sleep_data['data']:
        if night['hrv'] and night['hrv']['items']:
            # Parse the starting timestamp
            from datetime import datetime, timedelta
            import dateutil.parser
            
            start_time = dateutil.parser.parse(night['hrv']['timestamp'])
            interval_seconds = int(night['hrv']['interval'])  # 300 seconds = 5 minutes
            
            # Process each HRV reading
            for i, hrv_value in enumerate(night['hrv']['items']):
                if hrv_value is not None:  # Skip None values
                    measurement_time = start_time + timedelta(seconds=i * interval_seconds)
                    
                    hrv_entries.append({
                        'date': measurement_time.isoformat(),
                        'hrv': hrv_value,
                        'unit': 'ms',
                        'source': 'oura_ring_rmssd',
                        'measurement_type': 'rMSSD'
                    })
    
    return hrv_entries

extract_hrv_timeseries(sleep_hrv)
