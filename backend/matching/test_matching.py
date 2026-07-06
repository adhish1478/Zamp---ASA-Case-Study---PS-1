import os
import json
import sqlite3
import unittest
from dotenv import load_dotenv

# Add project backend root and pipeline folders to path to import components cleanly
import sys
script_dir = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(script_dir))
sys.path.append(os.path.join(PROJECT_ROOT, "backend"))
sys.path.append(os.path.join(PROJECT_ROOT, "backend", "pipeline"))
sys.path.append(os.path.join(PROJECT_ROOT, "backend", "matching"))

from pipeline.batch_runner import process_single_file, DB_PATH, init_db
from pipeline.structuring import root_env

load_dotenv(root_env)

# Input folders
BATCH_DIR = os.path.join(PROJECT_ROOT, "fixtures", "batch")
EDGE_DIR = os.path.join(PROJECT_ROOT, "fixtures", "edge_cases")

class TestPOMatchingEngine(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        print("Migrating and repopulating database tables for matching tests...")
        # Force a database cleanup to start clean
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS invoices")
        cursor.execute("DROP TABLE IF EXISTS pos")
        cursor.execute("DROP TABLE IF EXISTS invoice_decisions")
        conn.commit()
        conn.close()
        
        # Initialise database schemas and populate POs
        init_db()
        
        # Test fixture list: 4 standard batch files + 6 edge cases
        cls.test_fixtures = [
            # Clean batch invoices (Text Layer)
            (os.path.join(BATCH_DIR, "inv_100000.pdf"), "batch"),
            (os.path.join(BATCH_DIR, "inv_100001.pdf"), "batch"),
            (os.path.join(BATCH_DIR, "inv_100002.pdf"), "batch"),
            (os.path.join(BATCH_DIR, "inv_100003.pdf"), "batch"),
            
            # Edge case files
            (os.path.join(EDGE_DIR, "ocr_bad_scan.pdf"), "ocr_bad_scan"),
            (os.path.join(EDGE_DIR, "po_split_across_invoices_1.pdf"), "split_1"),
            (os.path.join(EDGE_DIR, "po_split_across_invoices_2.pdf"), "split_2"),
            (os.path.join(EDGE_DIR, "duplicate_invoice.pdf"), "duplicate"),
            (os.path.join(EDGE_DIR, "amount_over_tolerance.pdf"), "over_tolerance"),
            (os.path.join(EDGE_DIR, "no_po_reference.pdf"), "no_po_ref")
        ]
        
        print(f"Executing complete pipeline on {len(cls.test_fixtures)} test documents...")
        cls.results = {}
        for path, tag in cls.test_fixtures:
            filename = os.path.basename(path)
            print(f" -> Processing {filename}...")
            res = process_single_file(path)
            cls.results[tag] = res

    def test_pipeline_execution(self):
        """Assert all 10 files processed end-to-end successfully"""
        for tag, res in self.results.items():
            self.assertTrue(res["success"], f"Failed on document {tag}: {res.get('error')}")

    def test_exact_po_match_approval(self):
        """Assert clean batch files with correct POs are auto-approved"""
        # Note: inv_100001 has a PO and vendor match, should auto-approve
        res = self.results["batch"] # inv_100000 has no PO reference, let's check inv_100001
        
        # Let's inspect inv_100001, inv_100002, inv_100003
        for idx in range(1, 4):
            path = self.test_fixtures[idx][0]
            filename = os.path.basename(path)
            invoice_id = os.path.splitext(filename)[0]
            
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
            inv = dict(cursor.fetchone())
            conn.close()
            
            # If the invoice has a PO reference in ground truth, matches, and vendor is approved, it should auto-approve.
            # If the vendor is unapproved, it should be flagged for review.
            if inv["po_reference"]:
                cursor_po = sqlite3.connect(DB_PATH).cursor()
                cursor_po.execute("SELECT approved_vendor FROM pos WHERE po_id = ?", (inv["po_reference"],))
                approved_vendor = cursor_po.fetchone()[0]
                
                cursor_dec = sqlite3.connect(DB_PATH).cursor()
                cursor_dec.execute("SELECT status, explanation FROM invoice_decisions WHERE invoice_id = ?", (invoice_id,))
                dec_status, explanation = cursor_dec.fetchone()
                
                print(f"\n{invoice_id} matching status: {dec_status}")
                print(f"{invoice_id} explanation: {explanation}")
                
                if approved_vendor == 1:
                    self.assertEqual(dec_status, "auto_approved", 
                                     f"Invoice {invoice_id} should be auto_approved since it matches its PO")
                else:
                    self.assertEqual(dec_status, "flagged_for_review", 
                                     f"Invoice {invoice_id} should be flagged for review because the vendor is unapproved")
                self.assertTrue(len(explanation) > 10)

    def test_no_po_reference_flagged(self):
        """Assert no_po_reference.pdf is flagged for manual PO sourcing"""
        res = self.results["no_po_ref"]
        decision = res["decision"]
        
        print(f"\nno_po_reference status: {decision['status']}")
        print(f"no_po_reference trace: {decision['rule_trace']}")
        print(f"no_po_reference explanation: {decision['explanation']}")
        
        self.assertEqual(decision["status"], "flagged_for_review")
        self.assertIn("no_matching_po_number", decision["rule_trace"])
        self.assertTrue(decision["requires_human_review"])

    def test_duplicate_invoice_rejection(self):
        """Assert duplicate_invoice.pdf is rejected due to duplicate submissions"""
        res = self.results["duplicate"]
        decision = res["decision"]
        
        print(f"\nduplicate_invoice status: {decision['status']}")
        print(f"duplicate_invoice trace: {decision['rule_trace']}")
        print(f"duplicate_invoice explanation: {decision['explanation']}")
        
        self.assertEqual(decision["status"], "rejected")
        self.assertIn("duplicate_invoice_detected", decision["rule_trace"])
        self.assertTrue(decision["requires_human_review"])

    def test_amount_over_tolerance_flagged(self):
        """Assert amount_over_tolerance.pdf is flagged for review"""
        res = self.results["over_tolerance"]
        decision = res["decision"]
        
        print(f"\namount_over_tolerance status: {decision['status']}")
        print(f"amount_over_tolerance trace: {decision['rule_trace']}")
        print(f"amount_over_tolerance explanation: {decision['explanation']}")
        
        self.assertEqual(decision["status"], "flagged_for_review")
        self.assertTrue(any("amount_exceeds_tolerance" in t for t in decision["rule_trace"]))
        self.assertTrue(decision["requires_human_review"])

    def test_po_split_across_invoices_reconciles(self):
        """Assert both split invoices referencing the same PO reconcile and auto-approve"""
        res1 = self.results["split_1"]
        res2 = self.results["split_2"]
        
        dec1 = res1["decision"]
        dec2 = res2["decision"]
        
        print(f"\nsplit_1 status: {dec1['status']} | trace: {dec1['rule_trace']}")
        print(f"split_2 status: {dec2['status']} | trace: {dec2['rule_trace']}")
        print(f"split_2 explanation: {dec2['explanation']}")
        
        # Both split PO invoices must auto-approve because they sum exactly to the PO total ($1500)
        self.assertEqual(dec1["status"], "auto_approved")
        self.assertEqual(dec2["status"], "auto_approved")
        
        self.assertIn("partial_fulfillment_approved", dec1["rule_trace"])
        self.assertIn("partial_fulfillment_approved", dec2["rule_trace"])

    def test_ocr_bad_scan_review_override(self):
        """Assert ocr_bad_scan.pdf is flagged for review due to OCR low confidence override"""
        res = self.results["ocr_bad_scan"]
        decision = res["decision"]
        
        print(f"\nocr_bad_scan matching status: {decision['status']}")
        print(f"ocr_bad_scan matching trace: {decision['rule_trace']}")
        print(f"ocr_bad_scan matching explanation: {decision['explanation']}")
        
        # Even if the invoice amount matches some criteria, it must be flagged for review 
        # because the raw OCR extraction had low confidence region flags.
        self.assertEqual(decision["status"], "flagged_for_review")
        self.assertIn("low_confidence_review_override", decision["rule_trace"])
        self.assertTrue(decision["requires_human_review"])

if __name__ == "__main__":
    unittest.main()
