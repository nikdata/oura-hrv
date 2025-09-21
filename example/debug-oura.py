#!/usr/bin/env python3
"""
Oura Sleep Data Debug Script
Debug script to check sleep data availability and timing issues
"""

import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def refresh_access_token():
    """Refresh the access token using refresh token"""
    client_id = os.getenv('OURA_CLIENT_ID')
    client_secret = os.getenv('OURA_CLIENT_SECRET')
    refresh_token = os.getenv('OURA_REFRESH_TOKEN')
    
    if not all([client_id, client_secret, refresh_token]):
        print("❌ Missing OAuth credentials in .env file")
        return None
    
    token_url = "https://api.ouraring.com/oauth/token"
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': client_id,
        'client_secret': client_secret
    }
    
    try:
        response = requests.post(token_url, data=data)
        if response.status_code == 200:
            token_data = response.json()
            # Update environment for this session
            os.environ['OURA_ACCESS_TOKEN'] = token_data['access_token']
            print("✅ Access token refreshed successfully")
            return token_data['access_token']
        else:
            print(f"❌ Token refresh failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"❌ Token refresh error: {e}")
        return None

def make_api_request(endpoint):
    """Make authenticated API request with auto-refresh"""
    access_token = os.getenv('OURA_ACCESS_TOKEN')
    base_url = "https://api.ouraring.com/v2"
    
    headers = {'Authorization': f'Bearer {access_token}'}
    url = f"{base_url}/{endpoint}"
    
    try:
        response = requests.get(url, headers=headers)
        
        # If unauthorized, try refreshing token
        if response.status_code == 401:
            print("🔄 Access token expired, refreshing...")
            new_token = refresh_access_token()
            if new_token:
                headers = {'Authorization': f'Bearer {new_token}'}
                response = requests.get(url, headers=headers)
            else:
                return None
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"❌ API request failed: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ API request error: {e}")
        return None

def get_sleep_data(start_date, end_date):
    """Get sleep data for date range"""
    endpoint = f"usercollection/sleep?start_date={start_date}&end_date={end_date}"
    return make_api_request(endpoint)

def analyze_sleep_session(sleep_session):
    """Analyze a single sleep session for HRV data"""
    session_id = sleep_session.get('id', 'Unknown')
    day = sleep_session.get('day', 'Unknown')
    average_hrv = sleep_session.get('average_hrv')
    
    print(f"\n📊 Sleep Session Analysis for {day}")
    print(f"   Session ID: {session_id}")
    print(f"   Average HRV: {average_hrv}")
    
    # Check HRV time series
    hrv_data = sleep_session.get('hrv', {})
    if hrv_data:
        items = hrv_data.get('items', [])
        timestamp = hrv_data.get('timestamp')
        interval = hrv_data.get('interval', 300)  # Default 5 minutes
        
        # Filter out None values
        valid_hrv_readings = [x for x in items if x is not None and x > 0]
        
        print(f"   HRV Time Series:")
        print(f"     - Start time: {timestamp}")
        print(f"     - Interval: {interval} seconds ({interval//60} minutes)")
        print(f"     - Total readings: {len(items)}")
        print(f"     - Valid readings: {len(valid_hrv_readings)}")
        print(f"     - None/invalid readings: {len(items) - len(valid_hrv_readings)}")
        
        if valid_hrv_readings:
            print(f"     - HRV range: {min(valid_hrv_readings):.1f} - {max(valid_hrv_readings):.1f} ms")
            print(f"     - First few readings: {valid_hrv_readings[:5]}")
        else:
            print("     - ⚠️  No valid HRV readings found!")
    else:
        print("   ⚠️  No HRV time series data available")
    
    # Check other metrics
    bedtime = sleep_session.get('bedtime')
    if bedtime:
        print(f"   Bedtime: {bedtime}")
    
    total_sleep = sleep_session.get('total_sleep_duration')
    if total_sleep:
        hours = total_sleep // 3600
        minutes = (total_sleep % 3600) // 60
        print(f"   Total sleep: {hours}h {minutes}m ({total_sleep} seconds)")

def main():
    """Main debugging function"""
    print("🔍 Oura Sleep Data Debug Script")
    print("=" * 50)
    
    # Check environment variables
    required_vars = ['OURA_CLIENT_ID', 'OURA_CLIENT_SECRET', 'OURA_ACCESS_TOKEN', 'OURA_REFRESH_TOKEN']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing environment variables: {', '.join(missing_vars)}")
        print("   Make sure your .env file contains all required OAuth tokens")
        return
    
    print("✅ All environment variables found")
    
    # Calculate date ranges
    days_past = 10
    today = (datetime.now()+ timedelta(days=1)).strftime('%Y-%m-%d') 
    today2 = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    three_days_ago = (datetime.now() - timedelta(days=days_past)).strftime('%Y-%m-%d')
    
    print(f"📅 Date ranges:")
    print(f"   Today: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"   Yesterday: {yesterday}")
    print(f"   {days_past} days ago: {three_days_ago}")
    print(f"   Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S CDT')}")
    
    # Get recent sleep data (last 3 days)
    print(f"\n🌙 Fetching sleep data from {three_days_ago} to {today}...")
    sleep_data = get_sleep_data(three_days_ago, today)
    
    if not sleep_data:
        print("❌ Failed to retrieve sleep data")
        return
    
    sleep_sessions = sleep_data.get('data', [])
    print(f"✅ Retrieved {len(sleep_sessions)} sleep sessions")
    
    if not sleep_sessions:
        print("⚠️  No sleep sessions found in the last 3 days")
        return
    
    # Analyze each session
    print("\n" + "=" * 50)
    print("SLEEP SESSION ANALYSIS")
    print("=" * 50)
    
    sessions_by_date = {}
    for session in sleep_sessions:
        date = session.get('day', 'Unknown')
        sessions_by_date[date] = session
        analyze_sleep_session(session)
    
    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    
    print(f"📈 Sessions found:")
    for date in sorted(sessions_by_date.keys(), reverse=True):
        session = sessions_by_date[date]
        avg_hrv = session.get('average_hrv', 'N/A')
        hrv_items = session.get('hrv', {}).get('items', [])
        valid_hrv = len([x for x in hrv_items if x is not None and x > 0])
        
        status = "✅" if date == today and avg_hrv else "⏳" if date == today else "✅"
        print(f"   {status} {date}: Avg HRV = {avg_hrv}, Time series = {valid_hrv} readings")
    
    # Today-specific check
    if today in sessions_by_date:
        print(f"\n🎯 Today's data ({today2}) IS available!")
        print("   Your automation should be working correctly.")
    else:
        print(f"\n⚠️  Today's data ({today2}) is NOT yet available.")
        print("   Oura may still be processing your sleep session.")
        
        # Check if yesterday is available
        if yesterday in sessions_by_date:
            print(f"✅ Yesterday's data ({yesterday}) is available, so your API connection works fine.")
        
    print(f"\n💡 Next steps:")
    print("   - If today's data is missing, try again in 1-2 hours")
    print("   - Your GitHub Actions automation will catch it on the next run")
    # print("   - Consider running automation every 2-3 hours for faster pickup")

if __name__ == "__main__":
    main()