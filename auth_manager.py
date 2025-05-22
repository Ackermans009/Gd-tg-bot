import os
import json
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GOOGLE_SCOPES, TOKEN_STORAGE_FILE
from logger_config import logger

# In-memory store for flow objects, mapping state to flow
# For a deployed app, this might need a more persistent or shared store if using multiple workers
active_flows = {}

def get_google_auth_flow():
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )
    return flow

def get_auth_url(user_id: int):
    flow = get_google_auth_flow()
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent' # Force consent to get a refresh token every time
    )
    active_flows[str(user_id)] = {'flow': flow, 'state': state} # Store flow with user_id as key
    logger.info(f"Generated auth URL for user {user_id} with state {state}")
    return authorization_url

def exchange_code_for_credentials(user_id: int, code: str):
    user_flow_data = active_flows.get(str(user_id))
    if not user_flow_data:
        logger.error(f"No active OAuth flow found for user {user_id} to exchange code.")
        return None
    
    flow = user_flow_data['flow']
    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
        save_user_credentials(user_id, credentials)
        if str(user_id) in active_flows: # Clean up after successful exchange
            del active_flows[str(user_id)]
        return credentials
    except Exception as e:
        logger.error(f"Error exchanging code for user {user_id}: {e}")
        if str(user_id) in active_flows:
            del active_flows[str(user_id)]
        return None

def save_user_credentials(user_id: int, credentials):
    all_tokens = {}
    if os.path.exists(TOKEN_STORAGE_FILE):
        try:
            with open(TOKEN_STORAGE_FILE, 'r') as f:
                all_tokens = json.load(f)
        except json.JSONDecodeError:
            logger.warning(f"Token storage file {TOKEN_STORAGE_FILE} is corrupted. Starting fresh.")
            all_tokens = {}

    all_tokens[str(user_id)] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    with open(TOKEN_STORAGE_FILE, 'w') as f:
        json.dump(all_tokens, f)
    logger.info(f"Saved credentials for user {user_id}")

def load_user_credentials(user_id: int):
    if not os.path.exists(TOKEN_STORAGE_FILE):
        return None
    try:
        with open(TOKEN_STORAGE_FILE, 'r') as f:
            all_tokens = json.load(f)
    except json.JSONDecodeError:
        logger.error(f"Could not decode tokens from {TOKEN_STORAGE_FILE}")
        return None

    user_creds_dict = all_tokens.get(str(user_id))
    if not user_creds_dict:
        return None

    credentials = Credentials(**user_creds_dict)
    # Refresh token if expired
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            save_user_credentials(user_id, credentials) # Save refreshed credentials
            logger.info(f"Refreshed credentials for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to refresh credentials for user {user_id}: {e}")
            # Potentially delete invalid credentials or notify user
            delete_user_credentials(user_id)
            return None
    return credentials

def delete_user_credentials(user_id: int):
    all_tokens = {}
    if os.path.exists(TOKEN_STORAGE_FILE):
        with open(TOKEN_STORAGE_FILE, 'r') as f:
            all_tokens = json.load(f)
    
    if str(user_id) in all_tokens:
        del all_tokens[str(user_id)]
        with open(TOKEN_STORAGE_FILE, 'w') as f:
            json.dump(all_tokens, f)
        logger.info(f"Deleted credentials for user {user_id}")
        return True
    return False

# This part is for handling the redirect from Google (if your bot is a web service)
# For a simple bot (e.g., Colab), user might paste the code or full redirect URL.
# This example doesn't include a web server for the redirect.
# You'd typically have a small Flask/FastAPI app on Render/Koyeb for this.
# Example:
# from flask import Flask, request, redirect
# app = Flask(__name__)
# @app.route('/oauth2callback')
# def oauth2callback():
#     state = request.args.get('state')
#     code = request.args.get('code')
#     # Here you would find the user_id associated with the state,
#     # then call exchange_code_for_credentials(user_id, code)
#     # Then notify the user in Telegram
#     return "Authentication successful! You can close this tab."
