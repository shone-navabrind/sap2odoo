"""
Parses the Cloud Applications Studio custom OData service definitions in
`byd-api-samples-main/Custom OData Services/*.xml` into a machine-readable index.

Each .xml is an exported ABAP-XML service definition. Once imported into the ByDesign tenant
(Application and User Management -> OData Services -> Custom OData Services) it becomes live at
    {SAP_BASE_URL}/sap/byd/odata/cust/v1/<SERVICE_NAME>/<EntitySet>

Parsing them offline tells us, *without* importing anything, exactly which entity sets and which
fields each service would expose - which is what lets us say precisely which files the user needs
to import to close a given gap, instead of guessing, and lets us confirm that services already
imported are being read in full.

Structure (confirmed by inspection against the three services already live on the tenant):
    SERVICE/EXT_DEF/item
      SERVICE_NAME, SERVICE_NAMESPACE
      ENTITY_TYPE/item -> NAME, PROPERTY/item -> NAME, DATA_TYPE, MAX_LENGTH
      ENTITY_SET/item  -> codelist value lists only (UI dropdowns), NOT the business entity sets

The live entity set name is the ENTITY_TYPE name plus "Collection" - verified against
khpurchaseorder (PurchaseOrder/Item/Supplier -> PurchaseOrderCollection/ItemCollection/
SupplierCollection) and vmumaterial (Material/Text/Purchasing/Sales/ProductCategory -> ...Collection).

Run: python -m src.parse_service_defs           # summary table + writes the index
     python -m src.parse_service_defs <service> # everything one service exposes
"""

import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

SAMPLES_DIR = "byd-api-samples-main/Custom OData Services"
INDEX_PATH = "schema_snapshots/custom_service_index.json"

# Entity types that exist in every service and carry no business data.
BOILERPLATE_TYPES = {"CodeList", "ContextualCodeList"}


def _text(node, tag):
    child = node.find(tag)
    return child.text if child is not None and child.text else ""


def parse_service(path):
    """-> {file, service, namespace, entity_sets: [{name, type, fields, field_count}]}"""
    root = ET.parse(path).getroot()
    service = root.find(".//SERVICE/EXT_DEF/item")
    if service is None:
        return None

    entity_sets = []
    for item in service.findall("ENTITY_TYPE/item"):
        type_name = _text(item, "NAME")
        if not type_name or type_name in BOILERPLATE_TYPES:
            continue
        fields = []
        for prop in item.findall("PROPERTY/item"):
            name = _text(prop, "NAME")
            if not name:
                continue
            fields.append({
                "name": name,
                "type": _text(prop, "DATA_TYPE"),
                "max_length": _text(prop, "MAX_LENGTH"),
            })
        entity_sets.append({
            "name": f"{type_name}Collection",
            "entity_type": type_name,
            "fields": [f["name"] for f in fields],
            "field_types": {f["name"]: f["type"] for f in fields},
            "field_count": len(fields),
        })

    return {
        "file": os.path.basename(path),
        "service": _text(service, "SERVICE_NAME"),
        "namespace": _text(service, "SERVICE_NAMESPACE"),
        "start_ui": _text(service, "START_UI_APPLICATION_ID"),
        "entity_sets": sorted(entity_sets, key=lambda e: -e["field_count"]),
    }


def build_index(samples_dir=SAMPLES_DIR):
    out = []
    for path in sorted(glob.glob(os.path.join(samples_dir, "*.xml"))):
        try:
            parsed = parse_service(path)
        except ET.ParseError as exc:
            print(f"  !! {os.path.basename(path)}: {exc}", file=sys.stderr)
            continue
        if parsed:
            out.append(parsed)
    return out


def load_index(path=INDEX_PATH):
    """The cached index, rebuilt from the .xml files if it isn't on disk yet."""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return build_index()


def main():
    index = build_index()

    if len(sys.argv) > 1:
        wanted = sys.argv[1].lower()
        for svc in index:
            if svc["service"].lower() == wanted:
                print(f"{svc['service']}  ({svc['file']}, namespace={svc['namespace']})")
                print(f"  URL: /sap/byd/odata/cust/v1/{svc['service']}/<EntitySet>")
                for es in svc["entity_sets"]:
                    print(f"\n  {es['name']}  ({es['field_count']} fields)")
                    print("    " + ", ".join(es["fields"]))
                return
        print(f"No such service: {sys.argv[1]}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    print(f"{'service':<34} {'sets':>5} {'fields':>7}  biggest entity sets")
    print("-" * 125)
    for svc in index:
        total = sum(e["field_count"] for e in svc["entity_sets"])
        top = ", ".join(f"{e['name']}({e['field_count']})" for e in svc["entity_sets"][:3])
        print(f"{svc['service']:<34} {len(svc['entity_sets']):>5} {total:>7}  {top[:75]}")
    print("-" * 125)
    print(f"{len(index)} services, "
          f"{sum(len(s['entity_sets']) for s in index)} entity sets, "
          f"{sum(e['field_count'] for s in index for e in s['entity_sets'])} field definitions")
    print(f"\nWrote {INDEX_PATH}")


if __name__ == "__main__":
    sys.exit(main())
