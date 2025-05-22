import re
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials # For type hinting

from logger_config import logger

def get_drive_service(credentials: Credentials = None):
    if credentials:
        return build('drive', 'v3', credentials=credentials, static_discovery=False)
    else:
        # For public files, an API key might be used if preferred,
        # but for direct downloads, it often doesn't require explicit API key in `build`
        # if the file is truly public on the web.
        # However, listing folders etc., benefits from a service object.
        # This bot structure will rely on user credentials for simplicity for non-trivial operations.
        # For truly public files that don't need login, direct HTTP GET might be simpler.
        # This example will primarily focus on authenticated access for robustness.
        # If you MUST access some public metadata without user login,
        # you'd need to configure API key based auth.
        logger.warning("Attempting to get Drive service without credentials for public access.")
        # This might be limited.
        return build('drive', 'v3', developerKey="YOUR_API_KEY_IF_NEEDED_FOR_PUBLIC_ACCESS")


def get_file_id_from_link(drive_link: str):
    # Regex to find file ID from various GDrive link formats
    match = re.search(r'(?:/file/d/|id=)([a-zA-Z0-9_-]+)', drive_link)
    if match:
        return match.group(1)
    # Regex for folder ID
    match = re.search(r'(?:/drive/folders/|folders/)([a-zA-Z0-9_-]+)', drive_link)
    if match:
        return match.group(1) # Returns ID, need to check if it's a folder or file
    return None

async def get_file_metadata(file_id: str, credentials: Credentials = None):
    try:
        service = get_drive_service(credentials)
        file_metadata = service.files().get(fileId=file_id, fields="id, name, mimeType, size, webViewLink, parents").execute()
        return file_metadata
    except HttpError as error:
        logger.error(f"An API error occurred while fetching metadata for {file_id}: {error}")
        if error.resp.status == 401 and credentials: # Unauthorized
            logger.error("Credentials might be invalid or expired.")
            # Consider triggering re-auth or deleting stored creds
        elif error.resp.status == 404: # Not Found
            logger.error(f"File/Folder with ID {file_id} not found.")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching metadata for {file_id}: {e}")
        return None


async def list_files_in_folder_recursive(folder_id: str, credentials: Credentials, current_path=""):
    service = get_drive_service(credentials)
    files_and_folders = []
    page_token = None

    try:
        while True:
            response = service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name, mimeType, size)',
                pageToken=page_token,
                orderBy='folder,name' # Ensures folders come first, then sorted by name
            ).execute()

            for item in response.get('files', []):
                item_path = f"{current_path}/{item['name']}" if current_path else item['name']
                item_details = {
                    'id': item['id'],
                    'name': item['name'],
                    'mimeType': item['mimeType'],
                    'size': int(item.get('size', 0)), # Size might be absent for Google Docs type files
                    'path': item_path,
                    'is_folder': item['mimeType'] == 'application/vnd.google-apps.folder'
                }
                files_and_folders.append(item_details)

                if item_details['is_folder']:
                    # Recursively list files in this subfolder
                    logger.info(f"Entering subfolder: {item_details['path']}")
                    sub_items = await list_files_in_folder_recursive(item['id'], credentials, item_details['path'])
                    files_and_folders.extend(sub_items)
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
        return files_and_folders
    except HttpError as error:
        logger.error(f"API error listing folder {folder_id} ('{current_path}'): {error.resp.status} - {error.details}")
        raise error # Re-raise to be handled by the caller
    except Exception as e:
        logger.error(f"Unexpected error listing folder {folder_id} ('{current_path}'): {e}")
        raise e


async def get_drive_items_from_link(drive_link: str, credentials: Credentials):
    file_id = get_file_id_from_link(drive_link)
    if not file_id:
        logger.warning(f"Could not extract ID from link: {drive_link}")
        return None, "Invalid Google Drive link format."

    initial_metadata = await get_file_metadata(file_id, credentials)
    if not initial_metadata:
        return None, f"Could not fetch metadata for the provided link. It might be private, invalid, or API access issue."

    if initial_metadata['mimeType'] == 'application/vnd.google-apps.folder':
        logger.info(f"Link is a folder: {initial_metadata['name']}. Starting recursive listing.")
        try:
            all_items = await list_files_in_folder_recursive(file_id, credentials, initial_metadata['name'])
            # Filter out folders themselves from the final list to process, we only want files
            files_to_process = [item for item in all_items if not item['is_folder']]
            logger.info(f"Found {len(files_to_process)} files in folder '{initial_metadata['name']}' and its subdirectories.")
            return files_to_process, None
        except Exception as e:
            return None, f"Error processing folder '{initial_metadata['name']}': {e}"
    else:
        # It's a single file
        logger.info(f"Link is a single file: {initial_metadata['name']}")
        file_details = {
            'id': initial_metadata['id'],
            'name': initial_metadata['name'],
            'mimeType': initial_metadata['mimeType'],
            'size': int(initial_metadata.get('size', 0)),
            'path': initial_metadata['name'], # Root path for single file
            'is_folder': False
        }
        # Google Docs, Sheets, Slides don't have a 'size' directly and need to be exported.
        # This basic version might not handle export; it assumes direct downloadable files.
        if "google-apps" in initial_metadata['mimeType'] and not initial_metadata.get('size'):
            logger.warning(f"File '{initial_metadata['name']}' is a Google Workspace document. Direct download size is 0. Export might be needed (not implemented in this basic version).")
            # For simplicity, we'll allow it to proceed, but download might fail or be an empty/link file.
            # A more robust solution would involve service.files().export_media(...)

        return [file_details], None
