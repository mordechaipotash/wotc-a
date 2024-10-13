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

def extract_address_2(model_name, jpg_filename):
    image_url = f"https://pbuqlylgktjdhjqkvwnv.supabase.co/storage/v1/object/public/jpgs/{jpg_filename}"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": "Please extract the second line of the street address from this image of an 8850 Form. This might include apartment numbers, suite numbers, or be blank if there's no second line. If there's no second line, respond with 'NOT_FOUND'. Ensure the response contains only the second line of the address or 'NOT_FOUND', with no additional text or explanations."},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": image_url}}]}
        ],
        "max_tokens": 300
    }
    try:
        response = requests.post(OPENROUTER_URL, json=payload, headers=headers)
        response.raise_for_status()
        json_response = response.json()
        if 'choices' in json_response and json_response['choices']:
            extracted_address_2 = json_response['choices'][0]['message']['content'].strip()
            return extracted_address_2 if extracted_address_2 and extracted_address_2 != "NOT_FOUND" else None
        else:
            return None
    except requests.RequestException as e:
        logger.error(f"Error in API request: {str(e)}")
        return None

def process_batch(batch):
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_page = {executor.submit(extract_address_2, "google/gemini-flash-1.5", page[1]): page for page in batch}
        for future in as_completed(future_to_page):
            page = future_to_page[future]
            try:
                address_2 = future.result()
                if address_2:
                    results.append((address_2[:100], page[0]))  # Truncate to 100 characters
            except Exception as exc:
                logger.error(f'{page[0]} generated an exception: {exc}')
    return results

def update_pdf_pages_address_2():
    logger.info("Starting update_pdf_pages_address_2 function for 8850 Form")
    conn = psycopg2.connect(DB_URL)
    cursor = conn.cursor()

    try:
        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE "address_#2" IS NULL 
        AND form_type = '8850 Form';
        """)
        total_null_address_2 = cursor.fetchone()[0]
        logger.info(f"Total records to process: {total_null_address_2}")

        cursor.execute("""
        SELECT id, jpg_filename
        FROM pdf_pages
        WHERE "address_#2" IS NULL
        AND form_type = '8850 Form';
        """)
        pages = cursor.fetchall()

        batch_size = 100
        for i in range(0, len(pages), batch_size):
            batch = pages[i:i+batch_size]
            results = process_batch(batch)
            
            execute_batch(cursor, """
                UPDATE pdf_pages
                SET "address_#2" = %s
                WHERE id = %s;
                """, results)
            conn.commit()
            
            logger.info(f"Processed batch {i//batch_size + 1}/{(len(pages)-1)//batch_size + 1}")

        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE "address_#2" IS NOT NULL 
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
    update_pdf_pages_address_2()
    logger.info("Script completed")
