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

def extract_date_signed(model_name, jpg_filename):
    image_url = f"https://pbuqlylgktjdhjqkvwnv.supabase.co/storage/v1/object/public/jpgs/{jpg_filename}"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": "Please extract the date signed from this image of an 8 Question Form. The date signed is likely near the bottom of the form. Ensure the response contains only the date in the format MM/DD/YYYY, with no additional text or explanations."},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_url}}]}
        ],
        "max_tokens": 300
    }
    try:
        response = requests.post(OPENROUTER_URL, json=payload, headers=headers)
        response.raise_for_status()
        json_response = response.json()
        if 'choices' in json_response and json_response['choices']:
            extracted_date = json_response['choices'][0]['message']['content'].strip()
            # Validate date format (MM/DD/YYYY)
            if len(extracted_date) == 10 and extracted_date[2] == '/' and extracted_date[5] == '/':
                return extracted_date
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
        future_to_page = {executor.submit(extract_date_signed, "google/gemini-flash-1.5", page[1]): page for page in batch}
        for future in as_completed(future_to_page):
            page = future_to_page[future]
            try:
                date_signed = future.result()
                results.append((date_signed, page[0]))  # (date_signed, page_id)
            except Exception as exc:
                logger.error(f'{page[0]} generated an exception: {exc}')
    return results

def update_pdf_pages_date_signed():
    logger.info("Starting update_pdf_pages_date_signed function for 8 Question Form")
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE date_signed_gemini_8qf IS NULL 
        AND form_type = '8 Question Form';
        """)
        total_null_date_signed = cursor.fetchone()[0]
        logger.info(f"Total records to process: {total_null_date_signed}")

        cursor.execute("""
        SELECT id, jpg_filename
        FROM pdf_pages
        WHERE date_signed_gemini_8qf IS NULL
        AND form_type = '8 Question Form';
        """)
        pages = cursor.fetchall()

        batch_size = 100
        for i in range(0, len(pages), batch_size):
            batch = pages[i:i+batch_size]
            results = process_batch(batch)
            
            # Filter out any results where the date_signed is "NOT_FOUND"
            valid_results = [(date_signed, page_id) for date_signed, page_id in results if date_signed != "NOT_FOUND"]
            
            execute_batch(cursor, """
                UPDATE pdf_pages
                SET date_signed_gemini_8qf = %s
                WHERE id = %s;
                """, valid_results)
            conn.commit()
            
            logger.info(f"Processed batch {i//batch_size + 1}/{(len(pages)-1)//batch_size + 1}")

        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE date_signed_gemini_8qf IS NOT NULL 
        AND form_type = '8 Question Form';
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
    update_pdf_pages_date_signed()
    logger.info("Script completed")