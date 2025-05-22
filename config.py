import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0)) # Default to 0 if not set

# File size thresholds
LARGE_FILE_THRESHOLD_MB = int(os.getenv("LARGE_FILE_THRESHOLD_MB", 50))
LARGE_FILE_THRESHOLD_BYTES = LARGE_FILE_THRESHOLD_MB * 1024 * 1024
MAX_FILE_SIZE_TG_BYTES = int(os.getenv("MAX_FILE_SIZE_TG_MB", 2000)) * 1024 * 1024

# Google OAuth Scopes
GOOGLE_SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Temporary storage for downloads
DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# Simple file-based token storage (for demonstration; use a proper DB in production)
TOKEN_STORAGE_FILE = "user_tokens.json"
