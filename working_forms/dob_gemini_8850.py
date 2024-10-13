import os
import logging
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
DB_URL = os.getenv("DB_URL")
if DB_URL:
    DB_URL = DB_URL.rsplit('/', 1)[0] + '/postgres'
else:
    logger.error("DB_URL environment variable is not set")
    exit(1)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Log configuration details
logger.debug(f"DB_URL: {'set' if DB_URL else 'not set'}")
logger.debug(f"OPENROUTER_API_KEY: {'set' if OPENROUTER_API_KEY else 'not set'}")

def extract_dob(model_name, jpg_filename):
    image_url = f"https://pbuqlylgktjdhjqkvwnv.supabase.co/storage/v1/object/public/jpgs/{jpg_filename}"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": "Please extract the date of birth from this image of an 8850 Form. The date of birth is likely near the top of the form. Ensure the response contains only the date of birth in the format MM/DD/YYYY, with no additional text, characters, or explanations."},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_url}}]}
        ],
        "max_tokens": 300
    }
    try:
        response = requests.post(OPENROUTER_URL, json=payload, headers=headers)
        response.raise_for_status()
        json_response = response.json()
        if 'choices' in json_response and json_response['choices']:
            extracted_dob = json_response['choices'][0]['message']['content'].strip()
            # Validate date format (MM/DD/YYYY)
            if len(extracted_dob) == 10 and extracted_dob[2] == '/' and extracted_dob[5] == '/':
                return extracted_dob
            else:
                return "NOT_FOUND"
        else:
            return "NOT_FOUND"
    except requests.RequestException as e:
        logger.error(f"Error in API request: {str(e)}")
        return "NOT_FOUND"

def process_batch(batch):
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_page = {executor.submit(extract_dob, "google/gemini-flash-1.5", page[1]): page for page in batch}
        for future in as_completed(future_to_page):
            page = future_to_page[future]
            try:
                dob = future.result()
                results.append((dob, page[0]))  # (dob, page_id)
            except Exception as exc:
                logger.error(f'{page[0]} generated an exception: {exc}')
    return results

def update_pdf_pages_dob():
    logger.info("Starting update_pdf_pages_dob function for 8850 Form")
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE dob_gemini_8850 IS NULL 
        AND form_type = '8850 Form';
        """)
        total_null_dob = cursor.fetchone()[0]
        logger.info(f"Total records to process: {total_null_dob}")

        cursor.execute("""
        SELECT id, jpg_filename
        FROM pdf_pages
        WHERE dob_gemini_8850 IS NULL
        AND form_type = '8850 Form';
        """)
        pages = cursor.fetchall()

        batch_size = 100
        for i in range(0, len(pages), batch_size):
            batch = pages[i:i+batch_size]
            results = process_batch(batch)
            
            # Filter out any results where the DOB is "NOT_FOUND"
            valid_results = [(dob, page_id) for dob, page_id in results if dob != "NOT_FOUND"]
            
            execute_batch(cursor, """
                UPDATE pdf_pages
                SET dob_gemini_8850 = %s
                WHERE id = %s;
                """, valid_results)
            conn.commit()
            
            logger.info(f"Processed batch {i//batch_size + 1}/{(len(pages)-1)//batch_size + 1}")

        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE dob_gemini_8850 IS NOT NULL 
        AND form_type = '8850 Form';
        """)
        final_count = cursor.fetchone()[0]
        logger.info(f"Total records processed: {final_count}")

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        logger.info("Database connection closed")

if __name__ == "__main__":
    logger.info("Script started")
    update_pdf_pages_dob()
    logger.info("Script completed")