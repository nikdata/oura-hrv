# -----------------------------------------------------------------------
# data_processor.py — HRV and RHR extraction from Oura sleep documents
# -----------------------------------------------------------------------
# This is the core transform logic — the "crown jewel" of the pipeline.
# It takes raw sleep data from Oura's API and converts it into the JSON
# format that the Apple Shortcut expects.
#
# IMPORTANT: The output shape of these functions must NOT change. The
# Apple Shortcuts parse this exact JSON structure (key names, types,
# array nesting). If you change the output, the Shortcuts will break.
#
# HRV (Heart Rate Variability):
#   - Oura provides HRV as a time series: one reading every 5 minutes
#     during sleep. Each reading is rMSSD in milliseconds.
#   - We output ~80-96 readings per night as a flat JSON array.
#   - rMSSD != SDNN. Apple Watch uses SDNN. These are different metrics
#     and are NOT interconvertible. We write to Apple Health's RMSSD
#     field specifically. Don't try to convert or normalize.
#
# RHR (Resting Heart Rate):
#   - We find the lowest heart rate recorded during sleep and the exact
#     timestamp when it occurred.
#   - If the same lowest value appears multiple times, we use the last
#     occurrence (most likely to be during deep sleep).
#   - Output is a single-element JSON array.
#
# Timezone handling:
#   - Each reading's timezone comes directly from Oura and reflects
#     where the ring was worn that night. This varies if you travel.
#   - We pass it through unchanged — never normalize to a single zone.
# -----------------------------------------------------------------------

import json
from datetime import datetime, timedelta


def extract_hrv_timeseries(sleep_data):
    """Extract time series HRV data for Apple Health.

    Input: sleep_data with a 'data' key containing an array of sleep sessions.
    Output: list of HRV readings, each with timestamp, value, and timezone.
    """
    hrv_entries = []

    for night in sleep_data['data']:
        if night['hrv'] and night['hrv']['items']:
            # Oura provides a start timestamp and an interval (300s = 5 min).
            # Each item in the array is one reading at start + (index * interval).
            start_time = datetime.fromisoformat(night['hrv']['timestamp'].replace('Z', '+00:00'))
            interval_seconds = int(night['hrv']['interval'])

            for i, hrv_value in enumerate(night['hrv']['items']):
                # Oura includes None (no reading) and 0 (invalid) — skip both
                if hrv_value is not None and hrv_value != 0 and hrv_value > 0:
                    measurement_time = start_time + timedelta(seconds=i * interval_seconds)

                    unix_timestamp = int(measurement_time.timestamp())
                    human_readable = measurement_time.strftime("%Y-%m-%d %H:%M:%S")
                    timezone_name = measurement_time.strftime("%Z")
                    timezone_offset = measurement_time.strftime("%z")

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


def extract_rhr_data(sleep_data):
    """Extract RHR (lowest heart rate) data for Apple Health.

    Finds the lowest heart rate during sleep and the actual timestamp
    when it occurred (not midnight, not wake-up time).
    """
    rhr_entries = []

    for night in sleep_data['data']:
        lowest_hr = night.get('lowest_heart_rate')
        night_date = night['day']

        if lowest_hr is not None and lowest_hr > 0:
            hr_data = night.get('heart_rate', {})
            hr_items = hr_data.get('items', [])
            hr_timestamp = hr_data.get('timestamp')

            if hr_items and hr_timestamp:
                start_time_with_tz = datetime.fromisoformat(hr_timestamp.replace('Z', '+00:00'))
                interval_seconds = int(hr_data.get('interval', 300))

                # Walk the heart rate array to find the last index where
                # the value equals the lowest — last occurrence is most
                # likely during deepest sleep
                lowest_index = None
                for i, hr_value in enumerate(hr_items):
                    if hr_value == lowest_hr:
                        lowest_index = i

                if lowest_index is not None:
                    actual_time = start_time_with_tz + timedelta(seconds=lowest_index * interval_seconds)
                    unix_timestamp = int(actual_time.timestamp())
                    human_readable = actual_time.strftime("%Y-%m-%d %H:%M:%S")
                    timezone_name = actual_time.strftime("%Z")
                    timezone_offset = actual_time.strftime("%z")

                    rhr_entries.append({
                        'date': unix_timestamp,
                        'date_readable': human_readable,
                        'timezone': timezone_name,
                        'timezone_offset': timezone_offset,
                        'rhr': lowest_hr,
                        'unit': 'bpm',
                        'source': 'oura_ring_sleep',
                        'measurement_type': 'lowest_heart_rate'
                    })

    return rhr_entries


def process_sleep_document(sleep_doc):
    """Process a single sleep document from a webhook notification.

    The webhook gives us a single sleep document, but our extract_*
    functions expect the list-API format ({"data": [...]}) since that's
    what they were originally written for. We wrap the single doc in
    that format for compatibility.

    Returns (hrv_json_bytes, rhr_json_bytes, sleep_date).
    Either bytes value may be None if no valid data was found.
    """
    # Wrap in the list-API format that extract functions expect
    wrapped = {'data': [sleep_doc]}

    hrv_entries = extract_hrv_timeseries(wrapped)
    rhr_entries = extract_rhr_data(wrapped)

    sleep_date = sleep_doc['day']

    hrv_bytes = json.dumps(hrv_entries, indent=2).encode('utf-8') if hrv_entries else None
    rhr_bytes = json.dumps(rhr_entries, indent=2).encode('utf-8') if rhr_entries else None

    return hrv_bytes, rhr_bytes, sleep_date
