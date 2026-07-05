import os
import json
import sqlite3
import unittest
from dotenv import load_dotenv
from batch_runner import process_single_file, DB_PATH, init_db

# Load root .env
script_dir = os.path.dirname(os.path.abspath(__file__))
root_env = os.path.join(script_dir, "..", "..", ".env")
load_dotenv(root_env)

# Input folders
PROJECT_ROOT = os.path.dirname(os.path.dirname(script_dir))
BATCH_DIR = os.path.join(PROJECT_ROOT, "fixtures", "batch")
EDGE_DIR = os.path.join(PROJECT_ROOT, "fixtures", "edge_cases")

class TestStructuringPipeline(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        # Initialise database schema
        init_db()
        
        # We select a representative subset of files to save OpenAI API costs and speed up tests
        cls.target_files = [
            # Batch invoices (Text Layer)
            (os.path.join(BATCH_DIR, "inv_100000.pdf"), True),
            (os.path.join(BATCH_DIR, "inv_100001.pdf"), True),
            (os.path.join(BATCH_DIR, "inv_100002.pdf"), True),
            (os.path.join(BATCH_DIR, "inv_100003.pdf"), True),
            
            # Edge cases
            (os.path.join(EDGE_DIR, "ocr_bad_scan.pdf"), False),
            (os.path.join(EDGE_DIR, "po_split_across_invoices_1.pdf"), False),
            (os.path.join(EDGE_DIR, "po_split_across_invoices_2.pdf"), False),
            (os.path.join(EDGE_DIR, "duplicate_invoice.pdf"), False),
            (os.path.join(EDGE_DIR, "amount_over_tolerance.pdf"), False),
            (os.path.join(EDGE_DIR, "no_po_reference.pdf"), False)
        ]
        
        print(f"Running pipeline on {len(cls.target_files)} select test fixtures...")
        cls.results = []
        for path, is_batch in cls.target_files:
            print(f" -> Processing {os.path.basename(path)}...")
            res = process_single_file(path)
            cls.results.append((path, is_batch, res))
            
    def test_pipeline_execution_success(self):
        """Assert all files processed without raising unhandled python errors"""
        for path, _, res in self.results:
            self.assertTrue(res["success"], f"Failed to process {os.path.basename(path)}: {res.get('error')}")

    def test_batch_accuracy_against_ground_truth(self):
        """Compare Stage 2 LLM extraction against Ground Truth JSON for clean batch files"""
        for path, is_batch, res in self.results:
            if not is_batch or not res["success"]:
                continue
                
            filename = os.path.basename(path)
            gt_filename = os.path.splitext(filename)[0] + ".json"
            gt_path = os.path.join(BATCH_DIR, "ground_truth", gt_filename)
            
            self.assertTrue(os.path.exists(gt_path), f"Ground truth not found: {gt_path}")
            with open(gt_path, "r") as f:
                gt = json.load(f)
                
            extracted = res["stage2"]
            
            # Assert vendor matches (ignore case and minor spacing differences)
            self.assertEqual(
                extracted["vendor_name"].lower().replace(".", "").strip(),
                gt["vendor_name"].lower().replace(".", "").strip(),
                f"Vendor mismatch on {filename}"
            )
            
            # Assert invoice number matches
            self.assertEqual(extracted["invoice_number"], gt["invoice_number"], 
                             f"Invoice number mismatch on {filename}")
            
            # Assert invoice date matches
            self.assertEqual(extracted["invoice_date"], gt["invoice_date"], 
                             f"Invoice date mismatch on {filename}")
            
            # Assert total and tax match (allowing slight floating point variation)
            self.assertAlmostEqual(extracted["total"], gt["total"], places=1, 
                                   msg=f"Total mismatch on {filename}")
            if gt["tax"] is not None:
                self.assertAlmostEqual(extracted["tax"], gt["tax"], places=1, 
                                       msg=f"Tax mismatch on {filename}")
                
            # Assert number of line items matches
            self.assertEqual(len(extracted["line_items"]), len(gt["line_items"]), 
                             f"Line items count mismatch on {filename}")

    def test_ocr_bad_scan_review_routing(self):
        """Assert ocr_bad_scan.pdf triggers low composite confidence and flags review"""
        bad_scan_res = None
        for path, _, res in self.results:
            if os.path.basename(path) == "ocr_bad_scan.pdf":
                bad_scan_res = res
                break
                
        self.assertIsNotNone(bad_scan_res, "ocr_bad_scan.pdf result not found")
        self.assertTrue(bad_scan_res["success"])
        
        confidence = bad_scan_res["confidence"]
        
        # The blurred totals smudge should trigger the 'low_ocr_confidence_region' flag
        # and set 'requires_human_review' to True.
        print(f"\nocr_bad_scan composite confidence: {confidence['overall']}")
        print(f"ocr_bad_scan flags: {confidence['extraction_flags']}")
        
        self.assertTrue(confidence["requires_human_review"], 
                        "ocr_bad_scan.pdf should be flagged as requiring human review")
        
    def test_no_po_reference_handling(self):
        """Assert no_po_reference.pdf extracts with a null po_reference"""
        no_po_res = None
        for path, _, res in self.results:
            if os.path.basename(path) == "no_po_reference.pdf":
                no_po_res = res
                break
                
        self.assertIsNotNone(no_po_res, "no_po_reference.pdf result not found")
        self.assertTrue(no_po_res["success"])
        
        extracted = no_po_res["stage2"]
        self.assertIsNone(extracted["po_reference"], 
                          f"Expected po_reference to be None, got: {extracted['po_reference']}")

    def test_sqlite_data_correctness(self):
        """Assert database contains populated records for all structured fields"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # We processed 10 select files, let's verify they are written and fields exist
        for path, _, _ in self.results:
            filename = os.path.basename(path)
            invoice_id = os.path.splitext(filename)[0]
            
            cursor.execute("""
                SELECT vendor_name, invoice_number, total, composite_confidence, requires_human_review 
                FROM invoices WHERE invoice_id = ?
            """, (invoice_id,))
            row = cursor.fetchone()
            
            self.assertIsNotNone(row, f"Invoice {invoice_id} not found in SQLite database")
            
            vendor, inv_num, total, comp_conf, review = row
            self.assertTrue(len(vendor) > 0)
            self.assertIsNotNone(total)
            self.assertTrue(0.0 <= comp_conf <= 1.0)
            self.assertIn(review, [0, 1])
            
        conn.close()

if __name__ == "__main__":
    unittest.main()
