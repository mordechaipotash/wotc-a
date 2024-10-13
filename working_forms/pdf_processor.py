import os
import logging
from dotenv import load_dotenv
from supabase import create_client, Client
import fitz  # PyMuPDF
import psycopg2
from psycopg2.extras import execute_batch
from datetime import datetime, timezone
import concurrent.futures

# Load environment variables and set up logging
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
BUCKET_NAME = "attachments"
JPG_BUCKET_NAME = "jpgs"
DB_URL = os.getenv("DB_URL")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def get_db_connection():
    return psycopg2.connect(DB_URL)

def ensure_table_structure(cursor):
    cursor.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                       WHERE table_name = 'attachments' AND column_name = 'updated_at') THEN
            ALTER TABLE attachments ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP;
        END IF;
    END $$;
    """)
    logger.info("Ensured 'updated_at' column exists in attachments table")

def process_pdf(pdf_content, filename):
    try:
        pdf_document = fitz.open(stream=pdf_content, filetype="pdf")
        jpg_filenames = []
        
        for page_num in range(len(pdf_document)):
            page = pdf_document[page_num]
            pix = page.get_pixmap()
            jpg_data = pix.tobytes("jpg")
            
            jpg_filename = f"{filename[:-4]}_{page_num + 1}.jpg"
            try:
                supabase.storage.from_(JPG_BUCKET_NAME).upload(jpg_filename, jpg_data)
                jpg_filenames.append(jpg_filename)
            except Exception as e:
                if 'Duplicate' in str(e):
                    logger.info(f"JPG {jpg_filename} already exists. Using existing file.")
                    jpg_filenames.append(jpg_filename)
                else:
                    raise
        
        return jpg_filenames
    except Exception as e:
        logger.error(f"Error processing PDF {filename}: {str(e)}")
        return None

def update_database(conn, cursor, attachment_id, email_id, pdf_filename, jpg_filenames):
    try:
        current_time = datetime.now(timezone.utc)
        
        cursor.execute("""
            UPDATE attachments
            SET is_processed = TRUE, updated_at = %s
            WHERE id = %s
        """, (current_time, attachment_id))

        insert_data = [(email_id, pdf_filename, jpg_filename, page_num + 1, current_time, current_time, attachment_id) 
                       for page_num, jpg_filename in enumerate(jpg_filenames)]
        
        execute_batch(cursor, """
            INSERT INTO pdf_pages (email_id, pdf_filename, jpg_filename, page_number, created_at, updated_at, attachment_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (email_id, pdf_filename, page_number) 
            DO UPDATE SET 
                jpg_filename = EXCLUDED.jpg_filename,
                updated_at = EXCLUDED.updated_at,
                attachment_id = EXCLUDED.attachment_id
        """, insert_data)

        conn.commit()
        logger.info(f"Database updated for PDF: {pdf_filename}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating database for PDF {pdf_filename}: {str(e)}")
        raise

def process_attachment(attachment):
    attachment_id, email_id, storage_path = attachment
    try:
        pdf_content = supabase.storage.from_(BUCKET_NAME).download(storage_path)
        if pdf_content:
            jpg_filenames = process_pdf(pdf_content, storage_path)
            if jpg_filenames:
                return (attachment_id, email_id, storage_path, jpg_filenames)
            else:
                logger.warning(f"Failed to process {storage_path}")
        else:
            logger.warning(f"No content for {storage_path}")
    except Exception as e:
        logger.error(f"Error processing attachment {attachment_id}: {str(e)}")
    return None

def process_pdfs():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        ensure_table_structure(cursor)
        
        cursor.execute("""
            SELECT a.id, a.email_id, a.storage_path
            FROM attachments a
            WHERE a.content_type = 'application/pdf'
        """)
        all_attachments = cursor.fetchall()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            results = list(executor.map(process_attachment, all_attachments))

        for result in results:
            if result:
                attachment_id, email_id, storage_path, jpg_filenames = result
                update_database(conn, cursor, attachment_id, email_id, storage_path, jpg_filenames)

        # Update attachment_id for existing pdf_pages entries
        current_time = datetime.now(timezone.utc)
        cursor.execute("""
            UPDATE pdf_pages pp
            SET attachment_id = a.id,
                updated_at = %s
            FROM attachments a
            WHERE pp.email_id = a.email_id
              AND pp.pdf_filename = a.storage_path
              AND pp.attachment_id IS NULL
        """, (current_time,))
        updated_rows = cursor.rowcount
        logger.info(f"Updated attachment_id for {updated_rows} existing pdf_pages entries")

        # Update created_at and updated_at for pdf_pages where they are null
        cursor.execute("""
            UPDATE pdf_pages
            SET created_at = %s,
                updated_at = %s
            WHERE created_at IS NULL OR updated_at IS NULL
        """, (current_time, current_time))
        updated_timestamp_rows = cursor.rowcount
        logger.info(f"Updated timestamps for {updated_timestamp_rows} pdf_pages entries")

        conn.commit()

    except Exception as e:
        logger.error(f"An error occurred while processing PDFs: {str(e)}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()

def main():
    logger.info("Starting PDF processing")
    process_pdfs()
    logger.info("PDF processing completed")

if __name__ == "__main__":
    main()
