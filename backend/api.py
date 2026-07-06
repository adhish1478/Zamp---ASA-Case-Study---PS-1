import os
import sys
import json
import sqlite3
import shutil
from typing import List
import uuid
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure sys.path to import pipeline and matching components cleanly
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)
sys.path.append(os.path.join(script_dir, "pipeline"))
sys.path.append(os.path.join(script_dir, "matching"))

from pipeline.batch_runner import process_single_file, save_invoice_to_db, init_db, DB_PATH
from matching.matcher import run_matching_on_invoice

from fastapi.staticfiles import StaticFiles

# In-memory store for tracking active upload batches
batch_progress = {}

app = FastAPI(
    title="Invoice Processing API",
    description="Backend API for raw text extraction, LLM structuring, confidence composer, and PO matching.",
    version="1.0.0"
)

# Mount the static uploads directory so the frontend can retrieve PDF files
# Configure sys.path directories
script_dir = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(script_dir, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

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

class ReviewRequest(BaseModel):
    status: str  # "auto_approved" | "rejected" | "flagged_for_review"
    explanation: str
    vendor_name: str = None
    invoice_number: str = None
    invoice_date: str = None
    po_reference: str = None
    total: float = None
    tax: float = None

class ComplianceRequest(BaseModel):
    vendor_name: str
    approved_vendor: bool

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

def process_batch_background(batch_id: str, files_to_process: List[dict]):
    """
    Asynchronous worker processing batch uploads. Updates in-memory progress indicators
    and runs PO matching on cached and uncached invoices.
    """
    batch_progress[batch_id]["status"] = "processing"
    
    def process_item(item):
        dest_path = item["dest_path"]
        file_hash = item["file_hash"]
        filename = item["filename"]
        cached_inv_row = item["cached_inv_row"]
        is_pure_cache = item["is_pure_cache"]
        
        try:
            if is_pure_cache and cached_inv_row:
                # Re-upload of the exact same file. Skip OCR/LLM and direct matching.
                batch_progress[batch_id]["files"][filename] = {
                    "status": "matching",
                    "progress": 0.8
                }
                
                invoice_id = cached_inv_row["invoice_id"]
                decision = run_matching_on_invoice(invoice_id)
                
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
                inv_row = dict(cursor.fetchone())
                conn.close()
                
                res = {
                    "invoice_id": invoice_id,
                    "source_file": filename,
                    "success": True,
                    "cached": True,
                    "stage1": {
                        "raw_text": inv_row["raw_text"],
                        "source_type": inv_row["source_type"],
                        "ocr_confidence": inv_row["ocr_confidence"],
                        "page_count": inv_row["page_count"]
                    },
                    "stage2": {
                        "vendor_name": inv_row["vendor_name"],
                        "invoice_number": inv_row["invoice_number"],
                        "invoice_date": inv_row["invoice_date"],
                        "po_reference": inv_row["po_reference"],
                        "tax": inv_row["tax"],
                        "total": inv_row["total"],
                        "line_items": json.loads(inv_row["line_items"]) if inv_row["line_items"] else []
                    },
                    "confidence": {
                        "overall": inv_row["composite_confidence"],
                        "fields": json.loads(inv_row["confidence_details"]) if inv_row["confidence_details"] else {},
                        "extraction_flags": json.loads(inv_row["extraction_flags"]) if inv_row["extraction_flags"] else [],
                        "requires_human_review": bool(inv_row["requires_human_review"])
                    },
                    "decision": decision
                }
            elif cached_inv_row:
                # Duplicate upload (same hash, different filename). Reuse Stage 1 & 2.
                batch_progress[batch_id]["files"][filename] = {
                    "status": "matching",
                    "progress": 0.8
                }
                
                # Derive a new unique invoice ID based on the filename
                new_invoice_id = os.path.splitext(filename)[0]
                
                # Reconstruct structured objects from cached row
                stage1 = {
                    "raw_text": cached_inv_row["raw_text"],
                    "source_type": cached_inv_row["source_type"],
                    "ocr_confidence": cached_inv_row["ocr_confidence"],
                    "page_count": cached_inv_row["page_count"]
                }
                stage2 = {
                    "vendor_name": cached_inv_row["vendor_name"],
                    "invoice_number": cached_inv_row["invoice_number"],
                    "invoice_date": cached_inv_row["invoice_date"],
                    "po_reference": cached_inv_row["po_reference"],
                    "tax": cached_inv_row["tax"],
                    "total": cached_inv_row["total"],
                    "line_items": json.loads(cached_inv_row["line_items"]) if cached_inv_row["line_items"] else []
                }
                confidence = {
                    "overall": cached_inv_row["composite_confidence"],
                    "fields": json.loads(cached_inv_row["confidence_details"]) if cached_inv_row["confidence_details"] else {},
                    "extraction_flags": json.loads(cached_inv_row["extraction_flags"]) if cached_inv_row["extraction_flags"] else [],
                    "requires_human_review": bool(cached_inv_row["requires_human_review"])
                }
                
                # Save as a distinct record in SQLite invoices table
                save_invoice_to_db(new_invoice_id, filename, stage1, stage2, confidence, file_hash=file_hash)
                
                # Execute Matching rules engine to trigger duplicate_invoice_detected reject route
                decision = run_matching_on_invoice(new_invoice_id)
                
                res = {
                    "invoice_id": new_invoice_id,
                    "source_file": filename,
                    "success": True,
                    "cached": True,
                    "stage1": stage1,
                    "stage2": stage2,
                    "confidence": confidence,
                    "decision": decision
                }
            else:
                # Uncached execution flow
                batch_progress[batch_id]["files"][filename] = {
                    "status": "parsing",
                    "progress": 0.2
                }
                
                from pipeline.extraction import extract_pdf
                stage1 = extract_pdf(dest_path)
                
                batch_progress[batch_id]["files"][filename] = {
                    "status": "structuring",
                    "progress": 0.5
                }
                
                low_confidence = (stage1["source_type"] == "scanned" and (
                    stage1["ocr_confidence"] < 0.90 or stage1["region_confidence"]["bottom"] < 0.5
                ))
                
                from pipeline.structuring import structure_invoice
                stage2 = structure_invoice(stage1["raw_text"], low_confidence_regions=low_confidence)
                
                batch_progress[batch_id]["files"][filename] = {
                    "status": "matching",
                    "progress": 0.8
                }
                
                from pipeline.confidence import compose_confidence
                confidence = compose_confidence(stage1, stage2)
                
                invoice_id = os.path.splitext(filename)[0]
                save_invoice_to_db(invoice_id, filename, stage1, stage2, confidence, file_hash=file_hash)
                
                decision = run_matching_on_invoice(invoice_id)
                
                res = {
                    "invoice_id": invoice_id,
                    "source_file": filename,
                    "success": True,
                    "stage1": stage1,
                    "stage2": stage2,
                    "confidence": confidence,
                    "decision": decision
                }
                
            batch_progress[batch_id]["files"][filename] = {
                "status": "completed",
                "progress": 1.0,
                "result": res
            }
        except Exception as e:
            batch_progress[batch_id]["files"][filename] = {
                "status": "failed",
                "progress": 1.0,
                "error": str(e)
            }

    max_workers = min(10, len(files_to_process))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(process_item, files_to_process)
        
    batch_progress[batch_id]["status"] = "completed"


@app.post("/api/upload")
def upload_invoices(background_tasks: BackgroundTasks, files: List[UploadFile] = File(...)):
    """
    Accepts 1 to 100 PDF invoices and initializes batch background tasks.
    Returns status: queued and batch_id immediately.
    """
    import hashlib
    import json
    import sqlite3
    
    if len(files) > 100:
        raise HTTPException(status_code=400, detail="Cannot upload more than 100 files at once.")
        
    batch_id = str(uuid.uuid4())
    files_to_process = []
    
    # Initialize batch metrics
    batch_progress[batch_id] = {
        "status": "queued",
        "files": {}
    }
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        for file in files:
            if not file.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail=f"File {file.filename} is not a PDF.")
                
            safe_filename = os.path.basename(file.filename)
            content = file.file.read()
            file.file.seek(0)
            
            file_hash = hashlib.sha256(content).hexdigest()
            
            # Setup initial file progress indicator
            batch_progress[batch_id]["files"][safe_filename] = {
                "status": "queued",
                "progress": 0.0
            }
            
            cursor.execute("SELECT * FROM invoices WHERE file_hash = ?", (file_hash,))
            inv_row = cursor.fetchone()
            
            cached_row_dict = dict(inv_row) if inv_row else None
            is_pure_cache = cached_row_dict is not None and cached_row_dict["source_file"] == safe_filename
            
            # Save file statically to local uploads directory if not a pure cache hit
            dest_path = os.path.join(UPLOAD_DIR, safe_filename)
            if not is_pure_cache:
                with open(dest_path, "wb") as buffer:
                    shutil.copyfileobj(file.file, buffer)
            
            files_to_process.append({
                "dest_path": dest_path,
                "file_hash": file_hash,
                "filename": safe_filename,
                "cached_inv_row": cached_row_dict,
                "is_pure_cache": is_pure_cache
            })
            
    finally:
        conn.close()
        
    # Queue background task
    background_tasks.add_task(process_batch_background, batch_id, files_to_process)
    
    return {"batch_id": batch_id, "status": "queued"}


