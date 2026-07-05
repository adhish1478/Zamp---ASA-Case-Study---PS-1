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
        """Assert POST /api/upload accepts a file, processes it, and returns results"""
        self.assertTrue(os.path.exists(self.test_pdf_path), "Test PDF not found.")
        
        with open(self.test_pdf_path, "rb") as f:
            response = self.client.post(
                "/api/upload",
                files={"files": ("inv_100000.pdf", f, "application/pdf")}
            )
            
        self.assertEqual(response.status_code, 200)
        results = response.json()
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        
        res = results[0]
        self.assertTrue(res["success"])
        self.assertEqual(res["invoice_id"], "inv_100000")
        self.assertIn("decision", res)
        self.assertIn("status", res["decision"])

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

if __name__ == "__main__":
    unittest.main()
