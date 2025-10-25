"""
Oura API Token Refresh Script

- Most functions can be loaded ahead of time.

- All code below the "Interactive OAuth Setup (run these one by one)"
  must be run interactively. Browser-based authorization is required.

"""

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

# Function definitions
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

def get_sleep_hrv(start_date, end_date):
    endpoint = f"usercollection/sleep?start_date={start_date}&end_date={end_date}"
    return make_api_request(endpoint)


# === Interactive OAuth Setup (run these one by one) ===

# Step 1: Get authorization URL
auth_url = get_authorization_url()
print("Visit this URL to authorize:")
print(auth_url)

#  === DEBUG FOR STEP 1 === #
# auth_url = get_authorization_url()
# print("Full authorization URL:")
# print(auth_url)
# print("\nURL parts:")
# print(f"Client ID: {CLIENT_ID}")
# print(f"Redirect URI: {REDIRECT_URI}")

# Step 2: After visiting URL and getting redirected, extract the 'code' parameter
# This authorization code can be found in the URL after authorizing app
authorization_code = "VD2NS5VMFPT3ZD7CBKSSY26PEHM33PCV"

# Step 3: Exchange code for tokens
tokens = exchange_code_for_tokens(authorization_code)

# === DEBUG FOR STEP 3 === #
# print(tokens) # debug only

# Step 4: Save tokens to .env file
save_tokens_to_env(tokens)

# === After tokens are saved, test API access === #
user_info = make_api_request("usercollection/personal_info")
print(user_info)

my_sleep = get_sleep_hrv(start_date='2025-09-22', end_date='2025-09-23')

# Extract heart rate data
night = my_sleep['data'][0]
hr_data = night['heart_rate']
hr_array = hr_data['items']
start_time = hr_data['timestamp']

import polars as pl
from datetime import datetime, timedelta

start_dt = datetime.fromisoformat(start_time.rstrip('Z')).replace(tzinfo=None)

timestamps = [start_dt + timedelta(seconds=i*300) for i in range(len(hr_array))]


# df = pl.DataFrame({'timestamp': timestamps, 'hr':hr_array}).with_columns(pl.col('timestamp').dt.convert_time_zone('America/Chicago').alias('timestamp_chicago'))

df = pl.DataFrame({'timestamp': timestamps, 'hr':hr_array})

df.head()

df.select(pl.col('hr').min())
df.bottom_k(1, by=('hr'))

df.filter(pl.col('hr').is_null())


df_10min = df.with_columns((pl.col('timestamp').dt.truncate('10m')).alias('timestamp_10min')).group_by('timestamp_10min').agg(pl.col('hr').mean().alias('hr_10min_avg'))

rhr_10min_lowest = df_10min.select(pl.col('hr_10min_avg').min().alias('rhr_10min_lowest'))