@app.get("/api/upload/status/{batch_id}")
def get_upload_status(batch_id: str):
    """
    Returns the real-time processing status of an upload batch.
    """
    if batch_id not in batch_progress:
        raise HTTPException(status_code=404, detail="Upload batch not found.")
    return batch_progress[batch_id]

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
        
        # Also clear manual review flag and update fields on the extraction table
        if request.vendor_name is not None:
            cursor.execute("""
                UPDATE invoices
                SET vendor_name = ?, invoice_number = ?, invoice_date = ?, po_reference = ?, total = ?, tax = ?, requires_human_review = 0
                WHERE invoice_id = ?
            """, (
                request.vendor_name,
                request.invoice_number,
                request.invoice_date,
                request.po_reference,
                request.total,
                request.tax,
                invoice_id
            ))
        else:
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

@app.get("/api/pos")
def get_purchase_orders():
    """
    Fetches the complete ledger of purchase orders from pos table.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM pos ORDER BY po_id ASC")
        pos = [dict(row) for row in cursor.fetchall()]
        return pos
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/api/suppliers/compliance")
def update_supplier_compliance(request: ComplianceRequest):
    """
    Toggles a supplier's compliance status and dynamically updates all matching invoices.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        val = 1 if request.approved_vendor else 0
        # Update approved_vendor status for all POs matching this vendor name
        cursor.execute(
            "UPDATE pos SET approved_vendor = ? WHERE LOWER(vendor_name) = LOWER(?)",
            (val, request.vendor_name)
        )
        conn.commit()
        
        # Select all invoice IDs and their extracted vendor names to run re-matching
        cursor.execute("SELECT invoice_id, vendor_name FROM invoices")
        rows = cursor.fetchall()
        
        from matching.matcher import normalize_vendor
        target_norm = normalize_vendor(request.vendor_name)
        
        re_match_ids = []
        for inv_id, vname in rows:
            if normalize_vendor(vname) == target_norm:
                re_match_ids.append(inv_id)
                
        # Re-run rule matches to update statuses on the frontend
        for inv_id in re_match_ids:
            run_matching_on_invoice(inv_id)
            
        return {"success": True, "re_matched_count": len(re_match_ids)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

if __name__ == "__main__":
    import uvicorn
    # Start server on 0.0.0.0:8000
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
