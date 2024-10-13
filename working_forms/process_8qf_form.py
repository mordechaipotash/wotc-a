import os
import logging
import json
import psycopg2
from psycopg2 import pool
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
import requests
from datetime import datetime
import time
from dateutil import parser
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
import base64
import re

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database configuration
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')

# Create a connection pool
connection_pool = psycopg2.pool.ThreadedConnectionPool(
    5, 20,
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD
)

# Other configurations
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Queries for 8QF Form
QUERIES = [
    {'Text': 'What is the applicant\'s first name?'},
    {'Text': 'What is the applicant\'s last name?'},
    {'Text': 'What is the social security number?'},
    {'Text': 'What is the date of birth of the applicant?'},
    {'Text': 'Has the applicant worked for this employer before? (Yes/No)'},
    {'Text': 'Is the applicant receiving SNAP benefits? (Yes/No)'},
    {'Text': 'Has the applicant received SNAP benefits for 3 of the last 5 months? (Yes/No)'},
    {'Text': 'Is the applicant receiving TANF assistance? (Yes/No)'},
    {'Text': 'Is the applicant receiving SSI benefits? (Yes/No)'},
    {'Text': 'Has the applicant been unemployed for 27 weeks or more? (Yes/No)'},
    {'Text': 'Is the applicant participating in a ticket to work program? (Yes/No)'},
    {'Text': 'Has the applicant been convicted of a felony? (Yes/No)'},
    {'Text': 'Is the applicant a veteran? (Yes/No)'},
    {'Text': 'Is the form signed?'},
    {'Text': 'What\'s the signature name?'},
    {'Text': 'What\'s the date the form was signed?'}
]

def get_unprocessed_8qf_forms(cursor):
    query = """
    SELECT id, jpg_filename, pdf_filename, form_type
    FROM pdf_pages
    WHERE form_type = '8 Question Form'
      AND (textract_form_id IS NULL OR processed_for_matching_8qf IS NULL OR processed_for_matching_8qf = FALSE)
    LIMIT 1000
    """
    cursor.execute(query)
    results = cursor.fetchall()
    logger.info(f"Found {len(results)} unprocessed 8 Question Forms")
    return results

