import os
import sqlite3
import unittest
from batch_runner import run_batch, DB_PATH

class TestExtractionPipeline(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        print("Running batch runner to populate database and collect results...")
        cls.results = run_batch()
        
    def test_no_unhandled_exceptions(self):
        """Assert no files failed with an unhandled exception during processing"""
        failed_files = [r for r in self.results if not r["success"]]
        self.assertEqual(len(failed_files), 0, f"Some files failed to process: {failed_files}")
        
    def test_non_empty_raw_text(self):
        """Assert that every processed PDF produces non-empty raw text"""
        for r in self.results:
            if r["success"]:
                raw_text = r["result"]["raw_text"].strip()
                self.assertTrue(len(raw_text) > 0, f"File {r['source_file']} produced empty text layer")

    def test_ocr_bad_scan_detection(self):
        """Assert that ocr_bad_scan.pdf is detected as scanned and has lower confidence"""
        bad_scan_result = None
        text_layer_confidences = []
        
        for r in self.results:
            if r["success"]:
                res_detail = r["result"]
                if r["source_file"] == "ocr_bad_scan.pdf":
                    bad_scan_result = res_detail
                elif res_detail["source_type"] == "text_layer":
                    text_layer_confidences.append(res_detail["ocr_confidence"])
                    
        self.assertIsNotNone(bad_scan_result, "ocr_bad_scan.pdf not found in results")
        
        # 1. Assert it is scanned
        self.assertEqual(bad_scan_result["source_type"], "scanned", 
                         f"ocr_bad_scan.pdf was detected as {bad_scan_result['source_type']} instead of scanned")
        
        # 2. Assert lower confidence than text layer average
        avg_text_conf = sum(text_layer_confidences) / len(text_layer_confidences) if text_layer_confidences else 1.0
        bad_scan_conf = bad_scan_result["ocr_confidence"]
        
        print(f"\nAverage Text-Layer OCR Confidence: {avg_text_conf:.4f}")
        print(f"ocr_bad_scan.pdf OCR Confidence:     {bad_scan_conf:.4f}")
        
        self.assertLess(bad_scan_conf, avg_text_conf, 
                        f"ocr_bad_scan.pdf confidence ({bad_scan_conf}) should be lower than average text-layer confidence ({avg_text_conf})")
        
    def test_sqlite_storage(self):
        """Assert that database contains the expected number of invoices and correct schemas"""
        self.assertTrue(os.path.exists(DB_PATH), f"Database file not found at {DB_PATH}")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM invoices")
        count = cursor.fetchone()[0]
        
        # Expected total is 90 batch invoices + 6 edge cases = 96 total
        expected_count = 96
        self.assertEqual(count, expected_count, 
                         f"Database row count ({count}) does not match expected processed fixtures ({expected_count})")
        
        # Retrieve and inspect a row schema
        cursor.execute("PRAGMA table_info(invoices)")
        columns = {col[1]: col[2] for col in cursor.fetchall()}
        
        required_cols = {
            "invoice_id": "TEXT",
            "source_file": "TEXT",
            "raw_text": "TEXT",
            "source_type": "TEXT",
            "ocr_confidence": "REAL",
            "page_count": "INTEGER"
        }
        
        for col, expected_type in required_cols.items():
            self.assertIn(col, columns, f"Missing required column: {col}")
            # SQLite type affinity check
            self.assertEqual(columns[col], expected_type, f"Column {col} has type {columns[col]} instead of {expected_type}")
            
        conn.close()

if __name__ == "__main__":
    unittest.main()
