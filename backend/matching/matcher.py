import os
import json
import sqlite3
import logging
import re
from dotenv import load_dotenv
from openai import OpenAI

# Load root .env
script_dir = os.path.dirname(os.path.abspath(__file__))
root_env = os.path.join(script_dir, "..", "..", ".env")
load_dotenv(root_env)

# Configure paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(script_dir))
DB_PATH = os.path.join(PROJECT_ROOT, "backend", "db", "invoice_processor.db")

# Configure logging
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = None

def get_openai_client():
    global client
    if client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise ValueError("OPENAI_API_KEY environment variable is missing or set to placeholder.")
        client = OpenAI(api_key=api_key)
    return client

def normalize_vendor(name: str) -> str:
    """
    Standardizes vendor names to compare them fuzzy-style, ignoring common suffixes.
    """
    n = str(name).lower()
    # Strip common business suffixes
    suffixes = [
        r"\binc\b\.?", r"\bltd\b\.?", r"\bcorp\b\.?", r"\bco\b\.?", 
        r"\bsystems\b\.?", r"\bsolutions\b\.?", r"\bservices\b\.?", 
        r"\bwholesalers\b\.?", r"\badvisory\b\.?", r"\bsoftware\b\.?"
    ]
    for suffix in suffixes:
        n = re.sub(suffix, "", n)
    # Strip non-alphanumeric characters and spaces
    return re.sub(r"\W+", "", n)

