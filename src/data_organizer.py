#!/usr/bin/env python3
"""
Data Organization Script for Oura HRV Data

This script moves data files from data/ into organized folders:
- Before: data/2025-10-24_sleep-hrv.json
- After:  data/hrv/2025/10/2025-10-24_sleep-hrv.json

Runs daily via GitHub Actions to keep files organized.

Usage:
    python src/data_organizer.py           # Actually move files
    python src/data_organizer.py --dry-run # Show what would happen without moving
"""

import sys
import shutil
from pathlib import Path


def print_stats(moved, skipped, already_exists):
    """
    Print a summary of what was done.

    Args:
        moved: Number of files successfully moved
        skipped: Number of files skipped (wrong format)
        already_exists: Number of files already in correct location
    """
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Files moved:           {moved}")
    print(f"Already organized:     {already_exists}")
    print(f"Skipped (wrong name):  {skipped}")
    print("=" * 50)


def organize_files(dry_run=False):
    """
    Move data files from data/ root into organized year/month folders.

    For each file like "2025-10-24_sleep-hrv.json":
    1. Extract the date parts (year=2025, month=10)
    2. Extract the metric type (hrv or rhr)
    3. Create folder: data/hrv/2025/10/
    4. Move file into that folder

    Args:
        dry_run: If True, show what would be done without actually moving files
    """

    data_dir = Path('data')

    # Get all JSON files in the data/ folder (not in subfolders)
    files_in_root = list(data_dir.glob('*.json'))

    if not files_in_root:
        print("No files to organize - everything is already in folders!")
        return

    # Keep track of what happens
    moved_count = 0
    skipped_count = 0
    already_exists_count = 0

    if dry_run:
        print("DRY RUN MODE - No files will be moved\n")

    print(f"Found {len(files_in_root)} file(s) to organize\n")

    # Process each file
    for file_path in files_in_root:
        filename = file_path.name

        # Skip files that don't match our expected pattern
        # Expected: YYYY-MM-DD_sleep-hrv.json or YYYY-MM-DD_sleep-rhr.json
        if '_sleep-' not in filename:
            print(f"Skipping {filename} - doesn't match expected pattern")
            skipped_count += 1
            continue

        # Extract parts from filename
        # Example: "2025-10-24_sleep-hrv.json" -> ["2025-10-24", "sleep-hrv.json"]
        date_part = filename.split('_')[0]  # "2025-10-24"

        # Split date into year, month, day
        year, month, day = date_part.split('-')  # ["2025", "10", "24"]

        # Determine metric type (hrv or rhr)
        if '_sleep-hrv.json' in filename:
            metric_type = 'hrv'
        elif '_sleep-rhr.json' in filename:
            metric_type = 'rhr'
        else:
            print(f"Skipping {filename} - unknown metric type")
            skipped_count += 1
            continue

        # Create the target folder path: data/hrv/2025/10/
        target_folder = data_dir / metric_type / year / month

        # Determine where the file should go
        target_path = target_folder / filename

        # Check if file already exists at destination
        if target_path.exists():
            print(f"Already exists: {filename} in {metric_type}/{year}/{month}/")
            already_exists_count += 1
            continue

        # Move the file (or just show what would happen)
        if dry_run:
            print(f"[DRY RUN] Would move: {filename} -> {metric_type}/{year}/{month}/")
            moved_count += 1
        else:
            # Create the folder if it doesn't exist yet
            target_folder.mkdir(parents=True, exist_ok=True)

            # Actually move the file
            shutil.move(str(file_path), str(target_path))
            print(f"Moved: {filename} -> {metric_type}/{year}/{month}/")
            moved_count += 1

    # Print summary of what was done
    print_stats(moved_count, skipped_count, already_exists_count)


if __name__ == "__main__":
    # This runs when you execute: python src/data_organizer.py

    # Check if --dry-run was passed as a command line argument
    # sys.argv is a list of arguments: ['src/data_organizer.py', '--dry-run']
    is_dry_run = '--dry-run' in sys.argv

    organize_files(dry_run=is_dry_run)
