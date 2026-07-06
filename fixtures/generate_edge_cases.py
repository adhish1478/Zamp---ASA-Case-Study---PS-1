import os
import json
import shutil
import random
from io import BytesIO
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw
import fitz  # PyMuPDF
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BATCH_DIR = os.path.join(BASE_DIR, "batch")
EDGE_DIR = os.path.join(BASE_DIR, "edge_cases")
GT_DIR = os.path.join(EDGE_DIR, "ground_truth")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(EDGE_DIR, exist_ok=True)
os.makedirs(GT_DIR, exist_ok=True)

env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

# Load PO dataset to append edge cases POs
PO_FILE = os.path.join(BASE_DIR, "po_dataset.json")
if os.path.exists(PO_FILE):
    with open(PO_FILE, "r") as f:
        po_dataset = json.load(f)
else:
    po_dataset = []

def make_bad_scan(src_pdf, dest_pdf):
    """
    Rasterizes PDF to images, adds noise, rotation, blur, smudge, 
    and saves as a new text-less PDF.
    """
    doc = fitz.open(src_pdf)
    pil_images = []
    
    for page in doc:
        # Render page to 150 DPI image
        pix = page.get_pixmap(dpi=150)
        img_data = pix.tobytes("png")
        img = Image.open(BytesIO(img_data)).convert("RGB")
        w, h = img.size
        
        # 1. Apply slight rotation
        img = img.rotate(random.uniform(-1.2, 1.2), fillcolor=(255, 255, 255))
        
        # 2. Add salt & pepper noise specs
        draw = ImageDraw.Draw(img)
        for _ in range(random.randint(150, 350)):
            x = random.randint(0, w - 1)
            y = random.randint(0, h - 1)
            draw.point((x, y), fill=(80, 80, 80))
            
        # 3. Add smudge/noise over the total area (typically bottom-right)
        # Smudge coordinates: x around 70% to 90%, y around 70% to 80%
        smudge_x = int(w * 0.70)
        smudge_y = int(h * 0.70)
        smudge_w = int(w * 0.18)
        smudge_h = int(h * 0.08)
        for i in range(smudge_w):
            for j in range(smudge_h):
                if random.random() < 0.15:  # Sparse noise pattern
                    px_x = smudge_x + i
                    px_y = smudge_y + j
                    if px_x < w and px_y < h:
                        # Draw gray noise dot
                        draw.point((px_x, px_y), fill=(120, 120, 120))
                        
        # 4. Apply Gaussian Blur to simulate lens/scan blur
        img = img.filter(ImageFilter.GaussianBlur(radius=0.9))
        
        # 5. Contrast reduction (low contrast scan)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(0.65)
        
        # 6. Brightness reduction (dim scan)
        enhancer_b = ImageEnhance.Brightness(img)
        img = enhancer_b.enhance(0.92)
        
        pil_images.append(img)
        
    # Save back to PDF using Pillow's multi-page PDF output
    if pil_images:
        pil_images[0].save(dest_pdf, save_all=True, append_images=pil_images[1:])

def render_html_to_pdf(html_content, dest_pdf):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        temp_path = os.path.join(BASE_DIR, "temp_edge.html")
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        page.goto(f"file://{temp_path}")
        page.pdf(path=dest_pdf, format="A4", print_background=True)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        browser.close()

