import os
import io
import time
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError
from telegram import InputFile

from logger_config import logger
from config import DOWNLOAD_DIR, MAX_FILE_SIZE_TG_BYTES
from gdrive_handler import get_drive_service # To get service with credentials

# Helper to format size
def format_bytes(size):
    if size is None: return "0 B"
    # 2**10 = 1024
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"


async def download_gdrive_file(file_id: str, file_name: str, file_size: int, credentials, progress_callback=None):
    service = get_drive_service(credentials)
    file_path = os.path.join(DOWNLOAD_DIR, file_name) # Use original name for saving

    # Ensure filename is safe for the filesystem
    safe_file_name = "".join(c if c.isalnum() or c in ('.', '_', '-') else '_' for c in file_name)
    if not safe_file_name: # Handle cases where name becomes empty
        safe_file_name = file_id # Use file_id as a fallback
    file_path = os.path.join(DOWNLOAD_DIR, safe_file_name)


    logger.info(f"Starting download for: {file_name} (ID: {file_id}) to {file_path}. Size: {format_bytes(file_size)}")
    
    request = service.files().get_media(fileId=file_id)
    
    # Check if file already exists and is complete (simple check, can be improved)
    if os.path.exists(file_path) and os.path.getsize(file_path) == file_size and file_size > 0:
        logger.info(f"File {file_name} already exists and seems complete. Skipping download.")
        return file_path

    try:
        with io.FileIO(file_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=1024*1024*5) # 5MB chunks
            done = False
            downloaded_percentage = 0
            last_reported_time = time.time()

            while not done:
                status, done = downloader.next_chunk()
                if status:
                    current_progress = int(status.progress() * 100)
                    if current_progress > downloaded_percentage: # Report if changed
                        downloaded_percentage = current_progress
                        # Throttle progress updates (e.g., every 2 seconds or 5% change)
                        if progress_callback and (time.time() - last_reported_time > 2 or downloaded_percentage % 5 == 0):
                            await progress_callback(file_name, downloaded_percentage, file_size)
                            last_reported_time = time.time()
            
            if progress_callback: # Final progress
                 await progress_callback(file_name, 100, file_size, is_final=True)
            logger.info(f"Successfully downloaded {file_name} to {file_path}")
            return file_path
    except HttpError as error:
        logger.error(f"An API error occurred during download of {file_name}: {error}")
        if os.path.exists(file_path): os.remove(file_path) # Clean up partial download
        raise ConnectionError(f"Google Drive API error: {error.resp.status} - {error.details}") from error
    except Exception as e:
        logger.error(f"An unexpected error occurred during download of {file_name}: {e}")
        if os.path.exists(file_path): os.remove(file_path) # Clean up
        raise IOError(f"File system or download error: {e}") from e


async def upload_to_telegram(bot, chat_id: int, file_path: str, caption: str, original_filename: str, progress_callback_telegram=None):
    file_size = os.path.getsize(file_path)
    logger.info(f"Starting upload of {original_filename} ({format_bytes(file_size)}) to chat {chat_id}")

    if file_size > MAX_FILE_SIZE_TG_BYTES:
        logger.warning(f"File {original_filename} ({format_bytes(file_size)}) exceeds Telegram's limit of {format_bytes(MAX_FILE_SIZE_TG_BYTES)}. Skipping.")
        await bot.send_message(chat_id, f"⚠️ File '{original_filename}' ({format_bytes(file_size)}) is too large for Telegram (max {format_bytes(MAX_FILE_SIZE_TG_BYTES)}) and was skipped.")
        return False

    try:
        with open(file_path, 'rb') as f:
            # For very large files, you might need to use `InputFile` with streaming if supported well,
            # or ensure your bot has enough memory. `python-telegram-bot` handles this reasonably.
            # The `filename` argument in `send_document` ensures the original name is used in Telegram.
            
            # Note: python-telegram-bot's progress for uploads is not straightforward to implement
            # for send_document directly. It's more of a "send and wait".
            # For true progress, one might need to use lower-level HTTP requests,
            # which adds complexity. For now, we'll just signal start and end.
            if progress_callback_telegram:
                await progress_callback_telegram(original_filename, 0, file_size) # 0% before sending

            await bot.send_document(chat_id=chat_id, document=f, filename=original_filename, caption=caption, connect_timeout=60, read_timeout=120) # Increased timeouts
            
            if progress_callback_telegram:
                await progress_callback_telegram(original_filename, 100, file_size, is_final=True) # 100% after sending
            
            logger.info(f"Successfully uploaded {original_filename} to chat {chat_id}")
        return True
    except Exception as e: # Catch more specific Telegram errors if possible
        logger.error(f"Failed to upload {original_filename} to Telegram: {e}")
        await bot.send_message(chat_id, f"❌ Failed to upload '{original_filename}': {e}")
        return False
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Cleaned up temporary file: {file_path}")
            except Exception as e:
                logger.error(f"Error cleaning up file {file_path}: {e}")
