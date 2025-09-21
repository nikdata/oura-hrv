# Oura HRV to Apple Health Automation Pipeline

A fully automated system that transfers detailed Heart Rate Variability (HRV) time series data from your Oura Ring to Apple Health.

> [!IMPORTANT]  
> **Why This Matters**: The Oura Ring captures superior sleep HRV data (80-96 readings per night) using rMSSD measurements, but doesn't sync this rich dataset to Apple Health. This automation bridges that gap, making detailed HRV data available for any health apps that integrate with Apple Health.

## ✨ Key Features

- **Rich Time Series Data**: Captures 5-minute interval HRV readings throughout sleep (vs single daily averages)
- **Fully Automated**: Zero-maintenance pipeline with self-updating OAuth tokens
- **Travel & DST Proof**: Automatic timezone handling without manual adjustments  
- **Duplicate Prevention**: Smart file-based system prevents data overwrites
- **Apple Health Ready**: Optimized JSON format for seamless Apple Shortcuts integration
- **GitHub Actions Powered**: Free, reliable cloud automation (no servers needed)
- **Real Sleep Focus**: Uses rMSSD measurements during sleep (superior to general SDNN)

## 🔬 Technical Background

### HRV Measurement Differences
- **Oura Ring**: Uses **rMSSD** (Root Mean Square of Successive Differences) - optimized for sleep analysis
- **Apple Watch**: Uses **SDNN** (Standard Deviation of NN intervals) - general purpose measurement
- **Key Insight**: Oura's sleep-focused rMSSD provides more consistent and meaningful data for recovery tracking

