import os
import sqlite3
import time
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed
from extraction import extract_pdf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
DB_DIR = os.path.join(PROJECT_ROOT, "backend", "db")
DB_PATH = os.path.join(DB_DIR, "invoice_processor.db")

# Input folders
BATCH_DIR = os.path.join(PROJECT_ROOT, "fixtures", "batch")
EDGE_DIR = os.path.join(PROJECT_ROOT, "fixtures", "edge_cases")

def get_db_connection():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            source_type TEXT NOT NULL,
            ocr_confidence REAL NOT NULL,
            page_count INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_invoice_to_db(invoice_id, source_file, result):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO invoices 
        (invoice_id, source_file, raw_text, source_type, ocr_confidence, page_count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        invoice_id,
        source_file,
        result["raw_text"],
        result["source_type"],
        result["ocr_confidence"],
        result["page_count"]
    ))
    conn.commit()
    conn.close()

def process_single_file(pdf_path):
    filename = os.path.basename(pdf_path)
    # invoice_id is the filename without extension (e.g. inv_100000)
    invoice_id = os.path.splitext(filename)[0]
    
    try:
        result = extract_pdf(pdf_path)
        save_invoice_to_db(invoice_id, filename, result)
        return {
            "invoice_id": invoice_id,
            "source_file": filename,
            "success": True,
            "result": result
        }
    except Exception as e:
        return {
            "invoice_id": invoice_id,
            "source_file": filename,
            "success": False,
            "error": str(e)
        }

def run_batch():
    init_db()
    
    # Gather all PDF files from fixtures
    pdf_files = []
    
    # 1. Main batch PDFs
    batch_pdfs = glob.glob(os.path.join(BATCH_DIR, "*.pdf"))
    pdf_files.extend(batch_pdfs)
    
    # 2. Edge case PDFs
    edge_pdfs = glob.glob(os.path.join(EDGE_DIR, "*.pdf"))
    pdf_files.extend(edge_pdfs)
    
    total_files = len(pdf_files)
    print(f"Found {total_files} PDF files to process in batch runner.")
    
    results = []
    
    # Run processing concurrently using a ThreadPoolExecutor
    # A pool size of 8 works well for mixed I/O and process calls (Tesseract)
    max_workers = 8
    print(f"Starting batch runner with {max_workers} concurrent threads...")
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_file, path): path for path in pdf_files}
        
        processed_count = 0
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            processed_count += 1
            if processed_count % 15 == 0 or processed_count == total_files:
                print(f"Processed {processed_count}/{total_files} files...")
                
    elapsed_time = time.time() - start_time
    
    # Analyze and print summary
    success_count = sum(1 for r in results if r["success"])
    error_count = total_files - success_count
    
    text_layer_count = 0
    scanned_count = 0
    
    text_layer_conf_sum = 0.0
    scanned_conf_sum = 0.0
    
    errored_files = []
    
    for r in results:
        if r["success"]:
            res_detail = r["result"]
            if res_detail["source_type"] == "text_layer":
                text_layer_count += 1
                text_layer_conf_sum += res_detail["ocr_confidence"]
            else:
                scanned_count += 1
                scanned_conf_sum += res_detail["ocr_confidence"]
        else:
            errored_files.append((r["source_file"], r["error"]))
            
    avg_text_conf = (text_layer_conf_sum / text_layer_count) if text_layer_count > 0 else 0.0
    avg_scanned_conf = (scanned_conf_sum / scanned_count) if scanned_count > 0 else 0.0
    
    print("\n" + "="*50)
    print("BATCH RUNNER PROCESSING SUMMARY")
    print("="*50)
    print(f"Total files:            {total_files}")
    print(f"Successfully processed: {success_count}")
    print(f"Failed to process:      {error_count}")
    print(f"Text Layer Documents:   {text_layer_count} (Avg OCR Confidence: {avg_text_conf:.4f})")
    print(f"Scanned Documents:      {scanned_count} (Avg OCR Confidence: {avg_scanned_conf:.4f})")
    print(f"Total time elapsed:     {elapsed_time:.2f} seconds")
    print("="*50)
    
    if error_count > 0:
        print("\nErrored files:")
        for name, err in errored_files:
            print(f" - {name}: {err}")
        print("="*50)
        
    return results

if __name__ == "__main__":
    run_batch()
