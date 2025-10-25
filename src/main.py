#!/usr/bin/env python3
"""
Main script for processing Oura HRV data
Designed to run in GitHub Actions when triggered by webhook or schedule
"""
import os
from dotenv import load_dotenv
from oura_client import get_recent_sleep_hrv
# from data_processor import extract_hrv_timeseries, save_nightly_hrv_files
from data_processor import save_all_sleep_metrics

days_to_get = 10

def main():
    # Load environment variables (for local testing)
    load_dotenv()
    
    # Validate required environment variables
    required_vars = ['OURA_CLIENT_ID', 'OURA_CLIENT_SECRET', 'OURA_REFRESH_TOKEN']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        raise ValueError(f"Missing required environment variables: {missing_vars}")
    
    try:
        # Get recent sleep data (last days_to_get days to catch any missed data)
        print("Fetching recent sleep HRV data from Oura...")
        sleep_data = get_recent_sleep_hrv(days=days_to_get)
        
        if not sleep_data.get('data'):
            print("No sleep data returned from Oura API")
            return
        
        print(f"Retrieved {len(sleep_data['data'])} sleep sessions")
        
        # Save each night to individual files
        print("Processing and saving HRV data...")
        # files_created = save_nightly_hrv_files(sleep_data)
        files_created = save_all_sleep_metrics(sleep_data)
        
        if files_created:
            print(f"✅ Successfully processed {len(files_created)} files:")
            for file in files_created:
                print(f"  - {file}")
        else:
            print("✅ No new files created (data up to date)")
        
    except Exception as e:
        print(f"❌ Error processing HRV data: {e}")
        raise

if __name__ == "__main__":
    main()