"""Download ByDesign attachments through their parent business object.

The `*AttachmentFolderCollection` entity sets reject top-level queries on this tenant ("not
supported by the processor", 400), so attachments are reached the way SAP documents for
ByDesign: page the parent collection with `$expand=<AttachmentFolder nav>`, then fetch each
file from `<attachment __metadata.uri>/Binary/$value`.

Quality inspections are not covered: the tenant has no inspection OData service (only the
plm_quality*_analytics reports, which carry no attachments) - see src/quality_inspections/.

    python -m attahcments_download.download_attachments --service khpurchaseorder [--limit 100]

    python -m attahcments_download.download_attachments --service all --workers 5

Re-running resumes: attachments logged "ok" in downloads/<service>/_attachments_log.csv are skipped.
"""

import argparse
import csv
import hashlib
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.config import load_config
from src.sap_client import SAPODataClient

# service -> [(parent collection, attachment navigation property), ...] - confirmed from each
# service's live $metadata; header-level and item-level folders are separate navigations
SERVICES = {
    "khpurchaseorder": [("PurchaseOrderCollection", "AttachmentFolder")],
    "khsalesorder": [("SalesOrderCollection", "AttachmentFolder"), ("ItemCollection", "ItemAttachmentFolder")],
    "khcustomerinvoice": [("CustomerInvoiceCollection", "AttachmentFolder"), ("ItemCollection", "ItemAttachmentFolder")],
    "khcustomerinvoicerequest": [("CustomerInvoiceRequestCollection", "AttachmentFolder"), ("ItemCollection", "ItemAttachmentFolder")],
    "khcustomerquote": [("CustomerQuoteCollection", "AttachmentFolder"), ("ItemCollection", "ItemAttachmentFolder")],
    "khsupplierinvoice": [("SupplierInvoiceCollection", "Attachment")],
    "khinbounddelivery": [("InboundDeliveryCollection", "Attachment")],
    "khgoodsandserviceacknowledgement": [("ItemCollection", "ItemAttachmentFolder")],
    "khemployee": [("EmployeeCollection", "AttachmentFolder")],
}

OUT_DIR = os.path.join(os.path.dirname(__file__), "downloads")


def _safe(name):
    return re.sub(r"[^\w.\- ]", "_", name or "unnamed")


ATTACHMENT_FIELDS = ["ObjectID", "Name", "MimeType", "CategoryCodeText", "LinkWebURI"]
LOG_FIELDS = ["parent_collection", "parent_id", "attachment_object_id", "name", "mime_type",
              "category", "link_url", "file", "bytes", "sha256", "status"]


def _download(client, att, parent_id, out_dir):
    """Fetch one attachment's bytes from <attachment uri>/Binary/$value and save them."""
    response = client.session.get(
        att["__metadata"]["uri"] + "/Binary/$value",
        headers={"Accept": "application/octet-stream"},
        verify=client.config.verify_ssl, timeout=120,
    )
    if not response.ok:
        return {"status": f"HTTP {response.status_code}: {response.text[:150]}"}
    # attachment ObjectID prefix keeps two same-named files on one document from overwriting
    path = os.path.join(out_dir, f"{parent_id}_{att['ObjectID'][:8]}_{_safe(att.get('Name'))}")
    with open(path, "wb") as f:
        f.write(response.content)
    return {"file": path, "bytes": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(), "status": "ok"}


def _process(client, collection, parent_id, att, out_dir):
    row = {"parent_collection": collection, "parent_id": parent_id,
           "attachment_object_id": att.get("ObjectID"), "name": att.get("Name"),
           "mime_type": att.get("MimeType"), "category": att.get("CategoryCodeText"),
           "link_url": att.get("LinkWebURI")}
    if att.get("LinkWebURI") and not att.get("MimeType"):
        row["status"] = "web link, no file"
    else:
        try:
            row.update(_download(client, att, parent_id, out_dir))
        except Exception as exc:  # one bad file mustn't kill an overnight run; logged, retried on resume
            row["status"] = f"error: {exc}"
    return row


def export_collection(client, service, collection, nav, out_dir, log, log_file, done, limit, pool):
    url = f"{client.config.sap_base_url}/sap/byd/odata/cust/v1/{service}/{collection}"
    # item rows have no ID of their own worth naming files after; ParentObjectID ties back
    id_field = "ID" if collection != "ItemCollection" else "ParentObjectID"
    skip, page, found, ok = 0, 100, 0, 0
    pending = set()

    def record(future):
        nonlocal ok
        row = future.result()
        ok += row["status"] == "ok"
        log.writerow(row)
        print(collection, row["parent_id"], row["name"], row["status"], flush=True)

    while True:
        # nav-path $select leaves each attachment's inline Binary out of the listing (measured
        # 1.2s vs 12s per page) - the bytes come from /Binary/$value instead; $orderby keeps
        # $skip paging stable, since SAP returns a different row order run-to-run without it
        params = {"$format": "json", "$top": page, "$skip": skip, "$orderby": "ObjectID",
                  "$select": f"ObjectID,{id_field}," + ",".join(f"{nav}/{f}" for f in ATTACHMENT_FIELDS),
                  "$expand": nav}
        response = client._get(url, params)
        response.raise_for_status()
        parents = response.json()["d"]["results"]

        jobs = []
        for parent in parents:
            attachments = parent[nav]
            if isinstance(attachments, dict):
                attachments = attachments.get("results", [])
            for att in attachments:
                found += 1
                if att.get("ObjectID") not in done:
                    jobs.append((parent.get(id_field), att))
        # queue this page's downloads and keep listing - waiting for a page's downloads to finish
        # left workers idle behind one large file; finished ones are logged by this thread only
        pending.update(pool.submit(_process, client, collection, pid, att, out_dir) for pid, att in jobs)
        for future in [f for f in pending if f.done()]:
            pending.discard(future)
            record(future)
        log_file.flush()

        skip += len(parents)
        if len(parents) < page or (limit and skip >= limit):
            break
    for future in as_completed(pending):
        record(future)
    print(f"{service}/{collection}: {skip} rows scanned, {found} attachments, "
          f"{ok} downloaded this run ({found - ok} skipped/failed/links)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default="khpurchaseorder", choices=[*SERVICES, "all"])
    parser.add_argument("--limit", type=int, help="stop after N parent rows per collection")
    parser.add_argument("--workers", type=int, default=1, help="parallel file downloads")
    args = parser.parse_args()

    client = SAPODataClient(load_config())
    services = list(SERVICES) if args.service == "all" else [args.service]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for service in services:
            export_service(client, service, args.limit, pool)


def export_service(client, service, limit, pool):
    out_dir = os.path.join(OUT_DIR, service)
    os.makedirs(out_dir, exist_ok=True)

    # resume: attachments already downloaded OK in a previous run are skipped
    log_path = os.path.join(out_dir, "_attachments_log.csv")
    done = set()
    if os.path.exists(log_path):
        with open(log_path, newline="") as f:
            done = {r["attachment_object_id"] for r in csv.DictReader(f) if r["status"] == "ok"}
    new_log = not os.path.exists(log_path)
    with open(log_path, "a", newline="") as f:
        log = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_log:
            log.writeheader()
        for collection, nav in SERVICES[service]:
            export_collection(client, service, collection, nav, out_dir, log, f, done, limit, pool)
    print(f"log -> {log_path}")


if __name__ == "__main__":
    main()
