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
    5, 20,  # Increased min connections for better performance
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD
)

# Other configurations
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Queries for 8850 Form
QUERIES = [
    {'Text': 'What is the applicant\'s first name?'},
    {'Text': 'What is the applicant\'s last name?'},
    {'Text': 'What is the social security number?'},
    {'Text': 'What is the street address?'},
    {'Text': 'What is the city?'},
    {'Text': 'What\'s the state?'},
    {'Text': 'What\'s the zip code?'},
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

def get_unprocessed_8850_forms(cursor):
    query = """
    SELECT id, jpg_filename, pdf_filename, form_type
    FROM pdf_pages
    WHERE form_type = '8850 Form' AND textract_form_id IS NULL
    LIMIT 1000
    """
    cursor.execute(query)
    return cursor.fetchall()

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

def extract_relevant_data(response):
    if 'choices' not in response or not response['choices']:
        return {}
    content = response['choices'][0]['message']['content']
    
    # Extract information using regular expressions
    data = {}
    patterns = {
        'first_name': r"What is the applicant's first name\?\s*(.+)",
        'last_name': r"What is the applicant's last name\?\s*(.+)",
        'ssn': r"What is the social security number\?\s*(.+)",
        # Add more patterns for other fields
    }
    
    for key, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            data[key] = match.group(1).strip()
    
    return data

def update_pdf_pages_8850_data(cursor, forms_data):
    query = """
    UPDATE pdf_pages SET
        last_name_8850 = %(last_name)s,
        first_name_8850 = %(first_name)s,
        ssn_8850 = %(ssn)s,
        date_of_birth_8850 = %(dob)s,
        worked_before_8850 = %(worked_before)s,
        snap_benefits_8850 = %(snap_benefits)s,
        snap_3_of_5_months_8850 = %(snap_3_of_5_months)s,
        tanf_welfare_8850 = %(tanf_welfare)s,
        ssi_benefits_8850 = %(ssi_benefits)s,
        unemployed_27_weeks_8850 = %(unemployed_27_weeks)s,
        ticket_to_work_8850 = %(ticket_to_work)s,
        felony_conviction_8850 = %(felony_conviction)s,
        veteran_8850 = %(veteran)s,
        signature_8850 = %(signature)s,
        date_signed_8850 = %(date_signed)s,
        lastname_gemini_8850 = %(lastname_gemini)s,
        firstname_gemini_8850 = %(firstname_gemini)s,
        dob_gemini_8850 = %(dob_gemini)s,
        street1_gemini_8850 = %(street1_gemini)s,
        city_gemini_8850 = %(city_gemini)s,
        state_gemini_8850 = %(state_gemini)s,
        zip_gemini_8850 = %(zip_gemini)s,
        date_signed_gemini_8850 = %(date_signed_gemini)s,
        form_8850_is_signed = %(is_signed)s,
        form_8850_signature_name = %(signature_name)s,
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

def process_8850_forms():
    conn = connection_pool.getconn()
    try:
        with conn.cursor() as cursor:
            while True:
                unprocessed_forms = get_unprocessed_8850_forms(cursor)
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
                                'last_name': extracted_data.get('What is the applicant\'s last name?', {}).get('Text', '')[:100],
                                'first_name': extracted_data.get('What is the applicant\'s first name?', {}).get('Text', '')[:100],
                                'ssn': extracted_data.get('What is the social security number?', {}).get('Text', '')[:11],
                                'dob': parser.parse(extracted_data.get('What is the date of birth of the applicant?', {}).get('Text', '')).date() if extracted_data.get('What is the date of birth of the applicant?', {}).get('Text') else None,
                                'worked_before': extracted_data.get('Has the applicant worked for this employer before? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'snap_benefits': extracted_data.get('Is the applicant receiving SNAP benefits? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'snap_3_of_5_months': extracted_data.get('Has the applicant received SNAP benefits for 3 of the last 5 months? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'tanf_welfare': extracted_data.get('Is the applicant receiving TANF assistance? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'ssi_benefits': extracted_data.get('Is the applicant receiving SSI benefits? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'unemployed_27_weeks': extracted_data.get('Has the applicant been unemployed for 27 weeks or more? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'ticket_to_work': extracted_data.get('Is the applicant participating in a ticket to work program? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'felony_conviction': extracted_data.get('Has the applicant been convicted of a felony? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'veteran': extracted_data.get('Is the applicant a veteran? (Yes/No)', {}).get('Text', '').lower() == 'yes',
                                'signature': extracted_data.get('What\'s the signature name?', {}).get('Text', '')[:100],
                                'date_signed': parser.parse(extracted_data.get('What\'s the date the form was signed?', {}).get('Text', '')).date() if extracted_data.get('What\'s the date the form was signed?', {}).get('Text') else None,
                                'lastname_gemini': extracted_data.get('What is the applicant\'s last name?', {}).get('Text', '')[:255],
                                'firstname_gemini': extracted_data.get('What is the applicant\'s first name?', {}).get('Text', '')[:100],
                                'dob_gemini': extracted_data.get('What is the date of birth of the applicant?', {}).get('Text', '')[:100],
                                'street1_gemini': extracted_data.get('What is the street address?', {}).get('Text', '')[:100],
                                'city_gemini': extracted_data.get('What is the city?', {}).get('Text', '')[:100],
                                'state_gemini': extracted_data.get('What\'s the state?', {}).get('Text', '')[:100],
                                'zip_gemini': extracted_data.get('What\'s the zip code?', {}).get('Text', '')[:100],
                                'date_signed_gemini': extracted_data.get('What\'s the date the form was signed?', {}).get('Text', '')[:100],
                                'is_signed': extracted_data.get('Is the form signed?', {}).get('Text', '').lower() == 'yes',
                                'signature_name': extracted_data.get('What\'s the signature name?', {}).get('Text', '')[:100],
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
        logger.error(f"Error in process_8850_forms: {str(e)}")
        conn.rollback()
    finally:
        connection_pool.putconn(conn)

def update_database(cursor, forms_data):
    try:
        logger.info(f"Attempting to update {len(forms_data)} forms in the database.")
        cursor.execute("SAVEPOINT before_update")
        rows_affected = update_pdf_pages_8850_data(cursor, forms_data)
        logger.info(f"Updated {rows_affected} rows in the database.")
        cursor.execute("RELEASE SAVEPOINT before_update")
        cursor.connection.commit()
        logger.info(f"Successfully committed changes for {len(forms_data)} forms")
    except Exception as e:
        cursor.execute("ROLLBACK TO SAVEPOINT before_update")
        logger.error(f"Error updating data for forms: {str(e)}")

if __name__ == "__main__":
    logger.info("Starting 8850 Form processing...")
    process_8850_forms()
    logger.info("8850 Form processing completed")