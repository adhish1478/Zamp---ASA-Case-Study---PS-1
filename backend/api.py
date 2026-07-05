import os
import sys
import json
import sqlite3
import shutil
from typing import List
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure sys.path to import pipeline and matching components cleanly
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(script_dir, "pipeline"))
sys.path.append(os.path.join(script_dir, "matching"))

from pipeline.batch_runner import process_single_file, init_db, DB_PATH

app = FastAPI(
    title="Invoice Processing API",
    description="Backend API for raw text extraction, LLM structuring, confidence composer, and PO matching.",
    version="1.0.0"
)

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure database is initialized on startup
@app.on_event("startup")
def startup_event():
    init_db()

# Create a local upload directory
UPLOAD_DIR = os.path.join(script_dir, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

class ReviewRequest(BaseModel):
    status: str  # "auto_approved" | "rejected" | "flagged_for_review"
    explanation: str

@app.get("/api/health")
def health_check():
    return {"status": "healthy", "database": os.path.exists(DB_PATH)}

@app.get("/api/invoices")
def get_invoices():
    """
    Fetches all processed invoices and their PO matching decisions from the SQLite database.
    """
    if not os.path.exists(DB_PATH):
        return []
        
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT 
                i.invoice_id, 
                i.source_file, 
                i.source_type, 
                i.ocr_confidence, 
                i.page_count,
                i.vendor_name, 
                i.invoice_number, 
                i.invoice_date, 
                i.po_reference, 
                i.total, 
                i.tax, 
                i.line_items,
                i.composite_confidence, 
                i.confidence_details, 
                i.extraction_flags, 
                i.requires_human_review AS extraction_review,
                d.status, 
                d.matched_po_id, 
                d.rule_trace, 
                d.explanation, 
                d.requires_human_review AS decision_review,
                i.timestamp
            FROM invoices i
            LEFT JOIN invoice_decisions d ON i.invoice_id = d.invoice_id
            ORDER BY i.timestamp DESC
        """)
        rows = cursor.fetchall()
        
        invoices = []
        for row in rows:
            invoice = dict(row)
            # Deserialize JSON fields
            try:
                invoice["line_items"] = json.loads(invoice["line_items"]) if invoice["line_items"] else []
            except Exception:
                invoice["line_items"] = []
                
            try:
                invoice["confidence_details"] = json.loads(invoice["confidence_details"]) if invoice["confidence_details"] else {}
            except Exception:
                invoice["confidence_details"] = {}
                
            try:
                invoice["extraction_flags"] = json.loads(invoice["extraction_flags"]) if invoice["extraction_flags"] else []
            except Exception:
                invoice["extraction_flags"] = []
                
            try:
                invoice["rule_trace"] = json.loads(invoice["rule_trace"]) if invoice["rule_trace"] else []
            except Exception:
                invoice["rule_trace"] = []
                
            invoices.append(invoice)
            
        return invoices
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        conn.close()

@app.post("/api/upload")
def upload_invoices(files: List[UploadFile] = File(...)):
    """
    Accepts 1 to 100 PDF invoice files, processes them concurrently,
    saves structured outputs to the database, and returns the decisions.
    """
    if len(files) > 100:
        raise HTTPException(status_code=400, detail="Cannot upload more than 100 files at once.")
        
    saved_paths = []
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"File {file.filename} is not a PDF.")
            
        # Clean filename to avoid directory traversal
        safe_filename = os.path.basename(file.filename)
        dest_path = os.path.join(UPLOAD_DIR, safe_filename)
        
        # Write file contents
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        saved_paths.append(dest_path)
        
    # Process files concurrently using ThreadPoolExecutor
    # Mixed I/O (OpenAI network block) means multi-threading is highly efficient
    results = []
    max_workers = min(10, len(saved_paths))
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_file, path): path for path in saved_paths}
        
        for future in as_completed(futures):
            path = futures[future]
            try:
                res = future.result()
                results.append(res)
            except Exception as e:
                results.append({
                    "invoice_id": os.path.splitext(os.path.basename(path))[0],
                    "source_file": os.path.basename(path),
                    "success": False,
                    "error": str(e)
                })
            finally:
                # Cleanup uploaded temp file
                if os.path.exists(path):
                    os.remove(path)
                    
    return results

@app.post("/api/invoices/{invoice_id}/review")
def review_invoice(invoice_id: str, request: ReviewRequest):
    """
    Updates the decision status and reasoning for a flagged invoice during human review.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Check if decision exists
        cursor.execute("SELECT count(*) FROM invoice_decisions WHERE invoice_id = ?", (invoice_id,))
        exists = cursor.fetchone()[0]
        if not exists:
            raise HTTPException(status_code=404, detail="Invoice decision record not found.")
            
        # Update decision status, explanation, and clear manual review flag
        cursor.execute("""
            UPDATE invoice_decisions
            SET status = ?, explanation = ?, requires_human_review = 0
            WHERE invoice_id = ?
        """, (request.status, request.explanation, invoice_id))
        
        # Also clear manual review flag on the extraction table
        cursor.execute("""
            UPDATE invoices
            SET requires_human_review = 0
            WHERE invoice_id = ?
        """, (invoice_id,))
        
        conn.commit()
        return {"status": "success", "message": f"Invoice {invoice_id} successfully reviewed."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database update failed: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    # Start server on 0.0.0.0:8000
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
