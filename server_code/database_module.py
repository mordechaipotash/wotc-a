import anvil.server
import psycopg2
from psycopg2 import sql
import anvil.secrets

# Import extraction modules
from extract_lastname_module import extract_applicant_names
from extract_ssn_module import extract_social_security_number

# Load environment variables
DB_URL = anvil.secrets.get_secret('DB_URL')

def get_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(DB_URL)
        return conn
    except psycopg2.Error as e:
        anvil.server.raise_error(f"Database connection failed: {e}")

@anvil.server.callable
def fetch_all_emails():
    """Fetches all processed emails from the 'emails' table."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT email_id, subject, from_email, to_email, processed_at, attachment_count, total_pdf_pages
            FROM emails
            WHERE processed = TRUE
            ORDER BY processed_at DESC;
        """)
        emails = cursor.fetchall()
        # Transform into list of dictionaries for easier handling in frontend
        email_list = []
        for email in emails:
            email_dict = {
                'email_id': email[0],
                'subject': email[1],
                'from_email': email[2],
                'to_email': email[3],
                'processed_at': email[4],
                'attachment_count': email[5],
                'total_pdf_pages': email[6]
            }
            email_list.append(email_dict)
        return email_list
    except psycopg2.Error as e:
        anvil.server.raise_error(f"Error fetching emails: {e}")
    finally:
        cursor.close()
        conn.close()

@anvil.server.callable
def fetch_email_details(email_id):
    """Fetches detailed information for a specific email."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT *
            FROM emails
            WHERE email_id = %s;
        """, (email_id,))
        email = cursor.fetchone()
        if not email:
            return None
        # Map columns to values
        columns = [desc[0] for desc in cursor.description]
        email_dict = dict(zip(columns, email))
        # Fetch attachments
        cursor.execute("""
            SELECT filename, content_type, size, public_url
            FROM attachments
            WHERE email_id = %s;
        """, (email_id,))
        attachments = cursor.fetchall()
        attachment_list = []
        for attachment in attachments:
            attachment_dict = {
                'filename': attachment[0],
                'content_type': attachment[1],
                'size': attachment[2],
                'public_url': attachment[3]
            }
            attachment_list.append(attachment_dict)
        email_dict['attachments'] = attachment_list
        return email_dict
    except psycopg2.Error as e:
        anvil.server.raise_error(f"Error fetching email details: {e}")
    finally:
        cursor.close()
        conn.close()

@anvil.server.callable
def trigger_recalculation(email_id):
    """Triggers the recalculation process for a specific email."""
    try:
        # Call extraction functions
        result_names = extract_applicant_names(email_id)
        result_ssn = extract_social_security_number(email_id)
        
        if result_names['status'] == 'success' and result_ssn['status'] == 'success':
            return {
                "status": "success",
                "message": f"Recalculation completed for email {email_id}."
            }
        else:
            error_messages = []
            if result_names['status'] != 'success':
                error_messages.append(result_names['message'])
            if result_ssn['status'] != 'success':
                error_messages.append(result_ssn['message'])
            return {
                "status": "error",
                "message": "; ".join(error_messages)
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}