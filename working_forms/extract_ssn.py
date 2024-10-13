import os
import re
import logging
import psycopg2
from dotenv import load_dotenv
import requests

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
DB_URL = os.getenv("DB_URL")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Log configuration details
logger.debug(f"DB_URL: {'set' if DB_URL else 'not set'}")
logger.debug(f"OPENROUTER_API_KEY: {'set' if OPENROUTER_API_KEY else 'not set'}")

def validate_and_format_ssn(ssn):
    # Remove any non-digit characters
    digits = re.sub(r'\D', '', ssn)
    
    # Check if we have exactly 9 digits
    if len(digits) != 9:
        return None
    
    # Format as ###-##-####
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"

def extract_ssn(model_name, image_url):
    logger.debug(f"Attempting to extract SSN from image: {image_url}")
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": "Please extract the Social Security Number (SSN) from the provided image in the exact format ###-##-####. Ensure the response contains only the SSN, with no additional text, characters, or explanations."
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        }
                    }
                ]
            }
        ],
        "max_tokens": 300
    }

    try:
        response = requests.post(OPENROUTER_URL, json=payload, headers=headers)
        response.raise_for_status()
        json_response = response.json()

        if 'choices' in json_response and json_response['choices']:
            extracted_ssn = json_response['choices'][0]['message']['content'].strip()
            validated_ssn = validate_and_format_ssn(extracted_ssn)
            return validated_ssn if validated_ssn else "Invalid SSN"
        else:
            return "Invalid SSN"
    except requests.RequestException as e:
        logger.error(f"Error in API request: {str(e)}")
        return "Invalid SSN"

def update_pdf_pages_ssn():
    logger.info("Starting update_pdf_pages_ssn function")
    try:
        conn = psycopg2.connect(DB_URL)
        logger.info("Successfully connected to the database")
    except Exception as e:
        logger.error(f"Failed to connect to the database: {str(e)}")
        return

    cursor = conn.cursor()

    try:
        # Count total records without extracted_ssn for specific form types
        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE extracted_ssn IS NULL 
        AND form_type IN ('8850 Form', '8 Question Form', 'NYYF_1');
        """)
        total_null_ssn = cursor.fetchone()[0]
        logger.info(f"Total records without extracted_ssn for specified form types: {total_null_ssn}")

        # Count records for each form type
        cursor.execute("""
        SELECT form_type, COUNT(*) 
        FROM pdf_pages 
        WHERE extracted_ssn IS NULL 
        AND form_type IN ('8850 Form', '8 Question Form', 'NYYF_1')
        GROUP BY form_type;
        """)
        form_type_counts = cursor.fetchall()
        for form_type, count in form_type_counts:
            logger.info(f"Form type '{form_type}': {count} records without extracted_ssn")

        # Fetch all pdf_pages without an extracted_ssn and with specific form types
        cursor.execute("""
        SELECT id, jpg_url, form_type
        FROM pdf_pages
        WHERE extracted_ssn IS NULL
        AND form_type IN ('8850 Form', '8 Question Form', 'NYYF_1');
        """)
        pages = cursor.fetchall()
        logger.info(f"Found {len(pages)} pages to process")

        for index, (page_id, jpg_url, form_type) in enumerate(pages, start=1):
            logger.debug(f"Processing page {page_id} ({index}/{len(pages)}) with URL: {jpg_url}, Form type: {form_type}")
            ssn = extract_ssn("google/gemini-flash-1.5", jpg_url)
            
            cursor.execute("""
            UPDATE pdf_pages
            SET extracted_ssn = %s
            WHERE id = %s;
            """, (ssn, page_id))
            conn.commit()
            
            if ssn == "Invalid SSN":
                logger.warning(f"Invalid SSN detected for page {page_id}")
            else:
                logger.info(f"Updated page {page_id} with SSN: {ssn}")

        logger.info("Finished processing all pages")

        # Final count of records with extracted SSN
        cursor.execute("""
        SELECT COUNT(*) FROM pdf_pages 
        WHERE extracted_ssn IS NOT NULL 
        AND form_type IN ('8850 Form', '8 Question Form', 'NYYF_1');
        """)
        final_count = cursor.fetchone()[0]
        logger.info(f"Total records with extracted_ssn for specified form types after processing: {final_count}")

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()
        logger.info("Database connection closed")

if __name__ == "__main__":
    logger.info("Script started")
    update_pdf_pages_ssn()
    logger.info("Script completed")