def main():
    print("Generating edge case fixtures...")
    
    # -------------------------------------------------------------
    # Edge Case A: ocr_bad_scan.pdf
    # Take inv_100000.pdf from batch, degrade it, and save.
    # -------------------------------------------------------------
    src_pdf = os.path.join(BATCH_DIR, "inv_100000.pdf")
    src_gt = os.path.join(BATCH_DIR, "ground_truth", "inv_100000.json")
    
    if os.path.exists(src_pdf) and os.path.exists(src_gt):
        print("Creating ocr_bad_scan.pdf...")
        dest_pdf = os.path.join(EDGE_DIR, "ocr_bad_scan.pdf")
        make_bad_scan(src_pdf, dest_pdf)
        
        # Load and modify ground truth
        with open(src_gt, "r") as f:
            gt_data = json.load(f)
            
        gt_data["source_file"] = "ocr_bad_scan.pdf"
        gt_data["extraction_flags"] = ["total_amount_partially_obscured", "scanned_document", "low_ocr_confidence_region"]
        gt_data["confidence"]["overall"] = 0.55
        gt_data["confidence"]["fields"]["total"] = 0.35
        gt_data["confidence"]["fields"]["tax"] = 0.45
        
        with open(os.path.join(GT_DIR, "ocr_bad_scan.json"), "w") as f:
            json.dump(gt_data, f, indent=2)
    else:
        print("WARNING: Batch files missing. Run generate_invoices.py first.")

    # -------------------------------------------------------------
    # Edge Case B: po_split_across_invoices_1.pdf & _2.pdf
    # One PO split across two invoices.
    # -------------------------------------------------------------
    print("Creating split PO invoices...")
    vendor_split = {
        "name": "Nova Cloud Systems Inc.",
        "email": "billing@novacloud.com",
        "address": "100 Innovation Way, Suite 400, Boston, MA 02110"
    }
    po_split_id = "PO-SPLIT-2026-999"
    
    # Add PO record of $1500.00
    po_dataset.append({
        "po_id": po_split_id,
        "vendor_name": vendor_split["name"],
        "po_amount": 1500.00,
        "approved_vendor": True,
        "tolerance_pct": 2.0
    })
    
    # Render two separate invoices, each for $750.00
    template = env.get_template("template_1_tech.html")
    
    for part in [1, 2]:
        inv_data = {
            "invoice_id": f"inv_split_{part}",
            "vendor_name": vendor_split["name"],
            "vendor_address": vendor_split["address"],
            "vendor_email": vendor_split["email"],
            "client_name": "Zamp Inc.",
            "client_address": "548 Market St, Suite 8802, San Francisco, CA 94104",
            "invoice_number": f"INV-SPLIT-999-{part}",
            "invoice_date": "2026-06-15",
            "po_reference": po_split_id,
            "line_items": [
                {
                    "description": f"Cloud Computing Charges - Part {part}/2",
                    "qty": 1,
                    "unit_price": 700.00,
                    "amount": 700.00
                }
            ],
            "tax": 50.00,
            "total": 750.00
        }
        
        html_content = template.render(**inv_data)
        pdf_name = f"po_split_across_invoices_{part}.pdf"
        render_html_to_pdf(html_content, os.path.join(EDGE_DIR, pdf_name))
        
        gt_data = {
            "invoice_id": inv_data["invoice_id"],
            "vendor_name": inv_data["vendor_name"],
            "invoice_number": inv_data["invoice_number"],
            "invoice_date": inv_data["invoice_date"],
            "po_reference": inv_data["po_reference"],
            "line_items": inv_data["line_items"],
            "tax": inv_data["tax"],
            "total": inv_data["total"],
            "confidence": {
                "overall": 1.0,
                "fields": {
                    "invoice_number": 1.0,
                    "total": 1.0,
                    "vendor_name": 1.0,
                    "invoice_date": 1.0,
                    "po_reference": 1.0,
                    "line_items": 1.0,
                    "tax": 1.0
                }
            },
            "source_file": pdf_name,
            "extraction_flags": []
        }
        
        with open(os.path.join(GT_DIR, f"po_split_across_invoices_{part}.json"), "w") as f:
            json.dump(gt_data, f, indent=2)

    # -------------------------------------------------------------
    # Edge Case C: duplicate_invoice.pdf
    # Literal byte copy of inv_100001.pdf
    # -------------------------------------------------------------
    src_dup_pdf = os.path.join(BATCH_DIR, "inv_100001.pdf")
    src_dup_gt = os.path.join(BATCH_DIR, "ground_truth", "inv_100001.json")
    
    if os.path.exists(src_dup_pdf) and os.path.exists(src_dup_gt):
        print("Creating duplicate_invoice.pdf...")
        dest_dup_pdf = os.path.join(EDGE_DIR, "duplicate_invoice.pdf")
        shutil.copyfile(src_dup_pdf, dest_dup_pdf)
        
        with open(src_dup_gt, "r") as f:
            gt_data = json.load(f)
        gt_data["source_file"] = "duplicate_invoice.pdf"
        
        with open(os.path.join(GT_DIR, "duplicate_invoice.json"), "w") as f:
            json.dump(gt_data, f, indent=2)

    # -------------------------------------------------------------
    # Edge Case D: amount_over_tolerance.pdf
    # Invoice is $1200, PO is $1000 with 5% tolerance (max $1050).
    # -------------------------------------------------------------
    print("Creating amount_over_tolerance.pdf...")
    po_tol_id = "PO-TOL-2026-888"
    po_dataset.append({
        "po_id": po_tol_id,
        "vendor_name": "Apex Office Solutions Ltd.",
        "po_amount": 1000.00,
        "approved_vendor": True,
        "tolerance_pct": 5.0
    })
    
    template = env.get_template("template_2_classic.html")
    inv_data = {
        "invoice_id": "inv_over_tolerance",
        "vendor_name": "Apex Office Solutions Ltd.",
        "vendor_address": "42 High Street, Birmingham, B4 7TA, UK",
        "vendor_email": "accounts@apexoffice.co.uk",
        "client_name": "Zamp Inc.",
        "client_address": "548 Market St, Suite 8802, San Francisco, CA 94104",
        "invoice_number": "INV-TOL-888",
        "invoice_date": "2026-06-20",
        "po_reference": po_tol_id,
        "line_items": [
            {
                "description": "Executive Ergonomic Office Chairs",
                "qty": 5,
                "unit_price": 220.00,
                "amount": 1100.00
            }
        ],
        "tax": 100.00,
        "total": 1200.00
    }
    
    html_content = template.render(**inv_data)
    render_html_to_pdf(html_content, os.path.join(EDGE_DIR, "amount_over_tolerance.pdf"))
    
    gt_data = {
        "invoice_id": inv_data["invoice_id"],
        "vendor_name": inv_data["vendor_name"],
        "invoice_number": inv_data["invoice_number"],
        "invoice_date": inv_data["invoice_date"],
        "po_reference": inv_data["po_reference"],
        "line_items": inv_data["line_items"],
        "tax": inv_data["tax"],
        "total": inv_data["total"],
        "confidence": {
            "overall": 1.0,
            "fields": {
                "invoice_number": 1.0,
                "total": 1.0,
                "vendor_name": 1.0,
                "invoice_date": 1.0,
                "po_reference": 1.0,
                "line_items": 1.0,
                "tax": 1.0
            }
        },
        "source_file": "amount_over_tolerance.pdf",
        "extraction_flags": []
    }
    with open(os.path.join(GT_DIR, "amount_over_tolerance.json"), "w") as f:
        json.dump(gt_data, f, indent=2)

    # -------------------------------------------------------------
    # Edge Case E: no_po_reference.pdf
    # Invoice has no PO reference, and no matching PO in po_dataset.
    # -------------------------------------------------------------
    print("Creating no_po_reference.pdf...")
    template = env.get_template("template_4_minimal.html")
    inv_data = {
        "invoice_id": "inv_no_po_ref",
        "vendor_name": "Vivid Design Lab",
        "vendor_address": "88 Creative Lane, Studio B, Brooklyn, NY 11201",
        "vendor_email": "invoice@vividdesign.co",
        "client_name": "Zamp Inc.",
        "client_address": "548 Market St, Suite 8802, San Francisco, CA 94104",
        "invoice_number": "INV-NO-PO-777",
        "invoice_date": "2026-06-25",
        "po_reference": None,
        "line_items": [
            {
                "description": "User Interface Design Consultant Services",
                "qty": 10,
                "unit_price": 150.00,
                "amount": 1500.00
            }
        ],
        "tax": 123.75,
        "total": 1623.75
    }
    html_content = template.render(**inv_data)
    render_html_to_pdf(html_content, os.path.join(EDGE_DIR, "no_po_reference.pdf"))
    
    gt_data = {
        "invoice_id": inv_data["invoice_id"],
        "vendor_name": inv_data["vendor_name"],
        "invoice_number": inv_data["invoice_number"],
        "invoice_date": inv_data["invoice_date"],
        "po_reference": inv_data["po_reference"],
        "line_items": inv_data["line_items"],
        "tax": inv_data["tax"],
        "total": inv_data["total"],
        "confidence": {
            "overall": 1.0,
            "fields": {
                "invoice_number": 1.0,
                "total": 1.0,
                "vendor_name": 1.0,
                "invoice_date": 1.0,
                "po_reference": 1.0,
                "line_items": 1.0,
                "tax": 1.0
            }
        },
        "source_file": "no_po_reference.pdf",
        "extraction_flags": []
    }
    with open(os.path.join(GT_DIR, "no_po_reference.json"), "w") as f:
        json.dump(gt_data, f, indent=2)

    # -------------------------------------------------------------
    # Save updated PO dataset
    # -------------------------------------------------------------
    with open(PO_FILE, "w") as f:
        json.dump(po_dataset, f, indent=2)
    print(f"Updated PO dataset at {PO_FILE} with edge case PO records.")

    # -------------------------------------------------------------
    # Create README.md in edge_cases directory
    # -------------------------------------------------------------
    readme_content = """# Edge Case Fixtures

This directory contains hand-crafted edge case PDFs designed to test the invoice processing pipeline's robust matching and routing capabilities.

## Test Scenarios

### 1. `ocr_bad_scan.pdf`
* **Purpose**: Tests OCR failure mode and low-confidence field routing.
* **Details**: Created by rasterizing a standard batch invoice to an image, applying artificial Gaussian blur, salt-and-pepper noise, contrast/brightness degradation, and slight rotation before packing back to PDF. It has no digital text layer.
* **Expected Behavior**: OCR confidence will fall below the threshold (specifically on the obscured "total" field), triggering routing to the **Admin Review UI** for manual inspection and correction.

### 2. `po_split_across_invoices_1.pdf` & `po_split_across_invoices_2.pdf`
* **Purpose**: Tests partial fulfillment split matching.
* **Details**: Two separate invoices (each of total $750.00) issued by the same vendor referencing the same PO `PO-SPLIT-2026-999` (amount $1500.00).
* **Expected Behavior**: The PO matching engine should identify that the sum of these invoices matches the PO, auto-approving both as split/partial fulfillments rather than rejecting them as amount mismatches.

### 3. `duplicate_invoice.pdf`
* **Purpose**: Tests duplicate submittal rejection.
* **Details**: A byte-for-byte exact copy of a batch invoice.
* **Expected Behavior**: Rejection by the PO engine due to matching vendor, invoice number, and totals.

### 4. `amount_over_tolerance.pdf`
* **Purpose**: Tests PO tolerance limit violations.
* **Details**: Invoice total is $1200.00, but the matching PO `PO-TOL-2026-888` has an amount of $1000.00 and a `tolerance_pct` of 5.0% (max auto-approve amount is $1050.00).
* **Expected Behavior**: Invoice is flagged for manual review because the difference ($150.00, or 20%) exceeds the allowed PO tolerance.

### 5. `no_po_reference.pdf`
* **Purpose**: Tests handling of invoices missing PO numbers.
* **Details**: The invoice has no PO reference field and there is no matching PO record in `po_dataset.json`.
* **Expected Behavior**: Flagged for review with status "no PO — requires manual sourcing".
"""
    with open(os.path.join(EDGE_DIR, "README.md"), "w") as f:
        f.write(readme_content)
    print("Created edge cases README.md.")

if __name__ == "__main__":
    main()
