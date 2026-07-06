# Edge Case Fixtures

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
