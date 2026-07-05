import os
import sqlite3
import time
import glob
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from extraction import extract_pdf
from structuring import structure_invoice
from confidence import compose_confidence

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
    
    # We drop the old Stage 1 table if it doesn't have the new Stage 2 columns.
    # To do this safely and cleanly, we check if vendor_name exists in column list.
    cursor.execute("PRAGMA table_info(invoices)")
    cols = [col[1] for col in cursor.fetchall()]
    
    if cols and "vendor_name" not in cols:
        print("Old SQLite schema detected. Dropping table to migrate to Stage 2 schema...")
        cursor.execute("DROP TABLE invoices")
        
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            source_type TEXT NOT NULL,
            ocr_confidence REAL NOT NULL,
            page_count INTEGER NOT NULL,
            vendor_name TEXT,
            invoice_number TEXT,
            invoice_date TEXT,
            po_reference TEXT,
            total REAL,
            tax REAL,
            line_items TEXT,
            composite_confidence REAL,
            confidence_details TEXT,
            extraction_flags TEXT,
            requires_human_review INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_invoice_to_db(invoice_id, source_file, stage1, stage2, confidence):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO invoices 
        (
            invoice_id, source_file, raw_text, source_type, ocr_confidence, page_count,
            vendor_name, invoice_number, invoice_date, po_reference, total, tax, line_items,
            composite_confidence, confidence_details, extraction_flags, requires_human_review
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        invoice_id,
        source_file,
        stage1["raw_text"],
        stage1["source_type"],
        stage1["ocr_confidence"],
        stage1["page_count"],
        stage2["vendor_name"],
        stage2["invoice_number"],
        stage2["invoice_date"],
        stage2["po_reference"],
        stage2["total"],
        stage2["tax"],
        json.dumps(stage2["line_items"]),
        confidence["overall"],
        json.dumps(confidence["fields"]),
        json.dumps(confidence["extraction_flags"]),
        1 if confidence["requires_human_review"] else 0
    ))
    conn.commit()
    conn.close()

def process_single_file(pdf_path):
    filename = os.path.basename(pdf_path)
    invoice_id = os.path.splitext(filename)[0]
    
    try:
        # 1. Run Stage 1: Extraction & OCR Confidence
        stage1 = extract_pdf(pdf_path)
        
        # Determine if low confidence retry mode is triggered
        low_confidence = (stage1["source_type"] == "scanned" and (
            stage1["ocr_confidence"] < 0.90 or stage1["region_confidence"]["bottom"] < 0.5
        ))
        
        # 2. Run Stage 2: LLM Structuring
        stage2 = structure_invoice(stage1["raw_text"], low_confidence_regions=low_confidence)
        
        # 3. Run Stage 2: Confidence Composer
        confidence = compose_confidence(stage1, stage2)
        
        # 4. Save fully structured output to SQLite
        save_invoice_to_db(invoice_id, filename, stage1, stage2, confidence)
        
        return {
            "invoice_id": invoice_id,
            "source_file": filename,
            "success": True,
            "stage1": stage1,
            "stage2": stage2,
            "confidence": confidence
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
    
    # Gather PDF files
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
    
    # Run processing concurrently
    # Note: Threads will block on OpenAI network calls, making ThreadPoolExecutor highly effective.
    max_workers = 10
    print(f"Starting batch runner with {max_workers} concurrent threads...")
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_file, path): path for path in pdf_files}
        
        processed_count = 0
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            processed_count += 1
            if processed_count % 10 == 0 or processed_count == total_files:
                print(f"Processed {processed_count}/{total_files} files...")
                
    elapsed_time = time.time() - start_time
    
    # Analyze and print summary
    success_count = sum(1 for r in results if r["success"])
    error_count = total_files - success_count
    
    flagged_review_count = 0
    composite_conf_sum = 0.0
    
    errored_files = []
    
    for r in results:
        if r["success"]:
            conf_details = r["confidence"]
            composite_conf_sum += conf_details["overall"]
            if conf_details["requires_human_review"]:
                flagged_review_count += 1
        else:
            errored_files.append((r["source_file"], r["error"]))
            
    avg_composite_conf = (composite_conf_sum / success_count) if success_count > 0 else 0.0
    
    print("\n" + "="*50)
    print("STAGE 2 BATCH RUNNER PROCESSING SUMMARY")
    print("="*50)
    print(f"Total files:                 {total_files}")
    print(f"Successfully processed:      {success_count}")
    print(f"Failed to process:           {error_count}")
    print(f"Flagged for human review:    {flagged_review_count}")
    print(f"Average Composite Confidence: {avg_composite_conf:.4f}")
    print(f"Total time elapsed:          {elapsed_time:.2f} seconds")
    print("="*50)
    
    if error_count > 0:
        print("\nErrored files:")
        for name, err in errored_files:
            print(f" - {name}: {err}")
        print("="*50)
        
    return results

if __name__ == "__main__":
    # Check if key is configure first
    try:
        run_batch()
    except Exception as e:
        print(f"Execution failed: {e}")
