import requests
import os
import base64
from nacl import encoding, public

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
    
    data = {
        "grant_type": "refresh_token",
        "refresh_token": os.getenv("OURA_REFRESH_TOKEN"),
        "client_id": os.getenv("OURA_CLIENT_ID"),
        "client_secret": os.getenv("OURA_CLIENT_SECRET")
    }
    
    response = requests.post(url, data=data)
    response.raise_for_status()
    tokens = response.json()
    
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