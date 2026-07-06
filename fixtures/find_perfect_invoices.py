import os
import json
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BATCH_DIR = os.path.join(PROJECT_ROOT, "fixtures", "batch")
GT_DIR = os.path.join(BATCH_DIR, "ground_truth")
PO_JSON = os.path.join(PROJECT_ROOT, "fixtures", "po_dataset.json")
PERFECT_DIR = os.path.join(PROJECT_ROOT, "fixtures", "perfect")

def normalize_name(name):
    if not name:
        return ""
    # remove punctuation and lowercase
    import re
    return re.sub(r'\W+', '', name.lower())

def find_perfect():
    if not os.path.exists(PERFECT_DIR):
        os.makedirs(PERFECT_DIR)
        
    with open(PO_JSON, "r") as f:
        pos = json.load(f)
        
    po_dict = {p["po_id"]: p for p in pos}
    
    perfect_files = []
    
    for filename in sorted(os.listdir(GT_DIR)):
        if not filename.endswith(".json"):
            continue
            
        json_path = os.path.join(GT_DIR, filename)
        with open(json_path, "r") as f:
            inv = json.load(f)
            
        po_ref = inv.get("po_reference")
        if not po_ref or po_ref not in po_dict:
            continue
            
        po = po_dict[po_ref]
        
        # Check Vendor matching
        inv_vendor = normalize_name(inv.get("vendor_name"))
        po_vendor = normalize_name(po.get("vendor_name"))
        if inv_vendor != po_vendor:
            continue
            
        # Check approved vendor
        if not po.get("approved_vendor"):
            continue
            
        # Check total within tolerance
        inv_total = inv.get("total") or 0.0
        po_amount = po.get("po_amount") or 0.0
        tolerance_pct = po.get("tolerance_pct") or 0.0
        
        diff = inv_total - po_amount
        # We allow positive tolerance
        max_allowed = po_amount * (tolerance_pct / 100.0)
        
        if diff > max_allowed:
            continue
            
        # Ensure it doesn't have any other flags
        # All checks passed! This is a perfect match invoice!
        pdf_name = inv["source_file"]
        pdf_src = os.path.join(BATCH_DIR, pdf_name)
        pdf_dst = os.path.join(PERFECT_DIR, pdf_name)
        
        if os.path.exists(pdf_src):
            shutil.copy2(pdf_src, pdf_dst)
            perfect_files.append(pdf_name)
            
    print(f"Found {len(perfect_files)} perfect matching invoices.")
    print("Copied files:", perfect_files)

if __name__ == "__main__":
    find_perfect()
