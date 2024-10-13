import os
import logging
import json
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
from supabase import create_client, Client
import boto3
import botocore
from botocore.exceptions import ClientError
import requests
from datetime import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Log boto3 and botocore versions
logger.info(f"boto3 version: {boto3.__version__}")
logger.info(f"botocore version: {botocore.__version__}")

# Configuration
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY')
DB_URL = os.getenv('DB_URL')
ADAPTER_ID = '80c2f475d50f'
ADAPTER_VERSION = '1'

# Database configuration
DB_HOST = os.getenv('DB_HOST')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Initialize AWS Textract client
textract = boto3.client('textract', region_name='us-east-1')

# Queries for NYYF_1 Form
QUERIES = [
    {'Text': 'What is the applicant\'s last name?'},
    {'Text': 'What is the applicant\'s first name?'},
    {'Text': 'What is the applicant\'s birth date?'},
    {'Text': 'What is the applicant\'s Social Security Number?'},
    {'Text': 'What is the applicant\'s home address?'},
    {'Text': 'What is the applicant\'s city?'},
    {'Text': 'What is the applicant\'s state?'},
    {'Text': 'What is the applicant\'s zip code?'},
    {'Text': 'Is the applicant currently attending high school?'},
    {'Text': 'Is the applicant currently enrolled in a High School Equivalent program?'},
    {'Text': 'Are any of the 4 statements true?'},
    {'Text': 'Is the applicant 16-17 years old and has parent/guardian permission?'},
    {'Text': 'Does the applicant have working papers?'},
    {'Text': 'Is the applicant 18 to 24 years old?'}
]

def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {str(e)}")
        raise

def get_unprocessed_nyyf_1_forms():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query = """
        SELECT id, jpg_url, pdf_filename, form_type
        FROM pdf_pages
        WHERE form_type = 'NYYF_1' AND textract_form_id IS NULL
        """
        
        cursor.execute(query)
        result = cursor.fetchall()
        
        logger.info(f"Query executed. Raw result count: {len(result)}")
        
        filtered_result = [
            {
                'id': item[0],
                'jpg_url': item[1],
                'pdf_filename': item[2],
                'form_type': item[3]
            }
            for item in result if item[3] == 'NYYF_1'
        ]
        
        logger.info(f"Found {len(filtered_result)} unprocessed NYYF_1 Forms")
        
        if len(filtered_result) == 0:
            logger.warning("No unprocessed NYYF_1 Forms found. This might be unexpected.")
        
        cursor.close()
        conn.close()
        
        return filtered_result
    except Exception as e:
        logger.error(f"Error fetching unprocessed NYYF_1 Forms: {str(e)}")
        return []

def analyze_document_with_adapter(image_bytes):
    try:
        logger.info(f"Sending request to Textract with ADAPTER_ID: {ADAPTER_ID}, ADAPTER_VERSION: {ADAPTER_VERSION}")
        
        query_batches = [QUERIES[i:i + 10] for i in range(0, len(QUERIES), 10)]
        
        all_blocks = []
        for batch in query_batches:
            response = textract.analyze_document(
                Document={'Bytes': image_bytes},
                FeatureTypes=['QUERIES'],
                QueriesConfig={'Queries': batch}
            )
            all_blocks.extend(response['Blocks'])
        
        combined_response = {'Blocks': all_blocks}
        logger.debug(f"Textract response: {json.dumps(combined_response, indent=2)}")
        return combined_response
    except Exception as e:
        logger.error(f"Error in analyze_document_with_adapter: {str(e)}")
        return None

def extract_relevant_data(response):
    extracted_data = {}
    if 'Blocks' not in response:
        logger.warning("No 'Blocks' found in the Textract response")
        return extracted_data

    query_blocks = {block['Id']: block for block in response['Blocks'] if block['BlockType'] == 'QUERY'}
    answer_blocks = {block['Id']: block for block in response['Blocks'] if block['BlockType'] == 'QUERY_RESULT'}

    for block in response['Blocks']:
        if block['BlockType'] == 'QUERY':
            query_text = block['Query']['Text']
            if 'Relationships' in block:
                for relationship in block['Relationships']:
                    if relationship['Type'] == 'ANSWER':
                        for answer_id in relationship['Ids']:
                            if answer_id in answer_blocks:
                                answer_block = answer_blocks[answer_id]
                                extracted_data[query_text] = {
                                    'Text': answer_block.get('Text', ''),
                                    'Confidence': answer_block.get('Confidence', 0)
                                }
                                break
                        else:
                            logger.warning(f"No matching QUERY_RESULT block found for query: {query_text}")
                            extracted_data[query_text] = {'Text': 'NOT_FOUND', 'Confidence': 0}
            else:
                logger.warning(f"No 'Relationships' found for QUERY block: {query_text}")
                extracted_data[query_text] = {'Text': 'NOT_FOUND', 'Confidence': 0}

    logger.debug(f"Extracted data: {json.dumps(extracted_data, indent=2)}")
    return extracted_data

def parse_date(date_string):
    if not date_string or date_string == 'NOT_FOUND':
        return None
    try:
        return datetime.strptime(date_string, '%m/%d/%Y').date()
    except ValueError:
        logger.warning(f"Unable to parse date: {date_string}")
        return None

