import os
import json
import random
from datetime import datetime, timedelta
from faker import Faker
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

fake = Faker()

# Define output directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BATCH_DIR = os.path.join(BASE_DIR, "batch")
GT_DIR = os.path.join(BATCH_DIR, "ground_truth")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

os.makedirs(BATCH_DIR, exist_ok=True)
os.makedirs(GT_DIR, exist_ok=True)

# Initialize Jinja2 environment
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))

# Template filenames
TEMPLATES = [
    "template_1_tech.html",
    "template_2_classic.html",
    "template_3_retail.html",
    "template_4_minimal.html",
    "template_5_utility.html",
    "template_6_consulting.html"
]

# Realistic vendor items
TECH_ITEMS = [
    ("Cloud Server Compute (m5.xlarge) - US East", 120.00),
    ("Database Storage Backup Service", 45.50),
    ("API Integration Gateway License", 250.00),
    ("SSL Certificate Multi-Domain Renewal", 89.99),
    ("Premium Support Plan (Monthly)", 499.00),
    ("Data Warehousing Query Credits", 0.15)
]

CLASSIC_ITEMS = [
    ("Office Stationery and Paper Supplies", 35.00),
    ("Ergonomic Mesh Task Chairs", 185.00),
    ("LED Flat Panel Desk Lamps", 42.50),
    ("Breakroom Coffee and Tea Restock", 75.00),
    ("Document Shredding & Disposal Service", 120.00)
]

RETAIL_ITEMS = [
    ("Standard HDMI Cables 6ft (Pack of 10)", 45.00),
    ("USB-C Charging Hubs 65W", 29.99),
    ("Wireless Keyboard and Mouse Combo", 39.50),
    ("Privacy Screen Filters 14-inch", 25.00),
    ("Gigabit Ethernet Switch 8-Port", 55.00)
]

MINIMAL_ITEMS = [
    ("User Experience Consulting (Hours)", 150.00),
    ("Brand Identity Redesign Workshop", 2500.00),
    ("Digital Interface Styleguide Development", 1200.00),
    ("User Testing Moderation & Recruiting", 950.00)
]

UTILITY_ITEMS = [
    ("High-Speed Fiber Optic Internet 1Gbps", 189.00),
    ("VoIP Phone Lines (5 Extension Bundle)", 75.00),
    ("Dedicated Business IP Address Allocation", 15.00),
    ("SMS Notification API Gateway Credits", 0.02)
]

CONSULTING_ITEMS = [
    ("Senior Software Engineering (Hours)", 125.00),
    ("Solutions Architecture Review (Hours)", 175.00),
    ("DevOps CI/CD Pipeline Audit (Hours)", 150.00),
    ("Project Management & Coordination (Hours)", 95.00)
]

ITEM_MAPPING = {
    "template_1_tech.html": TECH_ITEMS,
    "template_2_classic.html": CLASSIC_ITEMS,
    "template_3_retail.html": RETAIL_ITEMS,
    "template_4_minimal.html": MINIMAL_ITEMS,
    "template_5_utility.html": UTILITY_ITEMS,
    "template_6_consulting.html": CONSULTING_ITEMS
}

# Fixed vendors to make the dataset cohesive
VENDORS = [
    {"name": "Nova Cloud Systems Inc.", "email": "billing@novacloud.com", "address": "100 Innovation Way, Suite 400, Boston, MA 02110"},
    {"name": "Apex Office Solutions Ltd.", "email": "accounts@apexoffice.co.uk", "address": "42 High Street, Birmingham, B4 7TA, UK"},
    {"name": "Quantum Tech Wholesalers", "email": "sales@quantumtech.com", "address": "777 Silicon Boulevard, San Jose, CA 95112"},
    {"name": "Vivid Design Lab", "email": "invoice@vividdesign.co", "address": "88 Creative Lane, Studio B, Brooklyn, NY 11201"},
    {"name": "Global Telecom Services", "email": "utility-billing@globaltel.net", "address": "500 Main Street, Dallas, TX 75201"},
    {"name": "Beacon Software Advisory", "email": "partner-billing@beaconadvisory.com", "address": "12 Financial District, San Francisco, CA 94104"}
]