def analyze_document_with_adapter(image_bytes):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "google/gemini-flash-1.5",
        "messages": [
            {"role": "user", "content": "Extract the following information from the image:"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('utf-8')}"}}]},
            {"role": "user", "content": json.dumps(QUERIES)}
        ],
        "max_tokens": 1000
    }
    try:
        with requests.Session() as session:
            response = session.post(OPENROUTER_URL, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
    except requests.RequestException as e:
        logger.error(f"Error in API request: {str(e)}")
        return None

def parse_date(date_string):
    if not date_string:
        return None
    try:
        # Remove any non-alphanumeric characters from the beginning of the string
        cleaned_date = re.sub(r'^[^a-zA-Z0-9]+', '', date_string)
        
        # Try parsing with different formats
        for fmt in ('%m/%d/%Y', '%m-%d-%Y', '%m/%d/%y', '%m-%d-%y', '%Y-%m-%d'):
            try:
                return datetime.strptime(cleaned_date, fmt).date()
            except ValueError:
                continue
        
        # If all formats fail, try a more flexible approach
        return parser.parse(cleaned_date).date()
    except Exception as e:
        logger.warning(f"Unable to parse date: {date_string}. Error: {str(e)}")
        return None

def extract_relevant_data(response):
    if 'choices' not in response or not response['choices']:
        return {}
    content = response['choices'][0]['message']['content']
    
    data = {}
    patterns = {
        'first_name': r"What is the applicant's first name\?\s*(.+)",
        'last_name': r"What is the applicant's last name\?\s*(.+)",
        'ssn': r"What is the social security number\?\s*(.+)",
        'dob': r"What is the date of birth of the applicant\?\s*(.+)",
        'worked_before': r"Has the applicant worked for this employer before\?\s*(Yes|No)",
        'snap_benefits': r"Is the applicant receiving SNAP benefits\?\s*(Yes|No)",
        'snap_3_of_5_months': r"Has the applicant received SNAP benefits for 3 of the last 5 months\?\s*(Yes|No)",
        'tanf_welfare': r"Is the applicant receiving TANF assistance\?\s*(Yes|No)",
        'ssi_benefits': r"Is the applicant receiving SSI benefits\?\s*(Yes|No)",
        'unemployed_27_weeks': r"Has the applicant been unemployed for 27 weeks or more\?\s*(Yes|No)",
        'ticket_to_work': r"Is the applicant participating in a ticket to work program\?\s*(Yes|No)",
        'felony_conviction': r"Has the applicant been convicted of a felony\?\s*(Yes|No)",
        'veteran': r"Is the applicant a veteran\?\s*(Yes|No)",
        'is_signed': r"Is the form signed\?\s*(Yes|No)",
        'signature_name': r"What's the signature name\?\s*(.+)",
        'date_signed': r"What's the date the form was signed\?\s*(.+)"
    }
    
    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            data[key] = match.group(1).strip()
    
    return data

def update_pdf_pages_8qf_data(cursor, forms_data):
    query = """
    UPDATE pdf_pages SET
        last_name_8qf = %(last_name)s,
        first_name_8qf = %(first_name)s,
        ssn_8qf = %(ssn)s,
        date_of_birth_8qf = %(dob)s,
        worked_before_8qf = %(worked_before)s,
        snap_benefits_8qf = %(snap_benefits)s,
        snap_3_of_5_months_8qf = %(snap_3_of_5_months)s,
        tanf_welfare_8qf = %(tanf_welfare)s,
        ssi_benefits_8qf = %(ssi_benefits)s,
        unemployed_27_weeks_8qf = %(unemployed_27_weeks)s,
        ticket_to_work_8qf = %(ticket_to_work)s,
        felony_conviction_8qf = %(felony_conviction)s,
        veteran_8qf = %(veteran)s,
        signature_8qf = %(signature)s,
        date_signed_8qf = %(date_signed)s,
        lastname_gemini_8qf = %(lastname_gemini)s,
        firstname_gemini_8qf = %(firstname_gemini)s,
        dob_gemini_8qf = %(dob_gemini)s,
        processed_for_matching_8qf = TRUE,
        textract_form_id = %(textract_form_id)s
    WHERE id = %(id)s
    """
    cursor.executemany(query, forms_data)
    return cursor.rowcount

def process_single_form(form):
    form_id, jpg_filename, _, _ = form
    textract_form_id = f"TEXTRACT-{form_id}-{int(time.time())}"
    
    logger.info(f"Processing form {form_id}")
    try:
        image_url = f"https://pbuqlylgktjdhjqkvwnv.supabase.co/storage/v1/object/public/jpgs/{jpg_filename}"
        with requests.Session() as session:
            response = session.get(image_url)
            if response.status_code == 200:
                image_bytes = response.content
                api_response = analyze_document_with_adapter(image_bytes)
                if api_response:
                    extracted_data = extract_relevant_data(api_response)
                    return form_id, extracted_data, textract_form_id
                else:
                    logger.error(f"Failed to analyze document for form {form_id}")
            else:
                logger.error(f"Failed to download image for form {form_id}: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Unexpected error processing form {form_id}: {str(e)}")
    
    return None

def update_database(cursor, forms_data):
    try:
        logger.info(f"Attempting to update {len(forms_data)} forms in the database.")
        cursor.execute("SAVEPOINT before_update")
        rows_affected = update_pdf_pages_8qf_data(cursor, forms_data)
        logger.info(f"Updated {rows_affected} rows in the database.")
        cursor.execute("RELEASE SAVEPOINT before_update")
        cursor.connection.commit()
        logger.info(f"Successfully committed changes for {len(forms_data)} forms")
    except Exception as e:
        cursor.execute("ROLLBACK TO SAVEPOINT before_update")
        logger.error(f"Error updating data for forms: {str(e)}")

def process_8qf_forms():
    conn = connection_pool.getconn()
    try:
        with conn.cursor() as cursor:
            while True:
                unprocessed_forms = get_unprocessed_8qf_forms(cursor)
                if not unprocessed_forms:
                    logger.info("No more unprocessed forms found.")
                    break

                logger.info(f"Found {len(unprocessed_forms)} unprocessed forms.")

                with ThreadPoolExecutor(max_workers=multiprocessing.cpu_count() * 2) as executor:
                    futures = [executor.submit(process_single_form, form) for form in unprocessed_forms]
                    forms_data = []
                    for future in as_completed(futures):
                        result = future.result()
                        if result:
                            form_id, extracted_data, textract_form_id = result
                            forms_data.append({
                                'id': form_id,
                                'last_name': extracted_data.get('last_name', '')[:100],
                                'first_name': extracted_data.get('first_name', '')[:100],
                                'ssn': extracted_data.get('ssn', '')[:11],
                                'dob': parse_date(extracted_data.get('dob')),
                                'worked_before': extracted_data.get('worked_before', '').lower() == 'yes',
                                'snap_benefits': extracted_data.get('snap_benefits', '').lower() == 'yes',
                                'snap_3_of_5_months': extracted_data.get('snap_3_of_5_months', '').lower() == 'yes',
                                'tanf_welfare': extracted_data.get('tanf_welfare', '').lower() == 'yes',
                                'ssi_benefits': extracted_data.get('ssi_benefits', '').lower() == 'yes',
                                'unemployed_27_weeks': extracted_data.get('unemployed_27_weeks', '').lower() == 'yes',
                                'ticket_to_work': extracted_data.get('ticket_to_work', '').lower() == 'yes',
                                'felony_conviction': extracted_data.get('felony_conviction', '').lower() == 'yes',
                                'veteran': extracted_data.get('veteran', '').lower() == 'yes',
                                'signature': extracted_data.get('signature_name', '')[:100],
                                'date_signed': parse_date(extracted_data.get('date_signed')),
                                'lastname_gemini': extracted_data.get('last_name', '')[:100],
                                'firstname_gemini': extracted_data.get('first_name', '')[:100],
                                'dob_gemini': extracted_data.get('dob', '')[:100],
                                'textract_form_id': textract_form_id
                            })
                            logger.info(f"Prepared data for form {form_id}")
                            
                            # Update database every 50 records
                            if len(forms_data) >= 50:
                                update_database(cursor, forms_data)
                                forms_data = []  # Reset the list after update
                        else:
                            logger.warning(f"Failed to process a form")
                    
                    # Update any remaining records
                    if forms_data:
                        update_database(cursor, forms_data)
                
                logger.info(f"Completed processing batch of {len(unprocessed_forms)} forms")

    except Exception as e:
        logger.error(f"Error in process_8qf_forms: {str(e)}")
        conn.rollback()
    finally:
        connection_pool.putconn(conn)

if __name__ == "__main__":
    logger.info("Starting 8 Question Form processing...")
    process_8qf_forms()
    logger.info("8 Question Form processing completed")