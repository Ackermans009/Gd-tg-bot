import asyncio
import re
from telegram import Update, constants
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext

from config import TELEGRAM_BOT_TOKEN, LARGE_FILE_THRESHOLD_BYTES, GOOGLE_REDIRECT_URI, ADMIN_USER_ID
from logger_config import logger
import auth_manager
import gdrive_handler
import file_manager

# Global flag to prevent concurrent processing for a single user
user_processing_locks = {} # chat_id: asyncio.Lock()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"/start command received from {user.username} (ID: {user.id})")
    await update.message.reply_html(
        rf"Hi {user.mention_html()}! I can download public Google Drive files/folders and send them to you.",
        rf"Send me a Google Drive link. For files larger than {file_manager.format_bytes(LARGE_FILE_THRESHOLD_BYTES)}, you might need to /login with Google."
        "\n\n<b>Available commands:</b>"
        "\n/start - Show this welcome message"
        "\n/help - Detailed help"
        "\n/login - Authorize Google Drive access for large files"
        "\n/logout - Revoke Google Drive access"
        "\n/status - Check login status"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/help command received from {update.effective_user.username}")
    help_text = (
        "<b>How to Use the Bot:</b>\n"
        "1. Send any public Google Drive file or folder link.\n"
        "2. The bot will list files (for folders) and then download & upload them sequentially.\n"
        "3. For files larger than configured limit (currently "
        f"{file_manager.format_bytes(LARGE_FILE_THRESHOLD_BYTES)}), "
        "you'll need to grant permission via the /login command.\n"
        "4. All file types are attempted; no filtering is done by extension.\n\n"
        "<b>Google Drive Link Formats:</b>\n"
        "Supports common `drive.google.com/file/d/...` and `drive.google.com/drive/folders/...` links.\n\n"
        "<b>Error Handling:</b>\n"
        "If a file fails, the bot will notify you and skip to the next one.\n\n"
        "<b>Important for Login:</b>\n"
        f"When you use /login, you'll get a Google authorization link. After authorizing, "
        f"Google will redirect you to a URL like `{GOOGLE_REDIRECT_URI}`. "
        "You need to copy the ENTIRE redirected URL (if it contains `code=...`) or just the `code` parameter value from that URL and send it back to me."
        "\n(This is a simplified OAuth flow for bot usage. For deployed bots on Render/Koyeb, the redirect can be handled automatically if a web endpoint is set up for the bot)."
    )
    await update.message.reply_html(help_text)

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"/login command received from user {user_id}")
    auth_url = auth_manager.get_auth_url(user_id)
    if auth_url:
        await update.message.reply_text(
            "Please authorize this bot by visiting the following link:\n"
            f"{auth_url}\n\n"
            "After authorization, Google will redirect you. "
            "Copy the FULL redirected URL (it will look like "
            f"`{GOOGLE_REDIRECT_URI}?state=...&code=...`) OR just the 'code' value from that URL, and send it back to me in the chat."
        )
    else:
        await update.message.reply_text("Could not generate authorization URL. Please contact admin.")

