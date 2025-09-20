import json
from datetime import datetime, timedelta
from pathlib import Path
import dateutil.parser

def extract_hrv_timeseries(sleep_data):
    """Extract time series HRV data for Apple Health"""
    hrv_entries = []
    
    for night in sleep_data['data']:
        if night['hrv'] and night['hrv']['items']:
            # Parse the starting timestamp
            start_time = dateutil.parser.parse(night['hrv']['timestamp'])
            interval_seconds = int(night['hrv']['interval'])  # 300 seconds = 5 minutes
            
            # Process each HRV reading
            for i, hrv_value in enumerate(night['hrv']['items']):
                # Filter out None, 0, and invalid values
                if hrv_value is not None and hrv_value != 0 and hrv_value > 0:
                    measurement_time = start_time + timedelta(seconds=i * interval_seconds)
                    
                    # Convert to Apple Shortcuts friendly format + debugging info
                    # Keep original timezone from Oura (handles travel & DST automatically)
                    unix_timestamp = int(measurement_time.timestamp())
                    human_readable = measurement_time.strftime("%Y-%m-%d %H:%M:%S")
                    timezone_name = measurement_time.strftime("%Z")  # Oura's timezone
                    timezone_offset = measurement_time.strftime("%z")  # Oura's offset
                    
                    hrv_entries.append({
                        'date': unix_timestamp,
                        'date_readable': human_readable,
                        'timezone': timezone_name,
                        'timezone_offset': timezone_offset,
                        'hrv': hrv_value,
                        'unit': 'ms',
                        'source': 'oura_ring_rmssd',
                        'measurement_type': 'rMSSD'
                    })
    
    return hrv_entries

def save_nightly_hrv_files(sleep_data):
    """Save each night's HRV data to separate files with YYYY-MM-DD_sleep-hrv.json format"""
    Path("data").mkdir(exist_ok=True)
    files_created = []
    
    for night in sleep_data['data']:
        # Extract date from the sleep session
        night_date = night['day']  # Format: "2025-09-20"
        
        # Process HRV for this night only
        night_hrv = extract_hrv_timeseries({'data': [night]})
        
        if night_hrv:
            filename = f"data/{night_date}_sleep-hrv.json"
            
            # Check if file already exists and has the same data
            if Path(filename).exists():
                with open(filename, 'r') as f:
                    existing_data = json.load(f)
                if existing_data == night_hrv:
                    print(f"Skipping {filename} - data unchanged")
                    continue
            
            # Save individual night file
            with open(filename, 'w') as f:
                json.dump(night_hrv, f, indent=2)
            
            files_created.append(filename)
            print(f"Saved {len(night_hrv)} HRV readings to {filename}")
        else:
            print(f"No valid HRV data found for {night_date} (all values filtered out)")
    
    return files_created

# def cleanup_old_files(days_to_keep=30):
#     """Remove HRV files older than specified days"""
#     data_dir = Path("data")
#     if not data_dir.exists():
#         return
    
#     cutoff_date = datetime.now().date() - timedelta(days=days_to_keep)
#     files_removed = []
    
#     for file_path in data_dir.glob("*_sleep-hrv.json"):
#         # Extract date from filename: YYYY-MM-DD_sleep-hrv.json
#         try:
#             date_str = file_path.stem.split('_')[0]  # Get YYYY-MM-DD part
#             file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            
#             if file_date < cutoff_date:
#                 file_path.unlink()
#                 files_removed.append(str(file_path))
#                 print(f"Removed old file: {file_path}")
#         except (ValueError, IndexError):
#             # Skip files that don't match expected format
#             continue
    
#     if files_removed:
#         print(f"Cleaned up {len(files_removed)} old files")
#     else:
#         print("No old files to clean up")