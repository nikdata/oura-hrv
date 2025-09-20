import os
import requests
from datetime import datetime, timedelta

# def refresh_access_token():
#     """Only call this when you get a 401 error"""
#     url = "https://api.ouraring.com/oauth/token"
#     data = {
#         "grant_type": "refresh_token",
#         "refresh_token": os.getenv("OURA_REFRESH_TOKEN"),
#         "client_id": os.getenv("OURA_CLIENT_ID"),
#         "client_secret": os.getenv("OURA_CLIENT_SECRET")
#     }
    
#     response = requests.post(url, data=data)
#     response.raise_for_status()
#     tokens = response.json()
    
#     # Update GitHub Secrets would require GitHub CLI or API
#     # For now, print new token for manual update
#     print(f"New access token: {tokens['access_token']}")
#     return tokens['access_token']

def refresh_access_token():
    """Only call this when you get a 401 error"""
    url = "https://api.ouraring.com/oauth/token"
    
    # Debug: Check if all required variables are present
    client_id = os.getenv("OURA_CLIENT_ID")
    client_secret = os.getenv("OURA_CLIENT_SECRET") 
    refresh_token = os.getenv("OURA_REFRESH_TOKEN")
    
    print(f"Client ID present: {bool(client_id)}")
    print(f"Client Secret present: {bool(client_secret)}")
    print(f"Refresh Token present: {bool(refresh_token)}")
    
    # Only show first/last few characters for security
    if client_id:
        print(f"Client ID: {client_id[:4]}...{client_id[-4:]}")
    if refresh_token:
        print(f"Refresh Token: {refresh_token[:4]}...{refresh_token[-4:]}")
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }
    
    response = requests.post(url, data=data)
    
    # Debug: Show response details
    print(f"Response status: {response.status_code}")
    print(f"Response text: {response.text}")
    
    response.raise_for_status()
    tokens = response.json()
    
    print(f"New access token: {tokens['access_token']}")
    return tokens['access_token']

def make_api_request(endpoint, auto_refresh=True):
    """Make API request with automatic token refresh on 401"""
    url = f"https://api.ouraring.com/v2/{endpoint}"
    
    # Get access token, or empty string if missing
    access_token = os.getenv('OURA_ACCESS_TOKEN', '')
    
    # If no access token, refresh immediately
    if not access_token and auto_refresh:
        print("No access token found, refreshing...")
        access_token = refresh_access_token()
        os.environ['OURA_ACCESS_TOKEN'] = access_token
    
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 401 and auto_refresh:
        # Token expired, refresh and retry
        new_token = refresh_access_token()
        # Update the environment variable for this session
        os.environ['OURA_ACCESS_TOKEN'] = new_token
        headers["Authorization"] = f"Bearer {new_token}"
        response = requests.get(url, headers=headers)
    
    response.raise_for_status()
    return response.json()

def get_recent_sleep_hrv(days=7):
    """Get HRV data for recent days"""
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)
    
    endpoint = f"usercollection/sleep?start_date={start_date}&end_date={end_date}"
    print(f"Fetching sleep data from {start_date} to {end_date}")
    
    return make_api_request(endpoint)