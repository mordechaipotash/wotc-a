import os
import requests
import psycopg2
from dotenv import load_dotenv
import logging

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
DB_URL = os.getenv("DB_URL")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

def get_form_type(model_name, image_url):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": """You are an expert in extracting structured data from documents. 
                Identify the form type based on specific rules and extract the required information accordingly. 
                Respond only with the form type, without any additional characters.

                Form types and their identification criteria:

                1. 8850 Form:
                   - Contains the title 'Pre-Screening Notice and Certification Request for the Work Opportunity Credit'
                   - Has the form number '8850' prominently displayed
                   - Includes sections such as 'Job Applicant Information' with fields for name, social security number, address, and date of birth
                   - Contains a series of checkboxes or statements related to eligibility conditions
                   - Includes a signature line for the job applicant
                   - Often contains references to the Internal Revenue Service (IRS) or Department of the Treasury

                2. 8 Question Form:
                   - Contains approximately 8 numbered questions or sections
                   - Typically starts with personal information fields like name, SSN, and date of birth
                   - Often includes the phrase 'Please Fill In to the Best of Your Ability!' at the top
                   - Questions cover topics such as previous employment, receipt of benefits, unemployment status, felony convictions, and veteran status
                   - Most questions have 'Yes' and 'No' checkboxes or options
                   - Often includes a signature line and date at the bottom

                3. NYYF_1:
                   - Keywords: 'New York Youth Jobs Program', 'Youth Certification', 'WE ARE YOUR DOL'
                   - Sections: 'Youth Certification', 'Applicant Information'
                   - Fields: last name, first name, birth date, social security number, home address, city, state, zip, educational status

                4. NYYF_2:
                   - Keywords: 'Youth Certification Qualifications', 'New York Youth Jobs Program'
                   - Sections: 'Qualifications', 'Agreement'
                   - Fields: age, unemployment status, educational background, benefits received, personal circumstances

                5. POU_1:
                   - Keywords: 'Participant Statement of Understanding', 'subsidized employment', 'paid on-the-job training'
                   - Sections: 'Participant Information', 'Statement of Understanding'
                   - Fields: participant's name, social security number, address, city, state, zip, employment and program participation details

                6. POU_2:
                   - Keywords: 'CA and SNAP benefits', 'supplemental grant', 'Fair Hearing aid-to-continue', 'Business Link'
                   - Sections: 'Income Reporting', 'Employment Conditions', 'Termination and Reduction of Benefits'
                   - Fields: income reporting requirements, conditions for supplemental grants, notification requirements, guidelines for maintaining benefits

                7. Identity Document:
                   - Contains personal identification information (name, date of birth, SSN)
                   - May include official headers or footers from government agencies
                   - Often includes document-specific identifiers (e.g., 'DRIVER LICENSE', 'SOCIAL SECURITY CARD')
                   - May contain security features or statements
                   - Often includes a unique identification number
                   - May have fields for physical characteristics
                   - Often includes issue date and/or expiration date
                   - May contain a photograph or space for a photograph
                   - May include a barcode or machine-readable zone

                8. Blank Form:
                   - Identify if the extracted text is empty or contains only minimal information

                9. Other/Undefined:
                   - Use this classification if the document contains significant text or information but doesn't match any specific form type

                Analyze the provided image and determine the form type based on these criteria. Return only the form type as a string."""
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
            return json_response['choices'][0]['message']['content'].strip()
        else:
            return f"Unexpected response format: {response.text}"
    except requests.RequestException as e:
        return f"Error: {str(e)}"

def update_pdf_pages_form_type():
    logger.info("Starting update_pdf_pages_form_type function")
    
    if not DB_URL or not OPENROUTER_API_KEY:
        logger.error("DB_URL or OPENROUTER_API_KEY environment variable is not set")
        return

    conn = None
    cursor = None
    try:
        logger.info("Attempting to connect to the database")
        conn = psycopg2.connect(DB_URL)
        cursor = conn.cursor()
        logger.info("Successfully connected to the database")
        check_table_structure(cursor)

        # Fetch all pdf_pages without a form_type
        logger.info("Attempting to fetch pages without form_type")
        cursor.execute("""
        SELECT id, jpg_url
        FROM pdf_pages
        WHERE form_type IS NULL;
        """)
        pages = cursor.fetchall()
        total_pages = len(pages)
        logger.info(f"Found {total_pages} pages without form_type")

        for index, (page_id, jpg_url) in enumerate(pages, start=1):
            logger.info(f"Processing page {page_id} ({index}/{total_pages})")
            try:
                form_type = get_form_type("google/gemini-flash-1.5", jpg_url)
                logger.info(f"Received form type: {form_type} for page {page_id}")
                
                # Update the form_type in the database
                cursor.execute("""
                UPDATE pdf_pages
                SET form_type = %s
                WHERE id = %s;
                """, (form_type, page_id))
                conn.commit()
                logger.info(f"Updated page {page_id} with form type: {form_type}")
            except Exception as e:
                logger.error(f"Error processing page {page_id}: {str(e)}")
                conn.rollback()

        logger.info("Finished processing all pages")

        # Check how many records were updated
        cursor.execute("""
        SELECT COUNT(*) 
        FROM pdf_pages 
        WHERE form_type IS NOT NULL;
        """)
        updated_count = cursor.fetchone()[0]
        logger.info(f"Total number of records with form_type: {updated_count}")

    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        logger.info("Database connection closed")

def check_table_structure(cursor):
    logger.info("Checking pdf_pages table structure")
    try:
        cursor.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'pdf_pages';
        """)
        columns = cursor.fetchall()
        logger.info(f"pdf_pages table structure: {columns}")
    except psycopg2.Error as e:
        logger.error(f"Error checking table structure: {e}")

if __name__ == "__main__":
    logger.info("Script started")
    update_pdf_pages_form_type()
    logger.info("Script finished")