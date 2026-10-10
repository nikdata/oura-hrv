# -----------------------------------------------------------------------
# data_processor.py — HRV, RHR, sleep times and readiness extraction
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
# Sleep times:
#   - Bedtime start/end and total sleep duration for overnight sleep.
#   - Only "long_sleep" sessions count as overnight sleep. Naps ("sleep",
#     "late_nap") and false detections ("rest") are skipped by the callers
#     using is_overnight_sleep(), so a nap never overwrites a night's files.
#
# Readiness:
#   - Just the day and the readiness score from the daily_readiness doc.
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


def is_overnight_sleep(sleep_doc):
    """True if this sleep session is overnight sleep, not a nap.

    Oura marks sleep longer than 3 hours as "long_sleep". Everything else
    ("sleep", "late_nap", "rest", "deleted") is a nap or not real sleep.
    """
    return sleep_doc.get('type') == 'long_sleep'


def extract_sleep_times(sleep_doc):
    """Extract bedtime, wake time and total sleep from one sleep session.

    Oura's timestamps include the local offset where the ring was worn
    (e.g. "2026-10-09T23:12:00-05:00"), so the readable times are local
    to wherever you slept.
    """
    total_sleep = sleep_doc.get('total_sleep_duration')
    start = sleep_doc.get('bedtime_start')
    end = sleep_doc.get('bedtime_end')

    if total_sleep is None or not start or not end:
        return []

    start_time = datetime.fromisoformat(start.replace('Z', '+00:00'))
    end_time = datetime.fromisoformat(end.replace('Z', '+00:00'))

    return [{
        'day': sleep_doc['day'],
        'bedtime_start': int(start_time.timestamp()),
        'bedtime_start_readable': start_time.strftime("%Y-%m-%d %H:%M:%S"),
        'bedtime_end': int(end_time.timestamp()),
        'bedtime_end_readable': end_time.strftime("%Y-%m-%d %H:%M:%S"),
        'timezone_offset': end_time.strftime("%z"),
        'total_sleep_seconds': total_sleep,
        'total_sleep_hours': round(total_sleep / 3600, 2),
        'time_in_bed_seconds': sleep_doc.get('time_in_bed'),
        'source': 'oura_ring_sleep',
        'measurement_type': 'long_sleep'
    }]


def process_sleep_times(sleep_doc):
    """Process a sleep session into sleep-times JSON.

    Returns (sleep_json_bytes, sleep_date). Bytes is None if the session
    has no sleep duration or bedtimes.
    """
    entries = extract_sleep_times(sleep_doc)
    sleep_bytes = json.dumps(entries, indent=2).encode('utf-8') if entries else None
    return sleep_bytes, sleep_doc['day']


def process_readiness_document(readiness_doc):
    """Process a daily_readiness doc into readiness JSON.

    Only the day and score are kept. Returns (readiness_json_bytes, day).
    Bytes is None if Oura hasn't calculated a score yet (score is null).
    """
    day = readiness_doc['day']
    score = readiness_doc.get('score')

    if score is None:
        return None, day

    entries = [{
        'day': day,
        'readiness': score,
        'source': 'oura_ring_readiness',
        'measurement_type': 'readiness_score'
    }]
    return json.dumps(entries, indent=2).encode('utf-8'), day
