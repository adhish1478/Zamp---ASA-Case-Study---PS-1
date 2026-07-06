import React, { useState, useEffect } from "react";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export default function App() {
  const [invoices, setInvoices] = useState([]);
  const [selectedInvoice, setSelectedInvoice] = useState(null);
  const [currentView, setCurrentView] = useState("overview"); // "overview" | "invoices" | "exceptions" | "suppliers"
  const [filter, setFilter] = useState("ALL"); // "ALL" | "APPROVED" | "FLAGGED" | "REJECTED"
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [showPdf, setShowPdf] = useState(false);
  const [activeBatch, setActiveBatch] = useState(null);
  const [purchaseOrders, setPurchaseOrders] = useState([]);
  const [showAllPos, setShowAllPos] = useState(false);

  // Form states for editable fields
  const [editVendor, setEditVendor] = useState("");
  const [editInvNumber, setEditInvNumber] = useState("");
  const [editDate, setEditDate] = useState("");
  const [editPoRef, setEditPoRef] = useState("");
  const [editTotal, setEditTotal] = useState(0);
  const [editTax, setEditTax] = useState(0);

  // Fetch all invoices
  const fetchInvoices = async (autoSelectId = null) => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE}/api/invoices`);
      if (!res.ok) throw new Error("Failed to fetch invoices");
      const data = await res.json();
      setInvoices(data);
      
      if (data.length > 0) {
        if (autoSelectId) {
          const match = data.find(i => i.invoice_id === autoSelectId);
          if (match) handleSelectInvoice(match);
        } else if (!selectedInvoice) {
          handleSelectInvoice(data[0]);
        } else {
          // Keep current selection but refresh details
          const updated = data.find(i => i.invoice_id === selectedInvoice.invoice_id);
          if (updated) handleSelectInvoice(updated);
        }
      }
    } catch (err) {
      console.error("Error fetching data:", err);
    } finally {
      setLoading(false);
    }
  };

  const fetchPurchaseOrders = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/pos`);
      if (!res.ok) throw new Error("Failed to fetch purchase orders");
      const data = await res.json();
      setPurchaseOrders(data);
    } catch (err) {
      console.error("Error fetching purchase orders:", err);
    }
  };

  const handleToggleCompliance = async (vendorName, currentApproved) => {
    try {
      const res = await fetch(`${API_BASE}/api/suppliers/compliance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vendor_name: vendorName,
          approved_vendor: !currentApproved
        })
      });
      if (!res.ok) throw new Error("Failed to toggle compliance");
      
      await fetchPurchaseOrders();
      await fetchInvoices(selectedInvoice ? selectedInvoice.invoice_id : null);
    } catch (err) {
      console.error("Error toggling compliance:", err);
      alert("Failed to update supplier compliance.");
    }
  };

  useEffect(() => {
    fetchInvoices();
    fetchPurchaseOrders();
  }, []);

  const handleSelectInvoice = (invoice) => {
    setSelectedInvoice(invoice);
    setShowPdf(false);
    setEditVendor(invoice.vendor_name || "");
    setEditInvNumber(invoice.invoice_number || "");
    setEditDate(invoice.invoice_date || "");
    setEditPoRef(invoice.po_reference || "");
    setEditTotal(invoice.total !== null ? invoice.total : 0);
    setEditTax(invoice.tax !== null ? invoice.tax : 0);
  };

  // Upload handlers
  const handleUploadFiles = async (files) => {
    if (!files || files.length === 0) return;
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append("files", files[i]);
    }

    try {
      setUploading(true);
      setActiveBatch({ status: "queued", files: {} });
      
      const res = await fetch(`${API_BASE}/api/upload`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("Upload failed to start");
      const initData = await res.json();
      const batchId = initData.batch_id;
      
      // Start polling
      await new Promise((resolve, reject) => {
        const interval = setInterval(async () => {
          try {
            const statusRes = await fetch(`${API_BASE}/api/upload/status/${batchId}`);
            if (!statusRes.ok) throw new Error("Failed to retrieve batch status");
            const statusData = await statusRes.json();
            setActiveBatch(statusData);
            
            if (statusData.status === "completed") {
              clearInterval(interval);
              resolve(statusData);
            }
          } catch (err) {
            clearInterval(interval);
            reject(err);
          }
        }, 800);
      });
      
      await fetchInvoices();
    } catch (err) {
      console.error("Upload error:", err);
      alert("Failed to upload/process invoices.");
    } finally {
      setUploading(false);
      // Keep the batch status dialog visible for 4 seconds after completion
      setTimeout(() => {
        setActiveBatch(null);
      }, 4000);
    }
  };

  const handleFileChange = (e) => {
    handleUploadFiles(e.target.files);
  };

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleUploadFiles(e.dataTransfer.files);
    }
  };

  // Review submission handler (Auditor manual action)
  const submitReview = async (decisionStatus) => {
    if (!selectedInvoice) return;

    // Create custom explanation based on review status
    const explanation = decisionStatus === "auto_approved"
      ? `Approved by auditor manual override. Fields verified.`
      : `Rejected by auditor audit review. Compliance rules breached.`;

    try {
      // Update the structured field values in invoices table (if auditor corrected them)
      const updateData = {
        vendor_name: editVendor,
        invoice_number: editInvNumber,
        invoice_date: editDate,
        po_reference: editPoRef || null,
        total: parseFloat(editTotal) || 0,
        tax: parseFloat(editTax) || 0,
        status: decisionStatus,
        explanation: explanation
      };

      const res = await fetch(`${API_BASE}/api/invoices/${selectedInvoice.invoice_id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updateData),
      });

      if (!res.ok) throw new Error("Failed to submit review");
      
      await fetchInvoices(selectedInvoice.invoice_id);
    } catch (err) {
      console.error("Review submit error:", err);
      alert("Failed to submit review.");
    }
  };

  const handleExportCSV = () => {
    const targets = filteredInvoices;
    if (targets.length === 0) {
      alert("No data available to export.");
      return;
    }

    const headers = [
      "Invoice ID",
      "Source File",
      "Vendor Name",
      "Invoice Number",
      "Invoice Date",
      "PO Reference",
      "Total Amount",
      "Tax Amount",
      "Audit Status",
      "Explanation",
      "Requires Review"
    ];

    const escape = (val) => {
      if (val === null || val === undefined) return "";
      const str = String(val);
      if (str.includes(",") || str.includes('"') || str.includes("\n")) {
        return `"${str.replace(/"/g, '""')}"`;
      }
      return str;
    };

    const rows = targets.map(inv => [
      escape(inv.invoice_id),
      escape(inv.source_file),
      escape(inv.vendor_name),
      escape(inv.invoice_number),
      escape(inv.invoice_date),
      escape(inv.po_reference),
      inv.total !== null ? inv.total.toFixed(2) : "0.00",
      inv.tax !== null ? inv.tax.toFixed(2) : "0.00",
      escape(inv.status),
      escape(inv.explanation),
      inv.decision_review ? "Yes" : "No"
    ]);

    const csvContent = [headers.join(","), ...rows.map(r => r.join(","))].join("\n");

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `audit_invoices_${currentView}_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Helper to get formatted error reason for the table list view
  const getDiscrepancyReason = (inv) => {
    if (inv.status === "auto_approved") return null;
    if (inv.rule_trace && inv.rule_trace.length > 0) {
      const issues = [];
      if (inv.rule_trace.includes("duplicate_invoice_detected")) issues.push("Duplicate Submission");
      if (inv.rule_trace.includes("no_matching_po_number")) issues.push("No Matching PO");
      if (inv.rule_trace.includes("unapproved_vendor")) issues.push("Unapproved Vendor");
      if (inv.rule_trace.includes("vendor_mismatch")) issues.push("Supplier Name Mismatch");
      if (inv.rule_trace.some(r => r.includes("amount_exceeds_tolerance"))) {
        const tolRule = inv.rule_trace.find(r => r.includes("amount_exceeds_tolerance"));
        const pct = tolRule.split(":")[1] || "";
        issues.push(`Over Tolerance (+${pct})`);
      }
      if (inv.rule_trace.includes("low_confidence_review_override")) issues.push("Low OCR Confidence");
      
      if (issues.length > 0) {
        return issues.join(", ");
      }
    }
    return "Audit Exception";
  };

  // Dynamically compute suppliers and metrics
  const getSuppliers = () => {
    const suppliersMap = {};
    invoices.forEach(inv => {
      const name = inv.vendor_name || "Unknown Vendor";
      if (!suppliersMap[name]) {
        suppliersMap[name] = {
          name,
          invoiceCount: 0,
          totalSpend: 0,
          isUnapproved: false
        };
      }
      suppliersMap[name].invoiceCount += 1;
      suppliersMap[name].totalSpend += (inv.total || 0);
      if (inv.rule_trace && inv.rule_trace.includes("unapproved_vendor")) {
        suppliersMap[name].isUnapproved = true;
      }
    });
    
    // Support basic search/filter on suppliers
    const list = Object.values(suppliersMap);
    if (searchQuery.trim() !== "") {
      const q = searchQuery.toLowerCase();
      return list.filter(s => s.name.toLowerCase().includes(q));
    }
    return list;
  };

  // Compute live KPI summaries from state
  const totalProcessed = invoices.length;
  const approvedCount = invoices.filter(i => i.status === "auto_approved").length;
  const flaggedCount = invoices.filter(i => i.status === "flagged_for_review").length;
  const rejectedCount = invoices.filter(i => i.status === "rejected").length;
  const exceptionsCount = flaggedCount + rejectedCount;

  // Calculate exception financial metrics
  const exceptionTotalAmount = invoices
    .filter(i => i.status !== "auto_approved")
    .reduce((sum, i) => sum + (i.total || 0), 0);

  const duplicateCount = invoices.filter(i => i.rule_trace?.includes("duplicate_invoice_detected")).length;
  const noPoCount = invoices.filter(i => i.rule_trace?.includes("no_matching_po_number")).length;
  const unapprovedCount = invoices.filter(i => i.rule_trace?.includes("unapproved_vendor")).length;
  const mismatchCount = invoices.filter(i => i.rule_trace?.includes("vendor_mismatch")).length;
  const toleranceCount = invoices.filter(i => i.rule_trace?.some(r => r.includes("amount_exceeds_tolerance"))).length;

  // Filter & search implementation
  const filteredInvoices = invoices.filter(inv => {
    // 1. Sidebar View Filters
    if (currentView === "exceptions" && inv.status === "auto_approved") return false;

    // 2. Status Segment Tabs Filter (only relevant inside overview & invoices views)
    if (currentView !== "exceptions") {
      if (filter === "APPROVED" && inv.status !== "auto_approved") return false;
      if (filter === "FLAGGED" && inv.status !== "flagged_for_review") return false;
      if (filter === "REJECTED" && inv.status !== "rejected") return false;
    }

    // 3. Search Query Filter
    if (searchQuery.trim() !== "") {
      const q = searchQuery.toLowerCase();
      const vendor = (inv.vendor_name || "").toLowerCase();
      const number = (inv.invoice_number || "").toLowerCase();
      const po = (inv.po_reference || "").toLowerCase();
      const id = (inv.invoice_id || "").toLowerCase();
      return vendor.includes(q) || number.includes(q) || po.includes(q) || id.includes(q);
    }

    return true;
  });

  // Recent invoices for the Dashboard Overview page
  const recentInvoices = invoices.slice(0, 3);

  // Switch supplier click -> Invoice log search
  const handleSupplierClick = (supplierName) => {
    setSearchQuery(supplierName);
    setFilter("ALL");
    setCurrentView("invoices");
  };

  return (
    <div className="font-body-md overflow-hidden bg-white text-on-surface w-screen h-screen flex">
      {/* Sidebar Navigation */}
      <aside className="h-screen w-64 fixed left-0 top-0 flex flex-col py-8 px-3 border-r border-outline-variant bg-surface-container-low select-none z-30">
        <div className="mb-10 px-3">
          <h1 className="text-headline-md font-headline-md font-bold text-on-surface">AuditDesk</h1>
          <p className="text-label-md text-on-surface-variant opacity-70">Enterprise AP Console</p>
        </div>
        <nav className="flex-1 space-y-1">
          <button
            onClick={() => {
              setCurrentView("overview");
              setFilter("ALL");
              setSearchQuery("");
            }}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-200 text-left ${
              currentView === "overview"
                ? "text-primary bg-secondary-container font-bold"
                : "text-on-surface-variant hover:bg-surface-variant"
            }`}
          >
            <span className="material-symbols-outlined">dashboard</span>
            <span className="font-label-md">Overview</span>
          </button>
          
          <button
            onClick={() => {
              setCurrentView("invoices");
              setFilter("ALL");
              setSearchQuery("");
            }}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-200 text-left ${
              currentView === "invoices"
                ? "text-primary bg-secondary-container font-bold"
                : "text-on-surface-variant hover:bg-surface-variant"
            }`}
          >
            <span className="material-symbols-outlined">description</span>
            <span className="font-label-md">Invoices</span>
          </button>

          <button
            onClick={() => {
              setCurrentView("exceptions");
              setSearchQuery("");
            }}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-200 text-left ${
              currentView === "exceptions"
                ? "text-primary bg-secondary-container font-bold"
                : "text-on-surface-variant hover:bg-surface-variant"
            }`}
          >
            <span className="material-symbols-outlined">report_problem</span>
            <span className="font-label-md flex-1">Exceptions</span>
            {exceptionsCount > 0 && (
              <span className="bg-amber-100 text-amber-800 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                {exceptionsCount}
              </span>
            )}
          </button>

          <button
            onClick={() => {
              setCurrentView("suppliers");
              setSearchQuery("");
            }}
            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg transition-all duration-200 text-left ${
              currentView === "suppliers"
                ? "text-primary bg-secondary-container font-bold"
                : "text-on-surface-variant hover:bg-surface-variant"
            }`}
          >
            <span className="material-symbols-outlined">factory</span>
            <span className="font-label-md">Suppliers</span>
          </button>
        </nav>
        <div className="mt-auto border-t border-outline-variant pt-4 px-3 flex items-center gap-3">
          <div className="w-8 h-8 rounded-full overflow-hidden bg-slate-200 flex items-center justify-center font-bold text-slate-600 text-xs">
            AR
          </div>
          <div>
            <p className="text-label-md font-semibold text-on-surface">Alex Rivera</p>
            <p className="text-[10px] text-on-surface-variant uppercase tracking-wider">Sr. Auditor</p>
          </div>
        </div>
      </aside>

      {/* Main Content Shell */}
      <main className="ml-64 h-screen flex flex-col bg-white flex-1 overflow-hidden">
        
        {/* Top App Bar */}
        <header className="h-16 flex items-center justify-between px-8 border-b border-outline-variant bg-white z-20">
          <div className="flex items-center gap-4">
            <span className="font-headline-sm text-headline-sm font-semibold text-on-surface">
              {currentView === "overview" && "Dashboard Overview"}
              {currentView === "invoices" && "Invoices Log Database"}
              {currentView === "exceptions" && "Exceptions Resolution Queue"}
              {currentView === "suppliers" && "Approved Suppliers Directory"}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {currentView !== "suppliers" && invoices.length > 0 && (
              <button
                onClick={handleExportCSV}
                className="h-9 px-3 bg-slate-50 hover:bg-slate-100 border border-slate-200 rounded text-xs font-bold text-slate-700 flex items-center gap-1.5 transition-colors cursor-pointer select-none"
              >
                <span className="material-symbols-outlined text-sm">download</span>
                Export CSV
              </button>
            )}
            <div className="relative w-72">
              <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant text-sm">search</span>
              <input
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full h-9 pl-10 pr-4 bg-slate-50 border border-slate-200 rounded text-body-sm focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary transition-all"
                placeholder={currentView === "suppliers" ? "Search Suppliers..." : "Search Invoices..."}
                type="text"
              />
            </div>
          </div>
        </header>

        {/* Audit Workspace */}
        <div className="flex-1 flex overflow-hidden">
          
          {/* MASTER PANEL (65% width) */}
          <section className="w-[65%] flex flex-col overflow-y-auto border-r border-outline-variant px-8 py-6 no-scrollbar bg-white">
            
            {/* WORKSPACE 1: EXECUTIVE DASHBOARD OVERVIEW */}
            {currentView === "overview" && (
              <div className="space-y-8 flex-1 flex flex-col">
                {/* KPI Metrics */}
                <div className="grid grid-cols-4 gap-4">
                  <div className="bg-[#f8fafc] border border-[#e2e8f0] p-4 rounded">
                    <p className="text-label-md text-slate-500 mb-1">Total Processed</p>
                    <p className="text-headline-sm font-bold text-slate-900">{totalProcessed}</p>
                  </div>
                  <div className="bg-[#f8fafc] border border-[#e2e8f0] p-4 rounded relative overflow-hidden">
                    <p className="text-label-md text-slate-500 mb-1">Auto-Approved</p>
                    <p className="text-headline-sm font-bold text-slate-900">{approvedCount}</p>
                    <div className="absolute bottom-0 left-0 h-1 w-full bg-emerald-500 opacity-50"></div>
                  </div>
                  <div className="bg-[#f8fafc] border border-[#e2e8f0] p-4 rounded relative overflow-hidden">
                    <p className="text-label-md text-slate-500 mb-1">Flagged Review</p>
                    <p className="text-headline-sm font-bold text-slate-900">{flaggedCount}</p>
                    <div className="absolute bottom-0 left-0 h-1 w-full bg-amber-500 opacity-50"></div>
                  </div>
                  <div className="bg-[#f8fafc] border border-[#e2e8f0] p-4 rounded relative overflow-hidden">
                    <p className="text-label-md text-slate-500 mb-1">Rejected</p>
                    <p className="text-headline-sm font-bold text-slate-900">{rejectedCount}</p>
                    <div className="absolute bottom-0 left-0 h-1 w-full bg-rose-500 opacity-50"></div>
                  </div>
                </div>

                {/* Upload Zone */}
                <div
                  onDragEnter={handleDrag}
                  onDragOver={handleDrag}
                  onDragLeave={handleDrag}
                  onDrop={handleDrop}
                  className={`p-8 border-2 border-dashed rounded-lg flex flex-col items-center justify-center text-center transition-colors cursor-pointer group select-none relative ${
                    dragActive ? "border-primary bg-primary/5" : "border-slate-200 bg-slate-50 hover:border-primary/50"
                  }`}
                >
                  {activeBatch ? (
                    <div className="w-full max-w-md py-2 space-y-3 text-left">
                      <div className="flex justify-between items-center border-b border-slate-100 pb-2 mb-2 select-none">
                        <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Batch Processing Status</p>
                        <span className="text-[10px] font-semibold bg-primary/10 text-primary px-2 py-0.5 rounded-full uppercase animate-pulse">
                          {activeBatch.status}
                        </span>
                      </div>
                      <div className="max-h-40 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
                        {Object.entries(activeBatch.files).map(([filename, fileState]) => {
                          let statusText = fileState.status;
                          let statusColor = "text-slate-400";
                          if (fileState.status === "parsing") {
                            statusText = "Running OCR";
                            statusColor = "text-blue-600 font-semibold animate-pulse";
                          } else if (fileState.status === "structuring") {
                            statusText = "LLM Structuring";
                            statusColor = "text-indigo-600 font-semibold animate-pulse";
                          } else if (fileState.status === "matching") {
                            statusText = "Running Rules Match";
                            statusColor = "text-amber-600 font-semibold animate-pulse";
                          } else if (fileState.status === "completed") {
                            statusText = "Ready";
                            statusColor = "text-emerald-600 font-bold";
                          } else if (fileState.status === "failed") {
                            statusText = "Error";
                            statusColor = "text-rose-600 font-bold";
                          }
                          return (
                            <div key={filename} className="flex justify-between items-center text-xs">
                              <span className="truncate max-w-[280px] font-medium text-slate-700">{filename}</span>
                              <span className={statusColor}>{statusText}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : (
                    <label className="flex flex-col items-center w-full h-full cursor-pointer">
                      <span className="material-symbols-outlined text-4xl text-slate-400 group-hover:text-primary mb-2">cloud_upload</span>
                      <p className="font-semibold text-slate-700 text-body-lg">Upload PDF Invoices</p>
                      <p className="text-slate-500 text-body-sm mt-1">Supports instant caching. Max 100 concurrent uploads</p>
                      <input type="file" multiple accept=".pdf" onChange={handleFileChange} className="hidden" />
                    </label>
                  )}
                </div>

                {/* Recent Activity List */}
                <div className="flex-1 flex flex-col min-h-[220px]">
                  <div className="flex justify-between items-center mb-4">
                    <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider">Recent Activity Queue</h3>
                    <button
                      onClick={() => setCurrentView("invoices")}
                      className="text-xs font-semibold text-primary hover:underline flex items-center gap-1"
                    >
                      View All Invoices
                      <span className="material-symbols-outlined text-sm">arrow_forward</span>
                    </button>
                  </div>
                  
                  <div className="border border-slate-200 rounded-lg overflow-hidden flex-1">
                    {recentInvoices.length === 0 ? (
                      <div className="p-8 text-center text-slate-400">
                        <span className="material-symbols-outlined text-[32px] mb-1">receipt_long</span>
                        <p className="text-body-sm">No activity recorded. Upload files to get started.</p>
                      </div>
                    ) : (
                      <table className="w-full text-left bg-white text-body-sm">
                        <thead className="bg-slate-50 text-slate-500 font-label-md">
                          <tr>
                            <th className="px-4 py-2.5 border-b border-slate-200">Vendor</th>
                            <th className="px-4 py-2.5 border-b border-slate-200">Invoice ID</th>
                            <th className="px-4 py-2.5 border-b border-slate-200 text-right">Amount</th>
                            <th className="px-4 py-2.5 border-b border-slate-200 text-center">Status</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                          {recentInvoices.map((inv) => (
                            <tr key={inv.invoice_id} className="hover:bg-slate-50/50">
                              <td className="px-4 py-3 font-semibold text-slate-800">{inv.vendor_name || "Unknown"}</td>
                              <td className="px-4 py-3 font-code-sm text-slate-400">{inv.invoice_number || "—"}</td>
                              <td className="px-4 py-3 text-right font-semibold font-numeric-table">${(inv.total || 0).toFixed(2)}</td>
                              <td className="px-4 py-3 text-center">
                                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase ${
                                  inv.status === "auto_approved" ? "bg-emerald-100 text-emerald-800" :
                                  inv.status === "rejected" ? "bg-rose-100 text-rose-800" : "bg-amber-100 text-amber-800"
                                }`}>
                                  {inv.status === "auto_approved" ? "Approved" : inv.status === "rejected" ? "Rejected" : "Flagged"}
                                </span>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* WORKSPACE 2: ALL INVOICES LOG VIEW */}
            {currentView === "invoices" && (
              <div className="flex-1 flex flex-col">
                {/* Segment Filters Bar */}
                <div className="flex items-center gap-8 mb-6 border-b border-slate-100 select-none">
                  <button
                    onClick={() => setFilter("ALL")}
                    className={`pb-3 text-label-md font-bold transition-all border-b-2 ${
                      filter === "ALL" ? "text-primary border-primary" : "text-slate-400 border-transparent hover:text-slate-600"
                    }`}
                  >
                    ALL ({totalProcessed})
                  </button>
                  <button
                    onClick={() => setFilter("APPROVED")}
                    className={`pb-3 text-label-md font-bold transition-all border-b-2 ${
                      filter === "APPROVED" ? "text-primary border-primary" : "text-slate-400 border-transparent hover:text-slate-600"
                    }`}
                  >
                    APPROVED ({approvedCount})
                  </button>
                  <button
                    onClick={() => setFilter("FLAGGED")}
                    className={`pb-3 text-label-md font-bold transition-all border-b-2 flex items-center gap-2 ${
                      filter === "FLAGGED" ? "text-primary border-primary" : "text-slate-400 border-transparent hover:text-slate-600"
                    }`}
                  >
                    FLAGGED <span className="bg-amber-100 text-amber-700 text-[10px] px-1.5 rounded-full">{flaggedCount}</span>
                  </button>
                  <button
                    onClick={() => setFilter("REJECTED")}
                    className={`pb-3 text-label-md font-bold transition-all border-b-2 ${
                      filter === "REJECTED" ? "text-primary border-primary" : "text-slate-400 border-transparent hover:text-slate-600"
                    }`}
                  >
                    REJECTED ({rejectedCount})
                  </button>
                </div>

                {/* Table Data Grid */}
                <div className="flex-1 overflow-y-auto custom-scrollbar border border-slate-200 rounded-lg">
                  {loading && invoices.length === 0 ? (
                    <div className="flex items-center justify-center p-8">
                      <span className="material-symbols-outlined text-primary text-[32px] animate-spin">sync</span>
                    </div>
                  ) : filteredInvoices.length === 0 ? (
                    <div className="p-8 text-center text-slate-400">
                      <span className="material-symbols-outlined text-[40px] mb-2">inbox</span>
                      <p>No invoices matched search/filter tags.</p>
                    </div>
                  ) : (
                    <table className="w-full text-left">
                      <thead className="sticky top-0 z-10 bg-slate-50">
                        <tr className="text-label-md text-slate-500 uppercase tracking-tight">
                          <th className="px-3 py-3 border-b border-slate-200">Vendor</th>
                          <th className="px-3 py-3 border-b border-slate-200">Invoice ID</th>
                          <th className="px-3 py-3 border-b border-slate-200">Date</th>
                          <th className="px-3 py-3 border-b border-slate-200 text-right">Amount</th>
                          <th className="px-3 py-3 border-b border-slate-200">Status</th>
                        </tr>
                      </thead>
                      <tbody className="text-body-sm text-slate-700 divide-y divide-slate-100 bg-white">
                        {filteredInvoices.map((inv) => {
                          const isSelected = selectedInvoice && selectedInvoice.invoice_id === inv.invoice_id;
                          const discrepancy = getDiscrepancyReason(inv);
                          
                          let badge = null;
                          if (inv.status === "auto_approved") {
                            badge = <span className="bg-emerald-100 text-emerald-800 text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase">Approved</span>;
                          } else if (inv.status === "flagged_for_review") {
                            badge = <span className="bg-amber-100 text-amber-800 text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase">Flagged</span>;
                          } else if (inv.status === "rejected") {
                            badge = <span className="bg-rose-100 text-rose-800 text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase">Rejected</span>;
                          }

                          return (
                            <tr
                              key={inv.invoice_id}
                              onClick={() => handleSelectInvoice(inv)}
                              className={`cursor-pointer group transition-all hover:bg-slate-50/80 ${
                                isSelected ? "invoice-row-active" : ""
                              }`}
                            >
                              <td className="px-3 py-3">
                                <div className="font-semibold text-slate-900">{inv.vendor_name || "Unknown"}</div>
                                {discrepancy && (
                                  <div className={`text-[11px] font-medium italic ${
                                    inv.status === "rejected" ? "text-rose-600" : "text-amber-600"
                                  }`}>
                                    {discrepancy}
                                  </div>
                                )}
                              </td>
                              <td className="px-3 py-3 font-code-sm text-slate-500">{inv.invoice_number || "—"}</td>
                              <td className="px-3 py-3 font-numeric-table">{inv.invoice_date || "—"}</td>
                              <td className="px-3 py-3 text-right font-semibold font-numeric-table">
                                ${inv.total !== null ? inv.total.toFixed(2) : "0.00"}
                              </td>
                              <td className="px-3 py-3">{badge}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            )}

            {/* WORKSPACE 3: EXCEPTIONS RESOLUTION WORKSPACE */}
            {currentView === "exceptions" && (
              <div className="flex-1 flex flex-col space-y-6">
                
                {/* Action-focused Exception Summary Panel */}
                <div className="bg-amber-50/70 border border-amber-200 rounded-lg p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="space-y-1">
                    <p className="text-xs font-bold text-amber-800 uppercase tracking-wider">Blocked Audit Queue</p>
                    <p className="text-2xl font-bold text-slate-900 font-numeric-table">
                      ${exceptionTotalAmount.toFixed(2)}
                    </p>
                    <p className="text-body-sm text-slate-600">
                      Total financial exposure held across {exceptionsCount} unresolved audit discrepancies.
                    </p>
                  </div>
                  
                  {/* Summary Breakdown Pills */}
                  <div className="flex flex-wrap gap-2 text-[11px] font-semibold text-slate-600">
                    {noPoCount > 0 && <span className="bg-white border border-slate-200 px-2 py-1 rounded">{noPoCount} x No Matching PO</span>}
                    {duplicateCount > 0 && <span className="bg-white border border-slate-200 px-2 py-1 rounded">{duplicateCount} x Duplicate</span>}
                    {toleranceCount > 0 && <span className="bg-white border border-slate-200 px-2 py-1 rounded">{toleranceCount} x Over Tolerance</span>}
                    {unapprovedCount > 0 && <span className="bg-white border border-slate-200 px-2 py-1 rounded">{unapprovedCount} x Unapproved Vendor</span>}
                    {mismatchCount > 0 && <span className="bg-white border border-slate-200 px-2 py-1 rounded">{mismatchCount} x Name Mismatch</span>}
                  </div>
                </div>

                {/* Exception List Table */}
                <div className="flex-1 overflow-y-auto custom-scrollbar border border-slate-200 rounded-lg bg-white">
                  {filteredInvoices.length === 0 ? (
                    <div className="p-8 text-center text-slate-400">
                      <span className="material-symbols-outlined text-[40px] mb-2 text-emerald-500">task_alt</span>
                      <p className="font-semibold text-slate-800">Clean Sheet! No Exceptions</p>
                      <p className="text-body-sm mt-1">All processed invoices have successfully cleared audit rules.</p>
                    </div>
                  ) : (
                    <table className="w-full text-left">
                      <thead className="sticky top-0 z-10 bg-slate-50">
                        <tr className="text-label-md text-slate-500 uppercase tracking-tight">
                          <th className="px-3 py-3 border-b border-slate-200">Vendor</th>
                          <th className="px-3 py-3 border-b border-slate-200">Invoice ID</th>
                          <th className="px-3 py-3 border-b border-slate-200">Date</th>
                          <th className="px-3 py-3 border-b border-slate-200 text-right">Amount</th>
                          <th className="px-3 py-3 border-b border-slate-200">Status</th>
                        </tr>
                      </thead>
                      <tbody className="text-body-sm text-slate-700 divide-y divide-slate-100 bg-white">
                        {filteredInvoices.map((inv) => {
                          const isSelected = selectedInvoice && selectedInvoice.invoice_id === inv.invoice_id;
                          const discrepancy = getDiscrepancyReason(inv);
                          
                          let badge = null;
                          if (inv.status === "flagged_for_review") {
                            badge = <span className="bg-amber-100 text-amber-800 text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase">Flagged</span>;
                          } else if (inv.status === "rejected") {
                            badge = <span className="bg-rose-100 text-rose-800 text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase">Rejected</span>;
                          }

                          return (
                            <tr
                              key={inv.invoice_id}
                              onClick={() => handleSelectInvoice(inv)}
                              className={`cursor-pointer group transition-all hover:bg-slate-50/80 ${
                                isSelected ? "invoice-row-active" : ""
                              }`}
                            >
                              <td className="px-3 py-3">
                                <div className="font-semibold text-slate-900">{inv.vendor_name || "Unknown"}</div>
                                {discrepancy && (
                                  <div className={`text-[11px] font-medium italic ${
                                    inv.status === "rejected" ? "text-rose-600" : "text-amber-600"
                                  }`}>
                                    {discrepancy}
                                  </div>
                                )}
                              </td>
                              <td className="px-3 py-3 font-code-sm text-slate-500">{inv.invoice_number || "—"}</td>
                              <td className="px-3 py-3 font-numeric-table">{inv.invoice_date || "—"}</td>
                              <td className="px-3 py-3 text-right font-semibold font-numeric-table">
                                ${inv.total !== null ? inv.total.toFixed(2) : "0.00"}
                              </td>
                              <td className="px-3 py-3">{badge}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            )}

            {/* WORKSPACE 4: SUPPLIERS DIRECTORY VIEW */}
            {currentView === "suppliers" && (
              <div className="flex-1 flex flex-col">
                <div className="flex-1 overflow-y-auto custom-scrollbar border border-slate-200 rounded-lg bg-white">
                  <table className="w-full text-left">
                    <thead className="sticky top-0 z-10 bg-slate-50">
                      <tr className="text-label-md text-slate-500 uppercase tracking-tight">
                        <th className="px-3 py-3 border-b border-slate-200">Supplier Name</th>
                        <th className="px-3 py-3 border-b border-slate-200 text-center">Invoices Processed</th>
                        <th className="px-3 py-3 border-b border-slate-200 text-right">Total Spend</th>
                        <th className="px-3 py-3 border-b border-slate-200 text-center">Vendor Type</th>
                      </tr>
                    </thead>
                    <tbody className="text-body-sm text-slate-700 divide-y divide-slate-100 bg-white">
                      {getSuppliers().length === 0 ? (
                        <tr>
                          <td colSpan="4" className="p-8 text-center text-slate-400">No suppliers matched search query.</td>
                        </tr>
                      ) : (
                        getSuppliers().map((sup, idx) => (
                          <tr
                            key={idx}
                            onClick={() => handleSupplierClick(sup.name)}
                            className="hover:bg-slate-50 cursor-pointer group transition-all"
                          >
                            <td className="px-3 py-4">
                              <div className="font-semibold text-slate-900 group-hover:text-primary transition-colors flex items-center gap-2">
                                {sup.name}
                                <span className="material-symbols-outlined text-slate-300 group-hover:text-primary text-[14px] opacity-0 group-hover:opacity-100 transition-opacity">
                                  arrow_forward
                                </span>
                              </div>
                            </td>
                            <td className="px-3 py-4 text-center font-numeric-table">{sup.invoiceCount}</td>
                            <td className="px-3 py-4 text-right font-semibold font-numeric-table">
                              ${sup.totalSpend.toFixed(2)}
                            </td>
                            <td className="px-3 py-4 text-center" onClick={(e) => e.stopPropagation()}>
                              <div className="flex items-center justify-center gap-3">
                                {sup.isUnapproved ? (
                                  <span className="bg-amber-100 text-amber-800 text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase">
                                    Unapproved
                                  </span>
                                ) : (
                                  <span className="bg-emerald-100 text-emerald-800 text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase">
                                    Approved
                                  </span>
                                )}
                                <button
                                  onClick={async (e) => {
                                    e.stopPropagation();
                                    await handleToggleCompliance(sup.name, !sup.isUnapproved);
                                  }}
                                  className="px-2 py-0.5 text-[10px] font-bold border border-slate-200 rounded hover:bg-slate-50 transition-colors cursor-pointer"
                                >
                                  {sup.isUnapproved ? "Authorize" : "Deauthorize"}
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

          </section>

          {/* AUDIT ACTION DRAWER (35% width) */}
          {selectedInvoice && currentView !== "suppliers" ? (
            <section className="w-[35%] flex flex-col h-full bg-slate-50 relative border-l border-outline-variant">
              <div className="flex-1 overflow-y-auto custom-scrollbar flex flex-col">
              
              {/* High-visibility Status Header Box */}
              {(selectedInvoice.status === "flagged_for_review" || selectedInvoice.status === "rejected") && (
                <div className={`p-4 border-b ${
                  selectedInvoice.status === "rejected"
                    ? "bg-rose-50 border-rose-200 text-rose-800"
                    : "bg-amber-50 border-amber-200 text-amber-800"
                }`}>
                  <div className="flex items-start gap-3">
                    <span className="material-symbols-outlined mt-0.5">
                      {selectedInvoice.status === "rejected" ? "cancel" : "warning"}
                    </span>
                    <div>
                      <p className="font-bold text-label-md uppercase">
                        Audit Violation: {
                          selectedInvoice.rule_trace?.includes("duplicate_invoice_detected") ? "DUPLICATE_INVOICE_ERROR" :
                          selectedInvoice.rule_trace?.includes("no_matching_po_number") ? "PO_MISMATCH_ERROR" :
                          selectedInvoice.rule_trace?.includes("unapproved_vendor") ? "UNAPPROVED_VENDOR_ERROR" :
                          selectedInvoice.rule_trace?.includes("vendor_mismatch") ? "SUPPLIER_MISMATCH_ERROR" :
                          "MATCH_COMPLIANCE_ERROR"
                        }
                      </p>
                      <p className="text-body-sm italic mt-1 leading-relaxed opacity-90">
                        "{selectedInvoice.explanation || "This invoice requires manual verification."}"
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Header Details Panel */}
              <div className="p-6 bg-white border-b border-slate-200 relative">
                <button
                  onClick={() => setSelectedInvoice(null)}
                  className="absolute right-4 top-4 p-1 text-slate-400 hover:text-slate-600 rounded-full hover:bg-slate-100"
                >
                  <span className="material-symbols-outlined text-lg">close</span>
                </button>
                <h2 className="text-headline-md font-bold text-slate-900 truncate pr-6">
                  {selectedInvoice.vendor_name || "Unknown Vendor"}
                </h2>
                <div className="flex justify-between items-baseline mt-1">
                  <span className="text-label-md font-code-sm text-slate-500 uppercase">
                    {selectedInvoice.invoice_id.toUpperCase()}
                  </span>
                  <span className="text-2xl font-bold text-slate-900 font-numeric-table">
                    ${selectedInvoice.total !== null ? selectedInvoice.total.toFixed(2) : "0.00"}
                  </span>
                </div>
              </div>

              {/* PDF Preview Drawer Section */}
              <div className="px-6 py-4 bg-white border-b border-slate-100 flex flex-col">
                <button
                  onClick={() => setShowPdf(!showPdf)}
                  className="flex items-center justify-between text-xs font-bold text-slate-500 hover:text-slate-800 transition-colors uppercase tracking-wider text-left select-none"
                >
                  <span className="flex items-center gap-1.5">
                    <span className="material-symbols-outlined text-[16px]">picture_as_pdf</span>
                    Invoice Document Preview
                  </span>
                  <span className="material-symbols-outlined text-sm">
                    {showPdf ? "expand_less" : "expand_more"}
                  </span>
                </button>
                
                {showPdf && selectedInvoice.source_file && (
                  <div className="mt-3 w-full h-80 rounded border border-slate-200 overflow-hidden bg-slate-100 relative">
                    <iframe
                      src={`${API_BASE}/api/uploads/${selectedInvoice.source_file}`}
                      className="w-full h-full border-none"
                      title="Invoice PDF Viewer"
                    />
                  </div>
                )}
              </div>

              {/* Fields Adjustment Form */}
              <div className="p-6 space-y-4 bg-white border-b border-slate-100">
                <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider">Correct Fields</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1">
                    <label className="text-label-md text-slate-500">Vendor Name</label>
                    <input
                      className="w-full h-8 px-2 border border-slate-200 rounded bg-white text-body-sm"
                      type="text"
                      value={editVendor}
                      onChange={(e) => setEditVendor(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-label-md text-slate-500">Invoice ID</label>
                    <input
                      className="w-full h-8 px-2 border border-slate-200 rounded bg-white font-code-sm text-body-sm"
                      type="text"
                      value={editInvNumber}
                      onChange={(e) => setEditInvNumber(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-label-md text-slate-500">Invoice Date</label>
                    <input
                      className="w-full h-8 px-2 border border-slate-200 rounded bg-white text-body-sm"
                      type="text"
                      value={editDate}
                      onChange={(e) => setEditDate(e.target.value)}
                    />
                  </div>
                  
                  {/* PO matching warning state visualization */}
                  <div className="space-y-1">
                    <label className="text-label-md text-slate-500">PO Reference</label>
                    <div className="relative">
                      <input
                        className={`w-full h-8 px-2 border rounded text-body-sm font-semibold ${
                          !editPoRef || selectedInvoice.rule_trace?.includes("no_matching_po_number")
                            ? "border-rose-300 bg-rose-50 text-rose-700 placeholder:text-rose-400"
                            : "border-slate-200 bg-white"
                        }`}
                        placeholder="No Match Found"
                        type="text"
                        value={editPoRef}
                        onChange={(e) => setEditPoRef(e.target.value)}
                      />
                      {(!editPoRef || selectedInvoice.rule_trace?.includes("no_matching_po_number")) && (
                        <span className="material-symbols-outlined absolute right-2 top-1/2 -translate-y-1/2 text-rose-500 text-sm">
                          error
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="space-y-1">
                    <label className="text-label-md text-slate-500">Tax Amount</label>
                    <input
                      className="w-full h-8 px-2 border border-slate-200 rounded bg-white text-body-sm font-numeric-table"
                      type="number"
                      step="0.01"
                      value={editTax}
                      onChange={(e) => setEditTax(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-label-md text-slate-500">Total Amount</label>
                    <input
                      className="w-full h-8 px-2 border border-slate-200 rounded bg-white text-body-sm font-bold font-numeric-table"
                      type="number"
                      step="0.01"
                      value={editTotal}
                      onChange={(e) => setEditTotal(e.target.value)}
                    />
                  </div>
                </div>
                
                {/* PO Linking & Status Details */}
                {(!editPoRef || selectedInvoice.rule_trace?.includes("no_matching_po_number")) ? (
                  <div className="mt-3 p-4 bg-slate-50 border border-slate-200 rounded space-y-3">
                    <div className="flex justify-between items-center select-none">
                      <label className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Link Purchase Order</label>
                      <span
                        onClick={() => setShowAllPos(!showAllPos)}
                        className="text-[10px] text-primary hover:underline cursor-pointer font-semibold"
                      >
                        {showAllPos ? "Show matching only" : "Search all POs"}
                      </span>
                    </div>
                    <div className="flex gap-2">
                      <select
                        value={editPoRef}
                        onChange={(e) => setEditPoRef(e.target.value)}
                        className="flex-1 h-8 px-2 border border-slate-200 rounded bg-white text-xs font-semibold"
                      >
                        <option value="">-- Select PO --</option>
                        {purchaseOrders
                          .filter(po => {
                            if (showAllPos) return true;
                            const poVendorNorm = po.vendor_name ? po.vendor_name.toLowerCase().replace(/\W+/g, "") : "";
                            const invVendorNorm = editVendor ? editVendor.toLowerCase().replace(/\W+/g, "") : "";
                            return poVendorNorm.includes(invVendorNorm) || invVendorNorm.includes(poVendorNorm);
                          })
                          .map(po => (
                            <option key={po.po_id} value={po.po_id}>
                              {po.po_id} (${po.po_amount.toFixed(2)}) - {po.vendor_name}
                            </option>
                          ))
                        }
                      </select>
                      <button
                        onClick={async () => {
                          if (editPoRef) {
                            await submitReview(selectedInvoice.status);
                          }
                        }}
                        disabled={!editPoRef}
                        className="px-3 h-8 bg-primary text-white text-xs font-bold rounded hover:bg-primary/90 disabled:opacity-50"
                      >
                        Link
                      </button>
                    </div>
                  </div>
                ) : (
                  (() => {
                    const matchedPo = purchaseOrders.find(p => p.po_id === editPoRef);
                    if (!matchedPo) return null;
                    const totalVal = parseFloat(editTotal) || 0;
                    const poAmt = matchedPo.po_amount;
                    const pctDiff = poAmt > 0 ? ((totalVal - poAmt) / poAmt) * 100 : 0;
                    const isOverTol = pctDiff > matchedPo.tolerance_pct;
                    
                    return (
                      <div className="mt-3 p-4 bg-slate-50 border border-slate-200 rounded space-y-3">
                        <p className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Matched Purchase Order: {matchedPo.po_id}</p>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div>
                            <span className="text-slate-400 block">Pre-Approved PO</span>
                            <span className="font-semibold text-slate-700">${poAmt.toFixed(2)}</span>
                          </div>
                          <div>
                            <span className="text-slate-400 block">Invoice Total</span>
                            <span className={`font-bold ${isOverTol ? 'text-rose-600' : 'text-slate-700'}`}>
                              ${totalVal.toFixed(2)} ({pctDiff > 0 ? `+${pctDiff.toFixed(1)}%` : `${pctDiff.toFixed(1)}%`})
                            </span>
                          </div>
                          <div>
                            <span className="text-slate-400 block">Allowed Tolerance</span>
                            <span className="font-semibold text-slate-700">{matchedPo.tolerance_pct}%</span>
                          </div>
                          <div>
                            <span className="text-slate-400 block">Vendor Status</span>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              <span className={`w-2 h-2 rounded-full ${matchedPo.approved_vendor ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
                              <span className="font-semibold text-slate-700">{matchedPo.approved_vendor ? "Approved" : "Unapproved"}</span>
                            </div>
                          </div>
                        </div>
                        
                        <div className="border-t border-slate-200/60 pt-2.5 flex justify-between items-center">
                          <span className="text-[10px] text-slate-400 italic">Toggle compliance:</span>
                          <button
                            onClick={() => handleToggleCompliance(matchedPo.vendor_name, matchedPo.approved_vendor)}
                            className={`px-2 py-1 text-[10px] font-bold rounded cursor-pointer ${
                              matchedPo.approved_vendor
                                ? "bg-amber-50 border border-amber-200 text-amber-700 hover:bg-amber-100"
                                : "bg-emerald-50 border border-emerald-200 text-emerald-700 hover:bg-emerald-100"
                            }`}
                          >
                            {matchedPo.approved_vendor ? "Deauthorize Vendor" : "Authorize Vendor"}
                          </button>
                        </div>
                      </div>
                    );
                  })()
                )}

                {/* Specific Unapproved Vendor Alert inside Drawer */}
                {selectedInvoice.rule_trace?.includes("unapproved_vendor") && (
                  <div className="mt-2 p-3 bg-amber-50 border border-amber-200 text-amber-800 rounded text-xs flex gap-2 items-center">
                    <span className="material-symbols-outlined text-[16px]">gavel</span>
                    <span>
                      <strong>Vendor Compliance Rule:</strong> This supplier is unapproved in the procurement system. Override is required.
                    </span>
                  </div>
                )}
              </div>

              {/* Line Items Grid */}
              <div className="px-6 py-6 pb-6">
                <p className="text-label-md font-bold text-slate-500 uppercase tracking-wider mb-3">
                  Line Items ({selectedInvoice.line_items?.length || 0})
                </p>
                <div className="border border-slate-200 rounded overflow-hidden">
                  <table className="w-full text-left bg-white text-[11px]">
                    <thead className="bg-slate-50 text-label-md text-slate-500">
                      <tr>
                        <th className="px-3 py-2 border-b border-slate-200">Description</th>
                        <th className="px-3 py-2 border-b border-slate-200 text-center">Qty</th>
                        <th className="px-3 py-2 border-b border-slate-200 text-right">Price</th>
                        <th className="px-3 py-2 border-b border-slate-200 text-right">Total</th>
                      </tr>
                    </thead>
                    <tbody className="text-body-sm text-slate-700 divide-y divide-slate-100">
                      {selectedInvoice.line_items && selectedInvoice.line_items.length > 0 ? (
                        selectedInvoice.line_items.map((item, idx) => (
                          <tr key={idx}>
                            <td className="px-3 py-2 border-b border-slate-100 truncate max-w-[120px]" title={item.description}>
                              {item.description}
                            </td>
                            <td className="px-3 py-2 border-b border-slate-100 text-center font-numeric-table">{item.qty}</td>
                            <td className="px-3 py-2 border-b border-slate-100 text-right font-numeric-table">
                              ${(item.unit_price || 0).toFixed(2)}
                            </td>
                            <td className="px-3 py-2 border-b border-slate-100 text-right font-semibold font-numeric-table">
                              ${(item.amount || 0).toFixed(2)}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan="4" className="p-3 text-center text-slate-400">No line items extracted.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            {/* Fixed Footer Actions (Only for Flagged invoices requiring review) */}
            {selectedInvoice.status === "flagged_for_review" && (
              <div className="p-6 bg-white border-t border-slate-200 flex gap-3 shadow-[0_-4px_10px_rgba(0,0,0,0.02)] z-10">
                <button
                  onClick={() => submitReview("rejected")}
                  className="flex-1 h-10 bg-rose-50 border border-rose-300 text-rose-700 font-bold text-label-md rounded hover:bg-rose-100 transition-colors cursor-pointer"
                >
                  Reject Permanent
                </button>
                <button
                  onClick={() => submitReview("auto_approved")}
                  className="flex-1 h-10 bg-primary text-white font-bold text-label-md rounded hover:bg-primary/90 transition-colors shadow-sm cursor-pointer"
                >
                  Approve Override
                </button>
              </div>
            )}
          </section>
          ) : null}

        </div>
      </main>
    </div>
  );
}
