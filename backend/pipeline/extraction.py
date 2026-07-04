
import time
import os
from io import BytesIO
import fitz  # PyMuPDF
from PIL import Image
import pytesseract

def extract_pdf(pdf_path: str) -> dict:
    """
    Extracts raw text and computes OCR confidence from a PDF.
    Auto-detects whether the PDF is a text_layer or scanned document.
    
    Returns:
        dict: {
            "raw_text": str,
            "source_type": "text_layer" | "scanned",
            "ocr_confidence": float (0.0 to 1.0),
            "region_confidence": {
                "top": float,
                "middle": float,
                "bottom": float
            },
            "page_count": int,
            "processing_time_ms": float
        }
    """
    start_time = time.time()
    
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
    doc = fitz.open(pdf_path)
    page_count = len(doc)
    
    # 1. Auto-detect source type based on extracted text length
    total_chars = 0
    text_content_list = []
    for page in doc:
        page_text = page.get_text()
        total_chars += len(page_text.strip())
        text_content_list.append(page_text)
        
    # Threshold: if more than 20 characters are present, treat as text-layer
    if total_chars > 20:
        raw_text = "\n".join(text_content_list)
        source_type = "text_layer"
        ocr_confidence = 1.0
        region_confidence = {
            "top": 1.0,
            "middle": 1.0,
            "bottom": 1.0
        }
    else:
        # 2. Scanned path using Tesseract OCR
        source_type = "scanned"
        raw_text_pages = []
        all_confidences = []
        top_confidences = []
        middle_confidences = []
        bottom_confidences = []
        
        for page in doc:
            # Render page to a high-resolution image (150 DPI)
            pix = page.get_pixmap(dpi=150)
            img_data = pix.tobytes("png")
            img = Image.open(BytesIO(img_data)).convert("RGB")
            page_h = img.height
            
            # Extract detailed OCR data (word by word)
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            
            page_words = []
            for i in range(len(data['text'])):
                word = data['text'][i].strip()
                conf_str = data['conf'][i]
                
                try:
                    conf = float(conf_str)
                except (ValueError, TypeError):
                    conf = -1.0
                    
                # Confidences >= 0 are valid OCR predictions for actual words
                if conf >= 0.0:
                    # Tesseract includes empty space markers, filter them
                    if word:
                        page_words.append(word)
                        conf_val = round(conf / 100.0, 4)
                        all_confidences.append(conf_val)
                        
                        # Segregate word confidence by page layout region
                        word_top = data['top'][i]
                        y_ratio = word_top / page_h
                        if y_ratio < 0.3:
                            top_confidences.append(conf_val)
                        elif y_ratio < 0.7:
                            middle_confidences.append(conf_val)
                        else:
                            bottom_confidences.append(conf_val)
                            
            raw_text_pages.append(" ".join(page_words))
            
        raw_text = "\n".join(raw_text_pages)
        
        # Calculate overall and regional confidence averages
        ocr_confidence = round(sum(all_confidences) / len(all_confidences), 4) if all_confidences else 0.0
        
        region_confidence = {
            "top": round(sum(top_confidences) / len(top_confidences), 4) if top_confidences else 0.0,
            "middle": round(sum(middle_confidences) / len(middle_confidences), 4) if middle_confidences else 0.0,
            "bottom": round(sum(bottom_confidences) / len(bottom_confidences), 4) if bottom_confidences else 0.0
        }
        
    processing_time_ms = round((time.time() - start_time) * 1000, 2)
    
    return {
        "raw_text": raw_text,
        "source_type": source_type,
        "ocr_confidence": ocr_confidence,
        "region_confidence": region_confidence,
        "page_count": page_count,
        "processing_time_ms": processing_time_ms
    }

if __name__ == "__main__":
    import sys
    import json
    
    # Use the first command line argument, or default to a sample invoice
    if len(sys.argv) > 1:
        target_path = sys.argv[1]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        target_path = os.path.join(script_dir, "..", "..", "fixtures", "batch", "inv_100000.pdf")
        
    print(f"Running extract_pdf on: {target_path}")
    try:
        res = extract_pdf(target_path)
        # Create a preview of raw_text (first 250 characters) to avoid flooding console
        text_preview = res["raw_text"][:250] + "..." if len(res["raw_text"]) > 250 else res["raw_text"]
        
        output_res = {
            "source_type": res["source_type"],
            "ocr_confidence": res["ocr_confidence"],
            "region_confidence": res["region_confidence"],
            "page_count": res["page_count"],
            "processing_time_ms": res["processing_time_ms"],
            "raw_text_preview": text_preview.replace("\n", " ")
        }
        print(json.dumps(output_res, indent=2))
    except Exception as e:
        print(f"Error: {e}")