def insert_nyyf_1_form_data(conn, pdf_jpg_id, extracted_data, textract_form_id):
    try:
        with conn.cursor() as cur:
            query = sql.SQL("""
            INSERT INTO form_nyyf_1 (
                pdf_jpg_id, last_name, first_name, birth_date, ssn, home_address, city, state, zip,
                attending_high_school, enrolled_high_school_equivalent, four_statements_true,
                parent_guardian_permission, has_working_papers, age_18_to_24
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) RETURNING id
            """)
            
            def convert_boolean(value):
                return value.lower() == 'yes' if value else None

            cur.execute(query, (
                pdf_jpg_id,
                extracted_data.get('What is the applicant\'s last name?', {}).get('Text'),
                extracted_data.get('What is the applicant\'s first name?', {}).get('Text'),
                parse_date(extracted_data.get('What is the applicant\'s birth date?', {}).get('Text')),
                extracted_data.get('What is the applicant\'s Social Security Number?', {}).get('Text'),
                extracted_data.get('What is the applicant\'s home address?', {}).get('Text'),
                extracted_data.get('What is the applicant\'s city?', {}).get('Text'),
                extracted_data.get('What is the applicant\'s state?', {}).get('Text'),
                extracted_data.get('What is the applicant\'s zip code?', {}).get('Text'),
                convert_boolean(extracted_data.get('Is the applicant currently attending high school?', {}).get('Text')),
                convert_boolean(extracted_data.get('Is the applicant currently enrolled in a High School Equivalent program?', {}).get('Text')),
                convert_boolean(extracted_data.get('Are any of the 4 statements true?', {}).get('Text')),
                convert_boolean(extracted_data.get('Is the applicant 16-17 years old and has parent/guardian permission?', {}).get('Text')),
                convert_boolean(extracted_data.get('Does the applicant have working papers?', {}).get('Text')),
                convert_boolean(extracted_data.get('Is the applicant 18 to 24 years old?', {}).get('Text'))
            ))
            form_nyyf_1_id = cur.fetchone()[0]
            
            # Update pdf_pages table with the new textract_form_id
            update_query = sql.SQL("""
            UPDATE pdf_pages SET textract_form_id = %s WHERE id = %s
            """)
            cur.execute(update_query, (textract_form_id, pdf_jpg_id))
            
            logger.info(f"Inserted NYYF_1 Form data for pdf_jpg_id: {pdf_jpg_id} with textract_form_id: {textract_form_id}")
            return form_nyyf_1_id
    except Exception as e:
        logger.error(f"Error inserting NYYF_1 Form data: {str(e)}")
        raise

def process_single_form(form):
    pdf_jpg_id = form['id']
    jpg_url = form['jpg_url']
    textract_form_id = f"TEXTRACT-{pdf_jpg_id}-{int(time.time())}"
    
    logger.info(f"Processing form {pdf_jpg_id}")
    try:
        response = requests.get(jpg_url)
        if response.status_code == 200:
            image_bytes = response.content
            textract_response = analyze_document_with_adapter(image_bytes)
            if textract_response:
                extracted_data = extract_relevant_data(textract_response)
                return pdf_jpg_id, extracted_data, textract_form_id
            else:
                logger.error(f"Failed to analyze document for form {pdf_jpg_id}")
        else:
            logger.error(f"Failed to download image for form {pdf_jpg_id}: HTTP {response.status_code}")
    except Exception as e:
        logger.error(f"Unexpected error processing form {pdf_jpg_id}: {str(e)}")
    
    return None

def process_nyyf_1_forms():
    conn = get_db_connection()
    try:
        unprocessed_forms = get_unprocessed_nyyf_1_forms()
        total_forms = len(unprocessed_forms)
        logger.info(f"Found {total_forms} unprocessed NYYF_1 Forms to process")
        
        successful_forms = 0
        failed_forms = 0
        
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=multiprocessing.cpu_count() * 2) as executor:
            futures = [executor.submit(process_single_form, form) for form in unprocessed_forms]
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    pdf_jpg_id, extracted_data, textract_form_id = result
                    try:
                        insert_nyyf_1_form_data(conn, pdf_jpg_id, extracted_data, textract_form_id)
                        successful_forms += 1
                        logger.info(f"Successfully processed form {pdf_jpg_id}")
                    except Exception as e:
                        failed_forms += 1
                        logger.error(f"Error inserting data for form {pdf_jpg_id}: {str(e)}")
                else:
                    failed_forms += 1
                
                # Commit every 5 successful inserts
                if successful_forms % 5 == 0:
                    conn.commit()
                    logger.info(f"Progress: {successful_forms + failed_forms}/{total_forms} forms processed. "
                                f"Successful: {successful_forms}, Failed: {failed_forms}")

        # Final commit for any remaining changes
        conn.commit()

        logger.info("Form processing completed")
        logger.info(f"Total forms processed: {total_forms}")
        logger.info(f"Successful: {successful_forms}, Failed: {failed_forms}")
    except Exception as e:
        logger.error(f"Error processing NYYF_1 Forms: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("Starting NYYF_1 Form processing...")
    process_nyyf_1_forms()