# Enterprise AP Invoice Processing & Auditing System

An automated, multi-stage Accounts Payable (AP) ingestion and compliance auditing engine. The system automates PDF invoice parsing via OCR, structure extraction, and correlation with pre-approved Purchase Orders (POs) using a deterministic rules validator.

---

## System Architecture

The core of the application resides in a decoupled Python backend hosting the data extraction, matching, and compliance validation pipelines.

```
                  ┌──────────────────────────────┐
                  │      React Client (Vercel)   │
                  └──────────────┬───────────────┘
                                 │ HTTPS requests
                                 ▼
                  ┌──────────────────────────────┐
                  │ Vercel Proxy Rewrite Router  │
                  └──────────────┬───────────────┘
                                 │ Proxy HTTP requests
                                 ▼
                  ┌──────────────────────────────┐
                  │   Oracle Cloud VM (8000)     │
                  └──────────────┬───────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ FastAPI Server  │     │ Ingestion & OCR │     │ Rules Validator │
│ (api.py)        │     │ (extraction.py) │     │ (matcher.py)    │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────┬───────────┴───────────┬───────────┘
                     ▼                       ▼
            ┌─────────────────┐     ┌─────────────────┐
            │   SQLite DB     │     │  Local Uploads  │
            │ (database.db)   │     │  (PDF Files)    │
            └─────────────────┘     └─────────────────┘
```

### 1. Ingestion & Extraction Pipeline (`backend/pipeline/`)
* **Stage 1: PDF to Text (OCR)**:
  * Uses `pdf2image` and `pytesseract` to handle native digital documents as well as scanned paper invoice PDFs.
  * Measures OCR text extraction density and outputs a confidence score. If average OCR confidence is low (< 60%), it overrides subsequent checks and flags the document for human review due to scan quality.
* **Stage 2: Schema Extraction (LLM-Guided)**:
  * Uses structured JSON mapping via OpenAI `gpt-4o-mini` to deserialize raw text into a strict schema including: `vendor_name`, `invoice_number`, `invoice_date`, `po_reference`, `line_items` (array of `description`, `qty`, `unit_price`, `amount`), `total`, and `tax`.

### 3. AP Auditing Rules Engine (`backend/matching/`)
* **Duplicate Detection Rule**: Scans the database for pre-existing records matching the same `vendor_name`, `invoice_number`, and `total` (excluding the current invoice ID) and auto-rejects matches as duplicates.
* **PO Matching & Partial Reconcile**:
  * Resolves vendor identity variations using token-sort Jaro-Winkler normalization (e.g. `Apex Office Solutions Ltd.` matching `APEX OFFICE SOLUTIONS`).
  * If a PO reference is provided, it matches the record in the `pos` ledger. If the vendor name on the invoice does not match the vendor name on the PO, it flags the invoice for manual review.
  * Evaluates invoice amounts against the pre-approved PO amount including vendor-specific tolerance bounds (e.g., 5% or 10%).
  * Supports partial fulfillment. Multiple invoices can be linked to the same PO; the validator accumulates prior processed invoice totals and flags if the cumulative sum exceeds the PO cap.

---

## Database Schema

The backend uses a local SQLite database file `backend/db/invoice_processor.db` containing the following schemas:

### `invoices`
Stores the extracted metadata from processed PDFs.
* `invoice_id` (TEXT, PK): Unique generated invoice ID (e.g. `inv_100000`).
* `source_file` (TEXT): Original name of the uploaded PDF.
* `file_hash` (TEXT): SHA-256 hash of file contents to prevent duplicate uploads.
* `raw_text` (TEXT): Full raw OCR text.
* `ocr_confidence` (REAL): Text conversion density confidence score.
* `vendor_name`, `invoice_number`, `invoice_date`, `po_reference` (TEXT).
* `total`, `tax` (REAL).
* `line_items` (TEXT): JSON array string of items.
* `composite_confidence` (REAL).

### `invoice_decisions`
Stores matching results, audit compliance traces, and auditor review states.
* `invoice_id` (TEXT, PK): Maps 1-to-1 with invoices.
* `status` (TEXT): Audit state (`auto_approved`, `flagged_for_review`, `rejected`).
* `matched_po_id` (TEXT): Linked PO reference code.
* `rule_trace` (TEXT): JSON array of audit trace codes (e.g. `["po_found", "vendor_approved", "split_po_sum_reconciled"]`).
* `explanation` (TEXT): Plain-English description explaining why the decision was reached.
* `requires_human_review` (INTEGER): Binary toggle indicating auditor review queue priority.

### `pos`
Stores the corporate pre-approved purchase order ledger.
* `po_id` (TEXT, PK): Purchase order reference code.
* `vendor_name` (TEXT): Approved vendor identity.
* `po_amount` (REAL): Maximum pre-approved spending limit.
* `approved_vendor` (INTEGER): Binary toggle for vendor compliance check.
* `tolerance_pct` (REAL): Allowed spending variance threshold (percentage).

---

## API Endpoints

All core features are exposed through the following REST API endpoints:

### Ingestion & Processing
* `POST /api/upload`: Receives multi-part file uploads, calculates file hashes, and spawns asynchronous background worker pipelines.
* `GET /api/upload/status/{batch_id}`: Polling status checker returning extraction and auditing logs for the batch.

### Invoice Administration
* `GET /api/invoices`: Returns the complete log of processed invoices.
* `POST /api/invoices/{invoice_id}/review`: Submits manually audited overrides to update compliance decisions.
* `DELETE /api/invoices/{invoice_id}`: Permanently deletes an invoice row and removes its physical PDF file from the disk.
* `POST /api/invoices/clear`: Wipes all invoices and decisions from database tables and empties local uploads folder.

### Procurement & Vendor Records
* `GET /api/pos`: Retrieves the active purchase orders ledger.
* `POST /api/suppliers/compliance`: Updates vendor authorization compliance.

---

## Deployment & Setup

### VM Configuration (Oracle Cloud)
1. **Firewall Settings**: Ensure TCP port `8000` is open in Oracle Cloud's Ingress Rules list and UFW:
   ```bash
   sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
   sudo netfilter-persistent save
   sudo ufw allow 8000/tcp
   ```
2. **Running the App**:
   ```bash
   nohup ./venv/bin/uvicorn backend.api:app --host 0.0.0.0 --port 8000 > uvicorn.log 2>&1 &
   ```

### Reverse Proxy Configuration (Vercel)
To prevent HTTPS browser Mixed Content warnings, the React frontend uses a secure reverse proxy to route calls to the VM's HTTP endpoint. This is configured in `frontend/vercel.json`:
```json
{
  "rewrites": [
    {
      "source": "/api/uploads/:path*",
      "destination": "http://<VM_IP>:8000/api/uploads/:path*"
    },
    {
      "source": "/api/:path*",
      "destination": "http://<VM_IP>:8000/api/:path*"
    }
  ]
}
```
In local development, the client falls back to calling `http://localhost:8000` automatically.
