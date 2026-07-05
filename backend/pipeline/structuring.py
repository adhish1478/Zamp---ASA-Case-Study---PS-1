import os
import json
import re
import logging
from dotenv import load_dotenv
from openai import OpenAI

# Load root .env
script_dir = os.path.dirname(os.path.abspath(__file__))
root_env = os.path.join(script_dir, "..", "..", ".env")
load_dotenv(root_env)

# Configure logger
logger = logging.getLogger(__name__)

# Initialize OpenAI client
# It will read the OPENAI_API_KEY environment variable loaded by dotenv
client = None

def get_openai_client():
    global client
    if client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key or api_key == "your_openai_api_key_here":
            raise ValueError(
                "OPENAI_API_KEY environment variable is missing or set to placeholder. "
                "Please configure it in the root .env file."
            )
        client = OpenAI(api_key=api_key)
    return client

def clean_extracted_data(data: dict) -> dict:
    """
    Cleans and standardizes the types and formatting of the LLM-extracted invoice data.
    """
    cleaned = {}
    
    # 1. Standard strings or null
    vendor_name = str(data.get("vendor_name", "")).strip() or "Unknown Vendor"
    cleaned["vendor_name"] = vendor_name
    
    # Clean common prefix markers like #, No, ID: from invoice number
    inv_num = str(data.get("invoice_number", "")).strip()
    inv_num = re.sub(r"^(?:#|No\.?|No:|ID:?)\s*", "", inv_num, flags=re.IGNORECASE)
    cleaned["invoice_number"] = inv_num or None
    
    cleaned["invoice_date"] = str(data.get("invoice_date", "")).strip() or None
    
    # Clean common prefix markers from PO Reference
    po_ref = str(data.get("po_reference", "")).strip()
    po_ref = re.sub(r"^(?:#|No\.?|No:|ID:?)\s*", "", po_ref, flags=re.IGNORECASE)
    cleaned["po_reference"] = po_ref or None
    
    # Clean special cases of PO Reference (like 'N/A' or 'None')
    if cleaned["po_reference"] in ["N/A", "n/a", "None", "none", "NULL", "null", ""]:
        cleaned["po_reference"] = None
        
    # 2. Parsing floats safely
    def parse_float(val):
        if val is None or val == "":
            return None
        if isinstance(val, (int, float)):
            return round(float(val), 2)
        # Handle string parsing
        try:
            s = str(val).replace("$", "").replace(",", "").strip()
            return round(float(s), 2)
        except ValueError:
            return None

    cleaned["tax"] = parse_float(data.get("tax"))
    cleaned["total"] = parse_float(data.get("total"))
    
    # 3. Clean line items
    raw_items = data.get("line_items", [])
    if not isinstance(raw_items, list):
        raw_items = []
        
    cleaned_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        
        desc = str(item.get("description", "")).strip() or "Item Description"
        
        # Quantity parsing
        qty_val = item.get("qty")
        if isinstance(qty_val, (int, float)):
            qty = float(qty_val)
        else:
            try:
                qty = float(str(qty_val).replace(",", "").strip())
            except ValueError:
                qty = 1.0
                
        # Price parsing
        price = parse_float(item.get("unit_price")) or 0.0
        
        # Amount parsing or auto-calculation
        amount = parse_float(item.get("amount"))
        if amount is None:
            amount = round(qty * price, 2)
            
        cleaned_items.append({
            "description": desc,
            "qty": qty,
            "unit_price": price,
            "amount": amount
        })
        
    cleaned["line_items"] = cleaned_items
    
    return cleaned

def structure_invoice(raw_text: str, low_confidence_regions: bool = False) -> dict:
    """
    Calls OpenAI GPT-4o-mini to structure raw unstructured invoice text.
    
    Args:
        raw_text (str): The raw text extracted from PDF.
        low_confidence_regions (bool): Flags if Stage 1 detected low-quality OCR.
        
    Returns:
        dict: Standardized structured invoice data.
    """
    openai_client = get_openai_client()
    
    # Define JSON output schema specification in system prompt
    system_prompt = (
        "You are an expert AI system specialized in parsing raw invoice text and structuring "
        "it into a strict JSON schema. Extract and output the following JSON structure:\n\n"
        "{\n"
        "  \"vendor_name\": \"string (name of the company issuing the invoice)\",\n"
        "  \"invoice_number\": \"string or null (invoice number or ID)\",\n"
        "  \"invoice_date\": \"string or null (date of invoice formatted as YYYY-MM-DD)\",\n"
        "  \"po_reference\": \"string or null (the purchase order number referenced in the text)\",\n"
        "  \"line_items\": [\n"
        "    {\n"
        "      \"description\": \"string\",\n"
        "      \"qty\": number,\n"
        "      \"unit_price\": number,\n"
        "      \"amount\": number\n"
        "    }\n"
        "  ],\n"
        "  \"tax\": number or null,\n"
        "  \"total\": number or null\n"
        "}\n\n"
        "Return ONLY the raw JSON object. Do not include markdown code block syntax (like ```json). "
        "Extract fields exactly as they appear. Do not auto-correct spellings inside descriptions."
    )
    
    # Confidence-aware retry warning injection
    if low_confidence_regions:
        system_prompt += (
            "\n\nCRITICAL WARNING: The input text was extracted from a scanned document with high noise "
            "or blurred text. Word recognition is uncertain. Do NOT guess or hallucinate numbers or dates "
            "if they are unreadable, cut off, or smeared. If a field is illegible or missing, "
            "set its value to null. Prioritize safety over guessing."
        )

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Extract invoice data from the following raw text:\n\n{raw_text}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        
        raw_json_str = response.choices[0].message.content
        raw_data = json.loads(raw_json_str)
        return clean_extracted_data(raw_data)
        
    except Exception as e:
        logger.error(f"Error in OpenAI GPT structuring completion: {e}")
        # Return empty structured schema fallback
        return {
            "vendor_name": "Unknown Vendor",
            "invoice_number": None,
            "invoice_date": None,
            "po_reference": None,
            "line_items": [],
            "tax": None,
            "total": None
        }

if __name__ == "__main__":
    # Small test when run directly
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python structuring.py <raw_text_file_or_string>")
        sys.exit(1)
        
    input_val = sys.argv[1]
    if os.path.exists(input_val):
        with open(input_val, "r") as f:
            text = f.read()
    else:
        text = input_val
        
    print("Testing structuring on input text...")
    try:
        res = structure_invoice(text)
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(f"Error: {e}")
