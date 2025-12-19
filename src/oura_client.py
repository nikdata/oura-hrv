import requests
import os
import base64
from nacl import encoding, public

# define the source number of days I want to get
days_to_get = 3

def update_github_secret(secret_name, secret_value, repo_owner, repo_name, github_token):
    """Update a GitHub repository secret"""
    
    # Step 1: Get repository public key
    headers = {
        'Authorization': f'token {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    key_url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/actions/secrets/public-key'
    key_response = requests.get(key_url, headers=headers)
    key_response.raise_for_status()
    
    public_key_data = key_response.json()
    public_key = public_key_data['key']
    key_id = public_key_data['key_id']
    
    # Step 2: Encrypt the secret value
    public_key_bytes = base64.b64decode(public_key)
    sealed_box = public.SealedBox(public.PublicKey(public_key_bytes))
    encrypted_value = sealed_box.encrypt(secret_value.encode('utf-8'))
    encrypted_value_b64 = base64.b64encode(encrypted_value).decode('utf-8')
    
    # Step 3: Update the secret
    secret_url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/actions/secrets/{secret_name}'
    secret_data = {
        'encrypted_value': encrypted_value_b64,
        'key_id': key_id
    }
    
    secret_response = requests.put(secret_url, json=secret_data, headers=headers)
    secret_response.raise_for_status()
    
    print(f"✅ Updated GitHub secret: {secret_name}")

def refresh_access_token():
    """Refresh tokens and update GitHub secrets automatically"""
    url = "https://api.ouraring.com/oauth/token"
    
    refresh_token = os.getenv("OURA_REFRESH_TOKEN")
    client_id = os.getenv("OURA_CLIENT_ID")
    client_secret = os.getenv("OURA_CLIENT_SECRET")
    
    # Validate we have all required credentials
    if not refresh_token:
        raise ValueError("OURA_REFRESH_TOKEN is missing from environment variables")
    if not client_id:
        raise ValueError("OURA_CLIENT_ID is missing from environment variables")
    if not client_secret:
        raise ValueError("OURA_CLIENT_SECRET is missing from environment variables")
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret
    }
    
    try:
        response = requests.post(url, data=data)
        response.raise_for_status()
        tokens = response.json()
    except requests.exceptions.HTTPError as e:
        # Get detailed error information from Oura API
        error_details = ""
        try:
            error_body = response.json()
            error_details = f"\nOura API Error: {error_body}"
        except:
            error_details = f"\nResponse Text: {response.text}"
        
        # Provide specific guidance based on status code
        if response.status_code == 400:
            raise RuntimeError(
                f"❌ OAuth token refresh failed with 400 Bad Request{error_details}\n\n"
                "This usually means your refresh token is invalid or expired.\n"
                "To fix this, you need to re-authorize the application:\n\n"
                "1. Run the OAuth flow locally to get a new refresh token\n"
                "2. Update the OURA_REFRESH_TOKEN secret in GitHub\n"
                "3. See project documentation for detailed OAuth setup steps\n\n"
                f"Current refresh token (first 10 chars): {refresh_token[:10]}..."
            ) from e
        elif response.status_code == 401:
            raise RuntimeError(
                f"❌ OAuth token refresh failed with 401 Unauthorized{error_details}\n\n"
                "This usually means your client credentials are incorrect.\n"
                "Verify these GitHub Secrets are correct:\n"
                "- OURA_CLIENT_ID\n"
                "- OURA_CLIENT_SECRET"
            ) from e
        else:
            raise RuntimeError(
                f"❌ OAuth token refresh failed with status {response.status_code}{error_details}"
            ) from e
    
    # Update environment for current session
    new_access_token = tokens['access_token']
    os.environ['OURA_ACCESS_TOKEN'] = new_access_token
    
    # If we got a new refresh token, update GitHub secrets
    if 'refresh_token' in tokens:
        new_refresh_token = tokens['refresh_token']
        os.environ['OURA_REFRESH_TOKEN'] = new_refresh_token
        
        # Update GitHub secrets (only in GitHub Actions environment)
        github_token = os.getenv('REPO_SECRETS_TOKEN')
        if github_token:
            try:
                # Extract repo info from GitHub environment
                github_repository = os.getenv('GITHUB_REPOSITORY')  # format: owner/repo
                if github_repository:
                    repo_owner, repo_name = github_repository.split('/')
                    
                    # Update both secrets
                    update_github_secret('OURA_ACCESS_TOKEN', new_access_token, repo_owner, repo_name, github_token)
                    update_github_secret('OURA_REFRESH_TOKEN', new_refresh_token, repo_owner, repo_name, github_token)
                    
                    print("🔄 GitHub secrets updated successfully")
                else:
                    print("⚠️ Not running in GitHub Actions, skipping secret update")
            except Exception as e:
                print(f"⚠️ Failed to update GitHub secrets: {e}")
                # Continue anyway - don't fail the whole process
    
    print(f"New access token: {new_access_token}")
    return new_access_token

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

def get_recent_sleep_hrv(days=days_to_get):
    """Get recent sleep HRV data from Oura"""
    from datetime import datetime, timedelta
    
    end_date = datetime.now().date() + timedelta(days=1) # adding 1 day to ensure current day data is accounted for
    start_date = end_date - timedelta(days=days)
    
    print(f"Fetching sleep data from {start_date} to {end_date}")
    endpoint = f"usercollection/sleep?start_date={start_date}&end_date={end_date}"
    return make_api_request(endpoint)