import os
import json
import sqlite3
import unittest
from fastapi.testclient import TestClient
from dotenv import load_dotenv

import sys
script_dir = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(script_dir)
sys.path.append(os.path.join(PROJECT_ROOT, "backend"))
sys.path.append(os.path.join(PROJECT_ROOT, "backend", "pipeline"))
sys.path.append(os.path.join(PROJECT_ROOT, "backend", "matching"))

from api import app, DB_PATH

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

class TestInvoiceAPI(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.test_pdf_path = os.path.join(PROJECT_ROOT, "fixtures", "batch", "inv_100000.pdf")

    def test_health_endpoint(self):
        """Assert GET /api/health is online and reports database exists"""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertTrue(data["database"])

    def test_get_invoices_endpoint(self):
        """Assert GET /api/invoices returns valid JSON list with deserialized fields"""
        response = self.client.get("/api/invoices")
        self.assertEqual(response.status_code, 200)
        invoices = response.json()
        self.assertIsInstance(invoices, list)
        
        # If there are records in the database, verify fields exist and are deserialized
        if len(invoices) > 0:
            inv = invoices[0]
            self.assertIn("invoice_id", inv)
            self.assertIn("vendor_name", inv)
            self.assertIn("line_items", inv)
            self.assertIsInstance(inv["line_items"], list)
            self.assertIn("rule_trace", inv)
            self.assertIsInstance(inv["rule_trace"], list)

    def test_upload_endpoint(self):
        """Assert POST /api/upload accepts a file, launches background task, and returns results via polling"""
        self.assertTrue(os.path.exists(self.test_pdf_path), "Test PDF not found.")
        
        with open(self.test_pdf_path, "rb") as f:
            response = self.client.post(
                "/api/upload",
                files={"files": ("inv_100000.pdf", f, "application/pdf")}
            )
            
        self.assertEqual(response.status_code, 200)
        init_data = response.json()
        self.assertIn("batch_id", init_data)
        self.assertEqual(init_data["status"], "queued")
        
        batch_id = init_data["batch_id"]
        
        # Poll status until completed
        import time
        max_attempts = 15
        completed = False
        batch_data = None
        
        for _ in range(max_attempts):
            status_resp = self.client.get(f"/api/upload/status/{batch_id}")
            self.assertEqual(status_resp.status_code, 200)
            batch_data = status_resp.json()
            if batch_data["status"] == "completed":
                completed = True
                break
            time.sleep(0.5)
            
        self.assertTrue(completed, "Batch did not complete within timeout.")
        
        # Check files results
        self.assertIn("inv_100000.pdf", batch_data["files"])
        file_res = batch_data["files"]["inv_100000.pdf"]
        self.assertEqual(file_res["status"], "completed")
        self.assertIn("result", file_res)
        
        res = file_res["result"]
        self.assertTrue(res["success"])
        self.assertEqual(res["invoice_id"], "inv_100000")
        self.assertIn("decision", res)

    def test_review_endpoint(self):
        """Assert POST /api/invoices/{invoice_id}/review updates review state"""
        # Get invoices list to find one that was processed
        response = self.client.get("/api/invoices")
        invoices = response.json()
        
        if len(invoices) == 0:
            self.skipTest("No invoices in database to run review test.")
            
        target_inv = invoices[0]
        inv_id = target_inv["invoice_id"]
        
        # Run review update
        review_data = {
            "status": "auto_approved",
            "explanation": "Manually verified by auditor during API testing."
        }
        
        resp = self.client.post(f"/api/invoices/{inv_id}/review", json=review_data)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        
        # Verify changes in SQLite
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT status, explanation, requires_human_review FROM invoice_decisions WHERE invoice_id = ?", (inv_id,))
        status, explanation, review = cursor.fetchone()
        conn.close()
        
        self.assertEqual(status, "auto_approved")
        self.assertEqual(explanation, "Manually verified by auditor during API testing.")
        self.assertEqual(review, 0)

    def test_delete_endpoint(self):
        """Assert DELETE /api/invoices/{invoice_id} successfully deletes invoice from database"""
        response = self.client.get("/api/invoices")
        invoices = response.json()
        if len(invoices) == 0:
            # Seed an invoice by running upload first
            self.test_upload_endpoint()
            response = self.client.get("/api/invoices")
            invoices = response.json()
            
        target_inv = invoices[0]
        inv_id = target_inv["invoice_id"]
        
        resp = self.client.delete(f"/api/invoices/{inv_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        self.assertEqual(resp.json()["deleted_id"], inv_id)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM invoices WHERE invoice_id = ?", (inv_id,))
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

if __name__ == "__main__":
    unittest.main()
