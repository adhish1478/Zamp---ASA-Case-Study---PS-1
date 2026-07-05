import datetime
import re

def compose_confidence(stage1_results: dict, stage2_results: dict) -> dict:
    """
    Composites Stage 1 OCR confidence and Stage 2 heuristics to calculate 
    overall and field-level confidence scores.
    
    Args:
        stage1_results (dict): Output from extraction.py
        stage2_results (dict): Output from structuring.py (structured JSON schema)
        
    Returns:
        dict: {
            "overall": float (0.0 to 1.0),
            "fields": {
                "vendor_name": float,
                "invoice_number": float,
                "invoice_date": float,
                "po_reference": float,
                "line_items": float,
                "tax": float,
                "total": float
            },
            "extraction_flags": list of str,
            "requires_human_review": bool
        }
    """
    source_type = stage1_results["source_type"]
    ocr_overall = stage1_results["ocr_confidence"]
    regions = stage1_results["region_confidence"]
    
    # Initialize extraction flags and field confidences
    extraction_flags = []
    
    # Map regional OCR confidence to structured fields as a base score
    # Top region fields
    field_conf = {
        "vendor_name": regions["top"] if source_type == "scanned" else 1.0,
        "invoice_number": regions["top"] if source_type == "scanned" else 1.0,
        "invoice_date": regions["top"] if source_type == "scanned" else 1.0,
        "po_reference": regions["top"] if source_type == "scanned" else 1.0,
        
        # Middle region fields
        "line_items": regions["middle"] if source_type == "scanned" else 1.0,
        
        # Bottom region fields
        "tax": regions["bottom"] if source_type == "scanned" else 1.0,
        "total": regions["bottom"] if source_type == "scanned" else 1.0
    }
    
    # -------------------------------------------------------------
    # HEURISTIC 1: Required fields check
    # -------------------------------------------------------------
    # vendor_name, total, invoice_number are required
    if not stage2_results["vendor_name"] or stage2_results["vendor_name"] == "Unknown Vendor":
        field_conf["vendor_name"] = 0.0
        extraction_flags.append("missing_vendor_name")
        
    if stage2_results["invoice_number"] is None:
        field_conf["invoice_number"] = 0.0
        extraction_flags.append("missing_invoice_number")
        
    if stage2_results["total"] is None:
        field_conf["total"] = 0.0
        extraction_flags.append("missing_total_amount")
        
    # -------------------------------------------------------------
    # HEURISTIC 2: Date validation check
    # -------------------------------------------------------------
    date_str = stage2_results["invoice_date"]
    date_valid = False
    if date_str:
        # Check standard format YYYY-MM-DD
        if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            try:
                # Attempt to parse into a valid datetime object
                yr, mo, dy = map(int, date_str.split("-"))
                datetime.date(yr, mo, dy)
                date_valid = True
            except ValueError:
                pass
        
        if not date_valid:
            field_conf["invoice_date"] = round(field_conf["invoice_date"] * 0.2, 4)
            extraction_flags.append("invalid_date_format")
    else:
        # missing date is a flag but not an absolute 0 if OCR was clean
        field_conf["invoice_date"] = 0.0
        extraction_flags.append("missing_invoice_date")
        
    # -------------------------------------------------------------
    # HEURISTIC 3: Totals reconciliation check (subtotal + tax == total)
    # -------------------------------------------------------------
    items = stage2_results["line_items"]
    items_sum = round(sum(item["amount"] for item in items), 2)
    tax = stage2_results["tax"] or 0.0
    total = stage2_results["total"]
    
    if total is not None:
        expected_total = round(items_sum + tax, 2)
        diff = abs(total - expected_total)
        
        # If the difference is greater than 5 cents, flag it
        if diff > 0.05:
            # Drop total and items confidence significantly due to discrepancy
            field_conf["total"] = round(field_conf["total"] * 0.5, 4)
            field_conf["line_items"] = round(field_conf["line_items"] * 0.5, 4)
            field_conf["tax"] = round(field_conf["tax"] * 0.5, 4)
            extraction_flags.append("totals_do_not_reconcile")
    else:
        # No total to reconcile against
        field_conf["line_items"] = round(field_conf["line_items"] * 0.5, 4)
        extraction_flags.append("totals_do_not_reconcile")

    # -------------------------------------------------------------
    # HEURISTIC 4: Low regional OCR flags propagation
    # -------------------------------------------------------------
    if source_type == "scanned":
        # If bottom region OCR was completely unreadable (e.g. smudged)
        if regions["bottom"] < 0.5:
            extraction_flags.append("low_ocr_confidence_region")
            
    # -------------------------------------------------------------
    # COMPOSITE SCORE CALCULATION
    # -------------------------------------------------------------
    # Average of all fields confidences
    overall_composite = round(sum(field_conf.values()) / len(field_conf), 4)
    
    # -------------------------------------------------------------
    # ROUTING DECISION: Requires human review?
    # -------------------------------------------------------------
    # Flag for manual human review if:
    # - Overall composite score is < 0.90
    # - Any required field (vendor_name, total, invoice_number) has confidence < 0.85
    # - Critical heuristic flags are present
    requires_human_review = False
    
    if overall_composite < 0.90:
        requires_human_review = True
    elif field_conf["vendor_name"] < 0.85 or field_conf["total"] < 0.85 or field_conf["invoice_number"] < 0.85:
        requires_human_review = True
    elif "totals_do_not_reconcile" in extraction_flags:
        requires_human_review = True
    elif "invalid_date_format" in extraction_flags:
        requires_human_review = True
    elif "low_ocr_confidence_region" in extraction_flags:
        requires_human_review = True
        
    return {
        "overall": overall_composite,
        "fields": field_conf,
        "extraction_flags": extraction_flags,
        "requires_human_review": requires_human_review
    }