async def logout_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"/logout command received from user {user_id}")
    if auth_manager.delete_user_credentials(user_id):
        await update.message.reply_text("You have been successfully logged out. Your Google Drive credentials have been removed.")
    else:
        await update.message.reply_text("You were not logged in, or no credentials found to remove.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    credentials = auth_manager.load_user_credentials(user_id)
    if credentials and not credentials.expired: # Check if valid and not expired
        await update.message.reply_text(f"You are currently logged in to Google Drive. Access for large files is enabled.")
    elif credentials and credentials.expired:
        await update.message.reply_text(f"Your Google Drive session has expired. Please /login again.")
        auth_manager.delete_user_credentials(user_id) # Clean up expired
    else:
        await update.message.reply_text(f"You are not logged in. Files larger than {file_manager.format_bytes(LARGE_FILE_THRESHOLD_BYTES)} will require login.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    message_text = update.message.text
    logger.info(f"Message from {user.username} (ID: {user.id}): {message_text}")

    # Prevent concurrent processing by the same user
    if chat_id not in user_processing_locks:
        user_processing_locks[chat_id] = asyncio.Lock()
    
    if user_processing_locks[chat_id].locked():
        await update.message.reply_text("I'm currently busy with your previous request. Please wait until it's complete.")
        return

    async with user_processing_locks[chat_id]:
        # Check if message is an OAuth code response
        # A simple check, can be made more robust by checking against the 'state' param if you pass it around
        if message_text.startswith(GOOGLE_REDIRECT_URI) or (len(message_text) > 40 and ("&code=" in message_text or "code=" in message_text.split("?")[1] if "?" in message_text else False)):
            code = None
            if GOOGLE_REDIRECT_URI in message_text: # Full URL pasted
                match = re.search(r'[?&]code=([^&]+)', message_text)
                if match:
                    code = match.group(1)
            elif "code=" in message_text and len(message_text) < 200: # Just the code pasted
                code = message_text.split("code=")[-1].split("&")[0]
            
            if code:
                logger.info(f"Received OAuth code from user {user.id}")
                creds = auth_manager.exchange_code_for_credentials(user.id, code)
                if creds:
                    await update.message.reply_text("Successfully authenticated with Google Drive! You can now process larger files.")
                else:
                    await update.message.reply_text("Failed to authenticate with Google Drive. The code might be invalid or expired. Please try /login again.")
                return
            # else: fall through if it doesn't look like a valid code response but contains the redirect URI (e.g., user just pasted the URI without code)

        # Check for Google Drive links
        drive_link_match = re.search(r'https?://drive\.google\.com/(?:file/d/|drive/folders/|open\?id=)([a-zA-Z0-9_-]+)', message_text)
        if not drive_link_match:
            # If it's not a command, not an auth code, and not a GDrive link
            if update.message.entities and any(e.type == constants.MessageEntityType.BOT_COMMAND for e in update.message.entities):
                 pass # It's a command, handled by CommandHandlers
            else:
                await update.message.reply_text("Please send a valid Google Drive link or use a command. Type /help for instructions.")
            return

        drive_link = message_text # Use the full message text as the link
        status_msg = await update.message.reply_text(f"🔗 Link received. Analyzing...")

        credentials = auth_manager.load_user_credentials(user.id)
        
        files_to_process, error_msg = await gdrive_handler.get_drive_items_from_link(drive_link, credentials)

        if error_msg:
            await status_msg.edit_text(f"Error: {error_msg}")
            # If auth error (401, 403) and not logged in, suggest login
            if ("401" in error_msg or "403" in error_msg or "Credentials" in error_msg) and not credentials:
                 await update.message.reply_text(f"This might be a private file/folder or require higher permissions. Try /login and then send the link again.")
            return
        
        if not files_to_process:
            await status_msg.edit_text("No files found at the provided link or the folder is empty.")
            return

        await status_msg.edit_text(f"Found {len(files_to_process)} file(s) to process. Starting sequential download and upload...")
        
        successful_uploads = 0
        failed_uploads = 0

        for index, file_info in enumerate(files_to_process):
            file_name = file_info['name']
            file_id = file_info['id']
            file_size = file_info['size']
            file_path_in_drive = file_info['path']

            progress_message_text = lambda stage, perc, final_fn, total_s, is_fin: \
                f"{'✅ Done: ' if is_fin and stage=='Uploading' else ('⏳ ' + stage + ': ')} '{final_fn}' ({file_manager.format_bytes(total_s)})" + \
                (f" {perc}%" if not (is_fin and stage=='Uploading') else "") + \
                f"\n(File {index+1}/{len(files_to_process)}: {file_path_in_drive})"

            current_op_msg = await context.bot.send_message(chat_id, f"Preparing to process: '{file_name}'...")

            async def download_progress_updater(current_file_name, percentage, total_size, is_final=False):
                nonlocal current_op_msg
                new_text = progress_message_text("Downloading", percentage, current_file_name, total_size, is_final)
                try:
                    if current_op_msg.text != new_text: # Only edit if text changed
                        await current_op_msg.edit_text(new_text)
                except Exception as e: # e.g., message not modified
                    logger.debug(f"Minor error updating download progress: {e}")
            
            async def upload_progress_updater(current_file_name, percentage, total_size, is_final=False):
                nonlocal current_op_msg
                new_text = progress_message_text("Uploading", percentage, current_file_name, total_size, is_final)
                try:
                     if current_op_msg.text != new_text:
                        await current_op_msg.edit_text(new_text)
                except Exception as e:
                    logger.debug(f"Minor error updating upload progress: {e}")

            try:
                # Check for large file and login status
                if file_size > LARGE_FILE_THRESHOLD_BYTES and not credentials:
                    await current_op_msg.edit_text(
                        f"⚠️ File '{file_name}' ({file_manager.format_bytes(file_size)}) exceeds "
                        f"{file_manager.format_bytes(LARGE_FILE_THRESHOLD_BYTES)} and requires login. "
                        f"Please use /login and then resend the original Drive link.\nSkipping this file."
                    )
                    failed_uploads += 1
                    continue

                # --- Download ---
                if "google-apps" in file_info['mimeType'] and not file_size: # Check for GDocs
                    # This example doesn't implement export for GDocs, Sheets, etc.
                    # They would need service.files().export_media(...)
                    await current_op_msg.edit_text(f"ℹ️ File '{file_name}' is a Google Workspace document type. "
                                               "Direct download might not be the intended file (e.g., it might be a link or small metadata file). "
                                               "Full export for these types is not supported in this version. Attempting standard download. Skipped if empty.")
                    # For simplicity, try to download; if it's 0 bytes or tiny, it'll likely be handled. Or skip here.
                    # For now, let it attempt download. If it's a real issue, user will see.
                
                downloaded_file_path = await file_manager.download_gdrive_file(
                    file_id, file_name, file_size, credentials, download_progress_updater
                )

                if not downloaded_file_path or not os.path.exists(downloaded_file_path) or os.path.getsize(downloaded_file_path) == 0 and file_size > 0 :
                    # Handle cases where download_gdrive_file might return None or empty file for non-error cases (e.g. already exists and skipped)
                    # If download truly failed, it would have raised an exception caught below.
                    # If file_size was 0 (like a GDoc link file), and it downloaded as 0, it's "successful" in that sense.
                    if not os.path.exists(downloaded_file_path) and file_size > 0: # Ensure it's a real failure to get the file
                        logger.error(f"Download of {file_name} reported success but file not found or empty. Path: {downloaded_file_path}")
                        await current_op_msg.edit_text(f"❌ Download of '{file_name}' seemed to complete but the file is missing or empty. Skipping.")
                        failed_uploads +=1
                        continue
                    elif os.path.exists(downloaded_file_path) and os.path.getsize(downloaded_file_path) == 0 and file_size > 0: # Downloaded an empty file when original had size
                        logger.warning(f"Downloaded file '{file_name}' is empty, but original size was {file_manager.format_bytes(file_size)}. Skipping upload of empty file.")
                        await current_op_msg.edit_text(f"⚠️ Downloaded file '{file_name}' is empty. Original size was {file_manager.format_bytes(file_size)}. Skipping upload.")
                        os.remove(downloaded_file_path) # Clean up empty file
                        failed_uploads += 1
                        continue


                # --- Upload ---
                # Caption includes the full path within the Drive folder structure
                upload_caption = f"{file_path_in_drive} ({file_manager.format_bytes(file_size)})"
                if len(upload_caption) > 1024: # Telegram caption limit
                    upload_caption = f"{file_name} ({file_manager.format_bytes(file_size)}) (Path too long)"


                upload_success = await file_manager.upload_to_telegram(
                    context.bot, chat_id, downloaded_file_path, upload_caption, file_name, upload_progress_updater
                )
                
                if upload_success:
                    successful_uploads += 1
                    # The final "Uploaded" message is handled by the progress updater with is_final=True
                    # We can delete current_op_msg as it's served its purpose for this file.
                    # Or let the next file's "Preparing" message overwrite it implicitly if not deleted.
                    # await current_op_msg.delete() # Optional: delete the progress message
                else:
                    failed_uploads += 1
                    # Error message already sent by upload_to_telegram
                    # await current_op_msg.edit_text(f"❌ Failed to upload '{file_name}'. See previous error. Skipping.") # This would overwrite specific error.

            except ConnectionError as e: # Specific for GDrive API issues during download usually
                logger.error(f"A Google Drive connection error occurred processing {file_name}: {e}")
                await current_op_msg.edit_text(f"❌ GDrive Connection Error for '{file_name}': {e}. Skipping.")
                failed_uploads += 1
            except IOError as e: # Specific for local file system issues during download
                logger.error(f"A file system error occurred processing {file_name}: {e}")
                await current_op_msg.edit_text(f"❌ File System Error for '{file_name}': {e}. Skipping.")
                failed_uploads += 1
            except Exception as e:
                logger.error(f"An unexpected error occurred processing file {file_name} (ID: {file_id}): {e}", exc_info=True)
                await current_op_msg.edit_text(f"❌ Unexpected Error for '{file_name}': {e}. Skipping.")
                failed_uploads += 1
            
            await asyncio.sleep(1) # Small delay between files

        final_summary = f"\n--- Processing Complete --- \n✅ Successfully uploaded: {successful_uploads} file(s)\n❌ Failed/Skipped: {failed_uploads} file(s)"
        await context.bot.send_message(chat_id, final_summary)
    # Lock is released when exiting 'async with'

def main():
    if not TELEGRAM_BOT_TOKEN or not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logger.critical("CRITICAL: Telegram Bot Token or Google API credentials are not set in .env file. Exiting.")
        return

    logger.info("Bot starting...")
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Command Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("login", login_command))
    application.add_handler(CommandHandler("logout", logout_command))
    application.add_handler(CommandHandler("status", status_command))

    # Message Handler for Google Drive links and OAuth codes
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Bot polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