def match_invoice_to_po(invoice_id: str) -> dict:
    """
    Runs the deterministic rules matching engine on the database record for an invoice.
    
    Returns:
        dict: {
            "status": "auto_approved" | "flagged_for_review" | "rejected",
            "matched_po_id": str | None,
            "rule_trace": list of str,
            "requires_human_review": bool,
            "invoice_data": dict,
            "po_data": dict | None
        }
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 1. Fetch invoice data
    cursor.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
    invoice_row = cursor.fetchone()
    if not invoice_row:
        conn.close()
        raise ValueError(f"Invoice record not found: {invoice_id}")
        
    invoice = dict(invoice_row)
    
    status = "auto_approved"
    rule_trace = []
    matched_po_id = None
    po_data = None
    
    vendor_name = invoice["vendor_name"]
    invoice_number = invoice["invoice_number"]
    total = invoice["total"]
    po_reference = invoice["po_reference"]
    stage2_review = bool(invoice["requires_human_review"])
    
    # -------------------------------------------------------------
    # PRE-RULE: OCR / Extraction Low-Confidence Review Override
    # -------------------------------------------------------------
    # If Stage 2 flagged this invoice as requiring human review,
    # we force it to be flagged for review in decision output immediately
    # to avoid running matching rules on corrupt OCR values.
    if stage2_review:
        status = "flagged_for_review"
        rule_trace.append("low_confidence_review_override")
        conn.close()
        return {
            "status": status,
            "matched_po_id": None,
            "rule_trace": rule_trace,
            "requires_human_review": True,
            "invoice_data": invoice,
            "po_data": None
        }
    
    # -------------------------------------------------------------
    # RULE 1: Duplicate Detection Check
    # -------------------------------------------------------------
    cursor.execute("""
        SELECT count(*) FROM invoices 
        WHERE vendor_name = ? AND invoice_number = ? AND total = ? AND invoice_id != ?
    """, (vendor_name, invoice_number, total, invoice_id))
    dup_count = cursor.fetchone()[0]
    
    if dup_count > 0:
        status = "rejected"
        rule_trace.append("duplicate_invoice_detected")
        conn.close()
        return {
            "status": status,
            "matched_po_id": None,
            "rule_trace": rule_trace,
            "requires_human_review": True,
            "invoice_data": invoice,
            "po_data": None
        }
        
    # -------------------------------------------------------------
    # RULE 2: PO Reference Number Check & Retrieval
    # -------------------------------------------------------------
    po_row = None
    if po_reference:
        cursor.execute("SELECT * FROM pos WHERE po_id = ?", (po_reference,))
        po_row = cursor.fetchone()
        
    if not po_reference or not po_row:
        status = "flagged_for_review"
        rule_trace.append("no_matching_po_number")
        
        # Fallback vendor validation: check if this vendor has any unapproved POs in the pos table
        norm_inv_vendor = normalize_vendor(vendor_name)
        cursor.execute("SELECT DISTINCT vendor_name, approved_vendor FROM pos")
        for row in cursor.fetchall():
            po_vendor_name, approved_vendor = row
            if normalize_vendor(po_vendor_name) == norm_inv_vendor and approved_vendor == 0:
                rule_trace.append("unapproved_vendor")
                break
            
        conn.close()
        return {
            "status": status,
            "matched_po_id": None,
            "rule_trace": rule_trace,
            "requires_human_review": True,
            "invoice_data": invoice,
            "po_data": None
        }
        
    po_data = dict(po_row)
    matched_po_id = po_data["po_id"]
    rule_trace.append("po_found")
    
    # -------------------------------------------------------------
    # RULE 4: Vendor Validation (Mismatch and Approved Status)
    # -------------------------------------------------------------
    norm_inv_vendor = normalize_vendor(vendor_name)
    norm_po_vendor = normalize_vendor(po_data["vendor_name"])
    
    if norm_inv_vendor != norm_po_vendor:
        status = "flagged_for_review"
        rule_trace.append("vendor_mismatch")
        
    if not po_data["approved_vendor"]:
        status = "flagged_for_review"
        rule_trace.append("unapproved_vendor")
    else:
        rule_trace.append("vendor_approved")
        
    # -------------------------------------------------------------
    # RULE 5: Amount and Tolerance matching rules
    # -------------------------------------------------------------
    po_amount = po_data["po_amount"]
    tolerance_pct = po_data["tolerance_pct"]
    
    if total is None:
        status = "flagged_for_review"
        rule_trace.append("missing_total")
    else:
        # Check exact matching
        if abs(total - po_amount) <= 0.05:
            rule_trace.append("exact_amount_match")
        # Check if exceeds PO amount (tolerance check)
        elif total > po_amount:
            diff = total - po_amount
            diff_pct = round((diff / po_amount) * 100, 2)
            
            if diff_pct <= tolerance_pct:
                rule_trace.append(f"amount_within_tolerance:{diff_pct}%")
            else:
                status = "flagged_for_review"
                rule_trace.append(f"amount_exceeds_tolerance:{diff_pct}%")
        # Check if less than PO amount (Partial / Split PO validation)
        else:
            # Look up other invoices referencing the same PO in the database
            cursor.execute("""
                SELECT total FROM invoices 
                WHERE po_reference = ? AND invoice_id != ?
            """, (po_reference, invoice_id))
            previous_totals = [r[0] for r in cursor.fetchall() if r[0] is not None]
            previous_sum = sum(previous_totals)
            cumulative_sum = round(previous_sum + total, 2)
            
            if cumulative_sum <= round(po_amount * (1 + tolerance_pct / 100), 2):
                rule_trace.append("partial_fulfillment_approved")
                rule_trace.append(f"split_po_sum_reconciled:{cumulative_sum}/{po_amount}")
            else:
                status = "flagged_for_review"
                rule_trace.append("partial_fulfillment_failed")
                rule_trace.append(f"split_po_sum_exceeds_po:{cumulative_sum}/{po_amount}")
    conn.close()
    
    return {
        "status": status,
        "matched_po_id": matched_po_id,
        "rule_trace": rule_trace,
        "requires_human_review": status in ["flagged_for_review", "rejected"],
        "invoice_data": invoice,
        "po_data": po_data
    }

def generate_explanation(invoice_data: dict, po_data: dict or None, status: str, rule_trace: list) -> str:
    """
    Generates a plain-English explanation of the decision using OpenAI gpt-4o-mini.
    """
    openai_client = get_openai_client()
    
    system_prompt = (
        "You are an expert Accounts Payable (AP) audit assistant. Your role is to write a single, "
        "friendly, clear, plain-English sentence summarizing why an invoice was approved, "
        "rejected, or flagged for manual review based on the engine's rule trace. "
        "Do not use technical jargon. Reference specific names and dollar amounts where possible."
    )
    
    user_prompt = (
        f"INVOICE DETAILS:\n"
        f" - Vendor Name: {invoice_data['vendor_name']}\n"
        f" - Invoice ID: {invoice_data['invoice_id']}\n"
        f" - Total Amount: ${invoice_data['total']}\n"
        f" - PO Number Referenced: {invoice_data['po_reference']}\n\n"
    )
    
    if po_data:
        user_prompt += (
            f"MATCHED PO DETAILS:\n"
            f" - PO ID: {po_data['po_id']}\n"
            f" - PO Vendor: {po_data['vendor_name']}\n"
            f" - PO Amount: ${po_data['po_amount']}\n"
            f" - Vendor Approved Status: {'Yes' if po_data['approved_vendor'] else 'No'}\n"
            f" - Allowed Tolerance: {po_data['tolerance_pct']}%\n\n"
        )
    else:
        user_prompt += "MATCHED PO DETAILS: None matched.\n\n"
        
    user_prompt += (
        f"DECISION STATS:\n"
        f" - Final Status: {status}\n"
        f" - Automated Rule Trace: {rule_trace}\n\n"
        f"Provide the explanation sentence directly."
    )
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error calling OpenAI in generate_explanation: {e}")
        # Return fallback text
        if status == "auto_approved":
            return "The invoice was automatically approved based on successful PO correlation."
        elif status == "rejected":
            return "The invoice was rejected due to a compliance rule violation."
        else:
            return "The invoice was flagged for manual review."

def run_matching_on_invoice(invoice_id: str) -> dict:
    """
    Executes PO matching, generates explanation, and saves the result in SQLite.
    """
    # 1. Run matcher rules
    match_res = match_invoice_to_po(invoice_id)
    
    # 2. Generate explanation sentence
    explanation = generate_explanation(
        match_res["invoice_data"],
        match_res["po_data"],
        match_res["status"],
        match_res["rule_trace"]
    )
    
    # 3. Save decision details to SQLite table
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO invoice_decisions 
        (invoice_id, status, matched_po_id, rule_trace, explanation, requires_human_review)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        invoice_id,
        match_res["status"],
        match_res["matched_po_id"],
        json.dumps(match_res["rule_trace"]),
        explanation,
        1 if match_res["requires_human_review"] else 0
    ))
    conn.commit()
    conn.close()
    
    return {
        "status": match_res["status"],
        "matched_po_id": match_res["matched_po_id"],
        "rule_trace": match_res["rule_trace"],
        "explanation": explanation,
        "requires_human_review": match_res["requires_human_review"]
    }

if __name__ == "__main__":
    # Tiny console test
    import sys
    if len(sys.argv) < 2:
        print("Usage: python matcher.py <invoice_id>")
        sys.exit(1)
        
    inv_id = sys.argv[1]
    print(f"Testing PO Matching on invoice_id: {inv_id}")
    try:
        res = run_matching_on_invoice(inv_id)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Error: {e}")