> [!NOTE]  
> These are different mathematical approaches to HRV calculation. Oura values are typically higher and not directly comparable to Apple Watch readings. However, I think using Oura's consistent measurement over time provides superior insights into recovery patterns.
> Learn more about the differences [here](https://www.spikeapi.com/blog/understanding-hrv-metrics-a-deep-dive-into-sdnn-and-rmssd).

### Data Flow Architecture
```
Oura Ring → Sleep Processing → GitHub Actions (Multiple Daily Runs) → 
Individual JSON Files → Apple Shortcuts (Daily 8 AM) → Apple Health → 
Your Health Apps
```

## 🚀 Quick Start

### Prerequisites
- Oura Ring Gen 4 with active subscription
    - I don't have a Gen 3 ring, so I can't be sure if this works for that model
- iPhone with iOS 26+ (for Apple Shortcuts)
- GitHub account (free tier sufficient)
- An active Oura subscription
- You must sync your ring after waking up through the Oura app for the data to make it to Oura Cloud. It can take up to 2 hours for the Oura Cloud to have your data available through the API.

> [!NOTE]
> I have found that my sleep data is available within a minute of syncing via the app.

### 1. Oura API Application Setup

1. Visit [Oura Cloud API Console](https://cloud.ouraring.com/v2/docs)
2. Create a new application with these settings:
   - **Display name**: `My HRV Sync` (or any name)
   - **Redirect URI**: `https://example.com/callback`
   - **Scopes**: Check "Allow server-side authentication"
   - **Authentication types**: Check both server-side and client-side
3. Save your **Client ID** and **Client Secret**

### 2. Repository Setup

1. Fork or clone this repository
2. Go to **Settings** → **Secrets and variables** → **Actions**
3. Add these repository secrets:
   ```
   OURA_CLIENT_ID=your_client_id_here
   OURA_CLIENT_SECRET=your_client_secret_here
   ```

### 3. OAuth Token Generation

1. Create a `.env` file locally with your credentials:
   ```bash
   OURA_CLIENT_ID=your_client_id
   OURA_CLIENT_SECRET=your_client_secret
   ```
   You can refer to the `.env.example` as a template

2. Run the OAuth setup script:
   ```bash
   python oura.py  # Run line by line up to line 115
   ```
   > [!IMPORTANT]
   > Run the file oura.py line by line; otherwise you may experience errors. You will have to replace values.

3. Visit the authorization URL provided, authorize the app
4. Copy the `code` parameter from the callback URL
5. Complete the token exchange and save the tokens to GitHub Secrets:
   ```
   OURA_REFRESH_TOKEN=your_refresh_token_here
   ```

### 4. GitHub Actions Configuration

1. Create a Personal Access Token (classic) with full repo access
2. Add it as a repository secret:
   ```
   REPO_SECRETS_TOKEN=your_github_pat_here
   ```
3. Enable Actions permissions: **Settings** → **Actions** → **General** → **Workflow permissions** → **Read and write permissions**

### 5. Apple Shortcuts Setup

Create a new Shortcut with these 8 actions:

1. **Get Current Date** → Current date
2. **Format Date** → Custom format: `yyyy-MM-dd`
3. **Text** → `https://raw.githubusercontent.com/yourusername/oura-hrv/main/data/[Formatted Date]_sleep-hrv.json`
4. **Get Contents of URL** → Enable "Allow Untrusted Hosts"
5. **If** → Contains "hrv" (error detection)
6. **Get Dictionary from Input** → Parse JSON (inside If)
7. **Repeat with Each** → Loop through entries (inside If)
8. **Log Health Sample** → Settings:
   - **Sample Type**: Heart Rate Variability RMSSD
   - **Quantity**: `Item from Repeat` → `hrv`
   - **Unit**: Milliseconds
   - **Date**: `Item from Repeat` → `date_readable`
   - **Source Name**: "Oura Ring"

Set automation to run daily at 8:00 AM (or whatever time you prefer) with "Ask Before Running" OFF.

## 📊 Data Format

Each night's data is saved as an individual JSON file (`YYYY-MM-DD_sleep-hrv.json`):

```json
[
  {
    "date": 1726817400,
    "date_readable": "2025-09-20 06:30:00",
    "timezone": "CDT", 
    "timezone_offset": "-0500",
    "hrv": 42.5,
    "unit": "ms",
    "source": "oura_ring_rmssd",
    "measurement_type": "rMSSD"
  }
]
```

### File Structure
```
data/
├── 2025-09-18_sleep-hrv.json  # 87 HRV readings
├── 2025-09-19_sleep-hrv.json  # 92 HRV readings  
└── 2025-09-20_sleep-hrv.json  # 84 HRV readings
```

## ⚙️ Automation Schedule

The system runs multiple times daily (using GitHub Actions) to ensure reliable data capture:

```yaml
# GitHub Actions Schedule (All times CDT)
- 5:45 AM, 7:45 AM     # Early morning capture
- 9:45 AM, 11:45 AM    # Standard processing window  
- 1:45 PM, 3:45 PM     # Afternoon backup
- 6:45 PM              # Evening final attempt
```

**Why Multiple Runs?**
- Handles variable sleep schedules and wake times
- Accommodates Oura's processing delays (2-8 hours post wake-up)
- Prevents failures from complex sleep patterns or travel
- Zero duplicates due to smart file-based deduplication

## 🔧 Key Components

### Core Python Modules

#### `oura_client.py`
- OAuth2 token management with automatic refresh
- GitHub Secrets auto-updating (self-sustaining automation)
- API request handling with 401 error recovery
- Fetches sleep data with configurable date ranges

#### `data_processor.py` 
- Extracts HRV time series from Oura sleep sessions
- Filters invalid values (None, 0, negative readings)
- Generates timezone-aware timestamps for travel
- Creates individual files per night (prevents duplicates)

#### `main.py`
- Orchestrates the complete data pipeline
- Environment validation and error handling
- Status reporting and logging

### GitHub Actions Workflow

- **File**: `.github/workflows/process-hrv.yml`
- **Triggers**: Scheduled runs + manual triggers + webhook-ready
- **Self-updating**: Manages its own OAuth token rotation
- **Zero maintenance**: Fully autonomous operation

## 🛠️ Local Development & Testing

### Debug Script
Use the included debug script to troubleshoot timing or data issues:

```bash
python debug-oura.py
```

This provides detailed analysis of:
- OAuth token status
- Recent sleep sessions and HRV availability  
- Data processing timing insights
- API connectivity validation

### Manual Testing
```bash
# Install dependencies
pip install requests python-dotenv python-dateutil PyNaCl

# Set up .env file with your tokens
# Run the main pipeline
python main.py
```

## 🎯 Integration with Health Apps

This automation makes detailed Oura HRV data available in Apple Health, where it can be used by any app that integrates with Apple Health HRV data:

### Benefits for Health Apps
- **High-frequency sleep HRV data** (vs single daily readings from most devices)
- **Consistent rMSSD measurements** throughout sleep  
- **Automatic daily data availability** by 10-11 AM

### Compatible Apps
Any app that uses Apple Health HRV data will benefit from this richer dataset:
- HRV4Training
- EliteHRV  
- Welltory
- Bevel
- Custom health dashboards
- Any app using HealthKit HRV data

## 🔍 Troubleshooting

### Common Issues

**"No data available today"**
- Normal if you woke up very early or had disrupted sleep
- Oura may need 2-8 hours to process complex sleep patterns
- The automation will catch it on the next run

**Apple Shortcuts failing**
- Check "Allow Untrusted Hosts" is enabled
- Verify the GitHub repository URL is correct
- Ensure the shortcut has Health app permissions

**GitHub Actions not running**
- Verify repository secrets are set correctly
- Check Actions tab for error messages
- Confirm workflow permissions are set to "Read and write"

### OAuth Token Issues
The system automatically handles token refresh, but if you see persistent auth errors:
1. Re-run the OAuth setup process
2. Update the `OURA_REFRESH_TOKEN` secret
3. The next automation run will self-correct

## 📈 Benefits Over Default Oura Integration

| Feature | Oura App → Apple Health | This Automation |
|---------|------------------------|-----------------|
| HRV Frequency | None (no sync) | 80-96 readings/night |
| Measurement Type | N/A | rMSSD (sleep-optimized) |
| Data Richness | N/A | 5-minute intervals |
| Automation | Manual only | Fully automatic |
| Travel Handling | N/A | Automatic timezone |
| Historical Data | N/A | Complete archive |

## ✌️ Random Notes

This project uses a functional programming approach with minimal classes and exception handling. When contributing:

- Use functional style (see existing code patterns)
- Test with the debug script before submitting PRs
- Follow the individual file per night architecture
- Maintain backwards compatibility with Apple Shortcuts format