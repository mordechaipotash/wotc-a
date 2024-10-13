import os
import base64
import logging
from datetime import datetime, timezone
import psycopg2
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from supabase import create_client, Client
from dotenv import load_dotenv
import re
from urllib.parse import urljoin
import pytz
import email

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
BUCKET_NAME = "attachments"
DB_URL = os.getenv("DB_URL")

# Gmail API configuration
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
script_dir = os.path.dirname(os.path.abspath(__file__))
credentials_path = os.path.join(script_dir, 'credentials.json')

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def authenticate_gmail():
    creds = None
    token_path = os.path.join(script_dir, 'token.json')
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

def sanitize_filename(filename):
    return re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)

def upload_attachment(file_name, file_content):
    try:
        response = supabase.storage.from_(BUCKET_NAME).upload(file_name, file_content)
        logger.info(f"Successfully uploaded file: {file_name}")
        return file_name
    except Exception as e:
        logger.error(f"Error uploading file {file_name}: {str(e)}")
        return None

def get_attachment_url(file_name):
    return urljoin(f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/", file_name)

def process_email(service, message_id):
    message = service.users().messages().get(userId='me', id=message_id, format='full').execute()
    
    utc_time = datetime.fromtimestamp(int(message['internalDate'])/1000, timezone.utc)
    est_timezone = pytz.timezone('America/New_York')
    est_time = utc_time.astimezone(est_timezone)
    
    email_data = {
        'email_id': message['id'],
        'thread_id': message['threadId'],
        'message_id': None,
        'subject': None,
        'snippet': message.get('snippet', ''),
        'from_name': None,
        'from_email': None,
        'sender': None,
        'to_email': None,
        'date': None,
        'internal_date': est_time,
        'body': None,
        'body_text': None,
        'body_html': None,
        'message_link': f"https://mail.google.com/mail/u/0/#inbox/{message['id']}",
        'is_sent': 'SENT' in message.get('labelIds', []),
        'processed': True,
        'processed_at': datetime.now(timezone.utc),
    }

    headers = message['payload']['headers']
    for header in headers:
        name = header['name'].lower()
        if name == 'subject':
            email_data['subject'] = header['value']
        elif name == 'from':
            from_parts = email.utils.parseaddr(header['value'])
            email_data['from_name'] = from_parts[0]
            email_data['from_email'] = from_parts[1]
            email_data['sender'] = header['value']
        elif name == 'to':
            email_data['to_email'] = header['value']
        elif name == 'date':
            email_data['date'] = header['value']
        elif name == 'message-id':
            email_data['message_id'] = header['value']

    email_data['body_text'] = ''
    email_data['body_html'] = ''
    if 'parts' in message['payload']:
        for part in message['payload']['parts']:
            if part['mimeType'] == 'text/plain':
                email_data['body_text'] = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
            elif part['mimeType'] == 'text/html':
                email_data['body_html'] = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
    elif 'body' in message['payload']:
        if message['payload']['mimeType'] == 'text/plain':
            email_data['body_text'] = base64.urlsafe_b64decode(message['payload']['body']['data']).decode('utf-8')
        elif message['payload']['mimeType'] == 'text/html':
            email_data['body_html'] = base64.urlsafe_b64decode(message['payload']['body']['data']).decode('utf-8')

    email_data['body'] = email_data['body_text'] or email_data['body_html']

    attachments = []
    if 'parts' in message['payload']:
        for part in message['payload']['parts']:
            if 'filename' in part and part['filename']:
                if 'body' in part and 'attachmentId' in part['body']:
                    attachment = service.users().messages().attachments().get(
                        userId='me', messageId=message_id, id=part['body']['attachmentId']
                    ).execute()
                    file_data = base64.urlsafe_b64decode(attachment['data'])
                    original_filename, file_extension = os.path.splitext(part['filename'])
                    sanitized_filename = sanitize_filename(original_filename)
                    new_filename = f"{sanitized_filename}_{est_time.strftime('%Y%m%d_%H%M%S')}{file_extension}"
                    uploaded_name = upload_attachment(new_filename, file_data)
                    if uploaded_name:
                        attachments.append({
                            'filename': part['filename'],
                            'content_type': part['mimeType'],
                            'size': int(part['body']['size']),
                            'attachment_id': part['body']['attachmentId'],
                            'storage_path': uploaded_name,
                            'public_url': get_attachment_url(uploaded_name)
                        })
                else:
                    logger.warning(f"Attachment found but no attachmentId for file: {part['filename']}")

    email_data['attachment_count'] = len(attachments)
    return email_data, attachments

def fetch_new_emails(service, start_date):
    query = f'after:{start_date.strftime("%Y/%m/%d")}'
    results = service.users().messages().list(userId='me', q=query).execute()
    messages = results.get('messages', [])
    while 'nextPageToken' in results:
        page_token = results['nextPageToken']
        results = service.users().messages().list(userId='me', q=query, pageToken=page_token).execute()
        messages.extend(results.get('messages', []))
    return messages

def upsert_email(conn, email_data):
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO emails (
            email_id, thread_id, message_id, subject, snippet, from_name, from_email,
            sender, to_email, date, internal_date, body, body_text, body_html,
            message_link, is_sent, processed, processed_at, attachment_count
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (email_id) DO UPDATE SET
            thread_id = EXCLUDED.thread_id,
            message_id = EXCLUDED.message_id,
            subject = EXCLUDED.subject,
            snippet = EXCLUDED.snippet,
            from_name = EXCLUDED.from_name,
            from_email = EXCLUDED.from_email,
            sender = EXCLUDED.sender,
            to_email = EXCLUDED.to_email,
            date = EXCLUDED.date,
            internal_date = EXCLUDED.internal_date,
            body = EXCLUDED.body,
            body_text = EXCLUDED.body_text,
            body_html = EXCLUDED.body_html,
            message_link = EXCLUDED.message_link,
            is_sent = EXCLUDED.is_sent,
            processed = EXCLUDED.processed,
            processed_at = EXCLUDED.processed_at,
            attachment_count = EXCLUDED.attachment_count,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """, (
            email_data['email_id'], email_data['thread_id'], email_data['message_id'],
            email_data['subject'], email_data['snippet'], email_data['from_name'],
            email_data['from_email'], email_data['sender'], email_data['to_email'],
            email_data['date'], email_data['internal_date'], email_data['body'],
            email_data['body_text'], email_data['body_html'], email_data['message_link'],
            email_data['is_sent'], email_data['processed'], email_data['processed_at'],
            email_data['attachment_count']
        ))
        email_id = cursor.fetchone()[0]
        conn.commit()
        logger.info(f"Email inserted/updated successfully. ID: {email_id}")
        return email_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Error upserting email: {str(e)}")
        return None
    finally:
        cursor.close()

def insert_attachment(conn, email_id, attachment):
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO attachments (
            email_id, filename, content_type, size, attachment_id, storage_path, public_url
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id
        """, (
            email_id,
            attachment['filename'],
            attachment['content_type'],
            attachment['size'],
            attachment['attachment_id'],
            attachment['storage_path'],
            attachment['public_url']
        ))
        attachment_id = cursor.fetchone()[0]
        conn.commit()
        logger.info(f"Attachment inserted successfully. ID: {attachment_id}")
        return attachment_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Error inserting attachment: {str(e)}")
        return None
    finally:
        cursor.close()

def process_email_batch(gmail_service, conn, message_batch):
    for message in message_batch:
        email_data, attachments = process_email(gmail_service, message['id'])
        email_id = upsert_email(conn, email_data)
        if email_id:
            for attachment in attachments:
                insert_attachment(conn, email_data['email_id'], attachment)

def main():
    conn = psycopg2.connect(DB_URL)
    gmail_service = authenticate_gmail()

    try:
        start_date = datetime(2024, 10, 8, tzinfo=timezone.utc)
        logger.info(f"Fetching emails since: {start_date}")

        messages = fetch_new_emails(gmail_service, start_date)
        logger.info(f"Found {len(messages)} emails to process")

        batch_size = 100
        for i in range(0, len(messages), batch_size):
            batch = messages[i:i+batch_size]
            logger.info(f"Processing batch {i//batch_size + 1}/{(len(messages)-1)//batch_size + 1}")
            try:
                process_email_batch(gmail_service, conn, batch)
            except Exception as e:
                logger.error(f"Error processing batch: {str(e)}")
                # Optionally, you can add more detailed logging here

        logger.info("All emails processed successfully")
    except Exception as e:
        logger.error(f"An error occurred in main: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()