def generate_random_invoice_data(index, template_name):
    # Determine vendor based on template index to keep it consistent
    vendor_idx = index % len(VENDORS)
    vendor = VENDORS[vendor_idx]
    
    # Generate invoice metadata
    inv_num = f"INV-2026-{100000 + index}"
    
    # Random date within last 6 months
    days_ago = random.randint(1, 180)
    inv_date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    
    # Determine PO Reference
    # 85% of invoices have a valid PO reference, others have None or invalid format
    has_po = random.random() < 0.85
    po_ref = f"PO-2026-{200000 + index}" if has_po else None
    
    # If this is template 3, po reference is 85% likely but we also have a random no_po case
    # If index is a specific modulo, set po_ref = None to test missing PO reference cases
    if index % 13 == 0:
        po_ref = None
        
    # Generate line items
    possible_items = ITEM_MAPPING[template_name]
    num_items = random.randint(1, 4)
    selected_items = random.sample(possible_items, min(num_items, len(possible_items)))
    
    line_items = []
    subtotal = 0.0
    for desc, unit_price in selected_items:
        # Determine quantity: consulting templates (4, 6) usually hourly, others integer count
        if template_name in ["template_4_minimal.html", "template_6_consulting.html"] and unit_price > 50:
            qty = round(random.uniform(5.0, 40.0), 1)
        else:
            qty = random.randint(1, 20)
            
        amount = round(qty * unit_price, 2)
        subtotal += amount
        line_items.append({
            "description": desc,
            "qty": qty,
            "unit_price": unit_price,
            "amount": amount
        })
        
    subtotal = round(subtotal, 2)
    
    # Calculate tax and total based on template style
    if template_name == "template_3_retail.html":
        # Tax is embedded in the price, so total = subtotal, and tax is a fraction of it
        total = subtotal
        tax = round(total * 0.0825, 2) # 8.25% included
    else:
        # Tax is separate
        tax_rate = 0.0825 # 8.25% tax
        tax = round(subtotal * tax_rate, 2)
        total = round(subtotal + tax, 2)
        
    return {
        "invoice_id": f"inv_{100000 + index}",
        "vendor_name": vendor["name"],
        "vendor_address": vendor["address"],
        "vendor_email": vendor["email"],
        "client_name": "Zamp Inc.",
        "client_address": "548 Market St, Suite 8802, San Francisco, CA 94104",
        "invoice_number": inv_num,
        "invoice_date": inv_date,
        "po_reference": po_ref,
        "line_items": line_items,
        "tax": tax,
        "total": total
    }

def main():
    print("Generating synthetic invoices dataset...")
    
    invoices_data = []
    po_dataset = []
    
    # Generate 90 invoices
    total_invoices = 90
    for i in range(total_invoices):
        template_name = TEMPLATES[i % len(TEMPLATES)]
        inv_data = generate_random_invoice_data(i, template_name)
        invoices_data.append((template_name, inv_data))
        
        # Create a PO mapping if po_reference exists
        if inv_data["po_reference"]:
            # approved vendor status: 95% approved
            approved = random.random() < 0.95
            # tolerance: 2%, 5%, or 10%
            tolerance = random.choice([2.0, 5.0, 10.0])
            
            # Most POs match the invoice amount exactly
            po_amount = inv_data["total"]
            
            # Add PO record
            po_dataset.append({
                "po_id": inv_data["po_reference"],
                "vendor_name": inv_data["vendor_name"],
                "po_amount": po_amount,
                "approved_vendor": approved,
                "tolerance_pct": tolerance
            })

    # Save PO dataset
    po_file = os.path.join(BASE_DIR, "po_dataset.json")
    with open(po_file, "w") as f:
        json.dump(po_dataset, f, indent=2)
    print(f"Generated PO dataset with {len(po_dataset)} POs at {po_file}")
    
    # Render PDFs using Playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        for idx, (template_name, data) in enumerate(invoices_data):
            # Render Jinja2 template to HTML string
            template = env.get_template(template_name)
            html_content = template.render(**data)
            
            # Write temporary HTML file to load in Playwright
            temp_html_path = os.path.join(BASE_DIR, f"temp_{idx}.html")
            with open(temp_html_path, "w", encoding="utf-8") as f:
                f.write(html_content)
                
            # Open temporary HTML in browser and print to PDF
            page.goto(f"file://{temp_html_path}")
            
            filename = f"inv_{100000 + idx}"
            pdf_path = os.path.join(BATCH_DIR, f"{filename}.pdf")
            
            page.pdf(path=pdf_path, format="A4", print_background=True)
            
            # Clean up temporary HTML
            if os.path.exists(temp_html_path):
                os.remove(temp_html_path)
                
            # Create ground truth JSON
            gt_data = {
                "invoice_id": data["invoice_id"],
                "vendor_name": data["vendor_name"],
                "invoice_number": data["invoice_number"],
                "invoice_date": data["invoice_date"],
                "po_reference": data["po_reference"],
                "line_items": data["line_items"],
                "tax": data["tax"],
                "total": data["total"],
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
                "source_file": f"{filename}.pdf",
                "extraction_flags": []
            }
            
            gt_path = os.path.join(GT_DIR, f"{filename}.json")
            with open(gt_path, "w") as f:
                json.dump(gt_data, f, indent=2)
                
            if (idx + 1) % 10 == 0:
                print(f"Rendered {idx + 1}/{total_invoices} PDFs and ground truths...")
                
        browser.close()
        
    print("Successfully generated all synthetic batch fixtures!")

if __name__ == "__main__":
    main()
