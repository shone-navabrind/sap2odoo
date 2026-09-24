import logging
import os
import re
from datetime import datetime, timezone

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import Config

logger = logging.getLogger("sap2odoo")

_SAP_DATE_RE = re.compile(r"/Date\((-?\d+)\)/")


def parse_sap_date(value):
    """Convert SAP OData's /Date(epoch_ms)/ format to an ISO date string, or pass through."""
    if not value:
        return ""
    match = _SAP_DATE_RE.match(value)
    if not match:
        return value
    epoch_ms = int(match.group(1))
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


_ENTITY_TYPE_RE = re.compile(r'<EntityType Name="([^"]+)"[^>]*>(.*?)</EntityType>', re.DOTALL)
_PROPERTY_RE = re.compile(r'<Property Name="([^"]+)"')
_PROPERTY_TYPE_RE = re.compile(r'<Property Name="([^"]+)"[^>]*\bType="([^"]+)"')
_ENTITY_SET_RE = re.compile(r'<EntitySet Name="([^"]+)"[^>]*EntityType="[^"]*\.([^"]+)"')

# SAP rejects a bulk $select that includes one of these - confirmed by testing: e.g.
# khcustomerinvoice/ItemAttachmentFolderCollection's "Binary" field (Edm.Binary, the raw file
# content) returns "400 Bad Request" the moment it's in $select, even though every other field
# on the same entity works fine. Binary/stream content needs its own dedicated $value request
# per row, not a bulk list query - out of scope here, so these fields are excluded from $select
# the same way P_*/PARA_* query-parameter fields already are, rather than failing the whole
# entity set over one field that could never have worked this way.
_UNSELECTABLE_TYPES = {"Edm.Binary", "Edm.Stream"}


def parse_metadata(xml_text):
    """
    Parse an OData $metadata EDMX document into {entity_set_name: [property_name, ...]}.
    No assumptions about which fields matter - every property SAP declares is included.
    """
    properties_by_type = {}
    for type_name, body in _ENTITY_TYPE_RE.findall(xml_text):
        properties_by_type[type_name] = _PROPERTY_RE.findall(body)

    fields_by_entity_set = {}
    for set_name, type_name in _ENTITY_SET_RE.findall(xml_text):
        fields_by_entity_set[set_name] = properties_by_type.get(type_name, [])

    return fields_by_entity_set


def parse_metadata_unselectable_fields(xml_text):
    """
    {entity_set_name: {field_name, ...}} for fields whose Edm type can't go in a bulk $select
    (see _UNSELECTABLE_TYPES) - mirrors parse_metadata's structure, from the same XML.
    """
    unselectable_by_type = {}
    for type_name, body in _ENTITY_TYPE_RE.findall(xml_text):
        unselectable_by_type[type_name] = {
            name for name, edm_type in _PROPERTY_TYPE_RE.findall(body)
            if edm_type in _UNSELECTABLE_TYPES
        }

    unselectable_by_entity_set = {}
    for set_name, type_name in _ENTITY_SET_RE.findall(xml_text):
        unselectable_by_entity_set[set_name] = unselectable_by_type.get(type_name, set())

    return unselectable_by_entity_set


def _olap_merge_key(fields):
    """
    Pick the field to merge an OLAP entity's field-chunks on, or None if there isn't one.

    Chunks of an analytics query are separate GROUP BYs and come back with different row
    counts, so they can only be recombined on a value that identifies a business object.

    A *_UUID field is the safest choice and is preferred. Failing that, ByDesign's analytics
    naming convention is used: dimensions ("characteristics") are prefixed C, their display
    texts T, and measures ("key figures") F or K. Merging on a measure would be meaningless,
    so only a C-prefixed dimension is considered - e.g. the G/L account master query has no
    UUID at all and is keyed by CGLACCT, the account number.
    """
    uuid_field = next((f for f in fields if "UUID" in f), None)
    if uuid_field:
        return uuid_field
    return next((f for f in fields if re.fullmatch(r"C[A-Z0-9_]+", f)), None)


def _flatten_expanded(row):
    """
    Flatten an $expand response so the raw dump stays a flat table.

    An expanded navigation property arrives as a nested object (or {"results": [...]} for a
    to-many nav). Its fields become "NavProp.Field" keys. Unexpanded navs arrive as
    {"__deferred": ...} and carry no data, so they're dropped.
    """
    flat = {}
    for key, value in row.items():
        if not isinstance(value, dict):
            flat[key] = value
            continue
        if "__deferred" in value:
            continue
        nested = value
        if "results" in nested and isinstance(nested["results"], list):
            nested = nested["results"][0] if nested["results"] else {}
        for sub_key, sub_value in nested.items():
            if sub_key == "__metadata" or isinstance(sub_value, dict):
                continue
            flat[f"{key}.{sub_key}"] = sub_value
    return flat


class SAPODataClient:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.auth = (config.sap_username, config.sap_password)
        self.session.headers.update({"Accept": "application/json"})

    def _base_params(self):
        params = {}
        if self.config.sap_client:
            params["sap-client"] = self.config.sap_client
        return params

    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=20),
    )
    def _get(self, url, params):
        response = self.session.get(
            url, params=params, verify=self.config.verify_ssl, timeout=60
        )
        if response.status_code >= 500:
            response.raise_for_status()
        return response

    def get_entity_set(self, service, entity_set, select=None, filter_expr=None, expand=None, max_rows=None):
        """
        Fetch every row of an OData entity set, paginating with $skip/$top.

        `expand` pulls a navigation property inline. Needed where a child collection can't be
        joined from its own top-level endpoint - e.g. khproductionorder's
        MainProductOutputCollection has no ParentObjectID and its ObjectIDs don't match the
        order's, so $expand=MainProductOutput is the only way to link a production order to the
        product it produces.

        `max_rows` stops pagination early once at least that many rows are collected (still
        returns full pages, just fewer of them) - for a quick/limited test pull instead of
        pulling a whole tenant's history.
        """
        url = f"{self.config.sap_base_url}/{service}/{entity_set}"
        page_size = self.config.page_size
        skip = 0
        rows = []

        while True:
            params = self._base_params()
            params["$format"] = "json"
            params["$top"] = page_size
            params["$skip"] = skip
            if select:
                params["$select"] = ",".join(select)
            if filter_expr:
                params["$filter"] = filter_expr
            if expand:
                params["$expand"] = expand

            response = self._get(url, params)
            if response.status_code == 404:
                logger.error(
                    "%s/%s returned 404 - service may not be activated for this user (%s)",
                    service, entity_set, url,
                )
                return rows
            response.raise_for_status()

            payload = response.json()
            page = payload.get("d", {}).get("results", [])
            rows.extend(page)

            if len(page) < page_size:
                break
            if max_rows and len(rows) >= max_rows:
                break
            skip += page_size

        return rows[:max_rows] if max_rows else rows

    def get_metadata_xml(self, service):
        url = f"{self.config.sap_base_url}/{service}/$metadata"
        response = self.session.get(
            url, params=self._base_params(), headers={"Accept": "application/xml"},
            verify=self.config.verify_ssl, timeout=60,
        )
        response.raise_for_status()
        return response.text

    def get_entity_fields(self, service, entity_set):
        """
        Return (field_names, unselectable_field_names) for an entity set - the full list SAP
        declares, and the subset of those whose Edm type can't go in a bulk $select (see
        _UNSELECTABLE_TYPES / parse_metadata_unselectable_fields).

        Prefers a static schema_snapshots/<service_basename>.metadata.xml file over a live
        $metadata call. On this tenant, live $metadata for BI/analytics ".svc" services turned
        out to be STATEFUL - it returned ~90-100 real properties per entity type the first time
        it was queried, then later collapsed to just ID+TotaledProperties on repeat calls with
        no code or query change, for reasons outside this tool's control (most likely tied to
        the underlying SAP report/query designer's current field configuration, since these are
        BI query services, not plain CRUD OData). A captured-known-good snapshot avoids the
        raw extractor silently losing 100+ real columns if metadata collapses again.
        """
        basename = service.rsplit("/", 1)[-1]
        snapshot_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "schema_snapshots", f"{basename}.metadata.xml"
        )
        if os.path.exists(snapshot_path):
            with open(snapshot_path, encoding="utf-8") as f:
                xml_text = f.read()
            source = f"snapshot ({snapshot_path})"
        else:
            xml_text = self.get_metadata_xml(service)
            source = "live $metadata"

        fields = parse_metadata(xml_text).get(entity_set, [])
        unselectable = parse_metadata_unselectable_fields(xml_text).get(entity_set, set())
        logger.info("%s/%s: %d fields from %s", service, entity_set, len(fields), source)
        return fields, unselectable

    def get_entity_set_all_fields(self, service, entity_set, chunk_size=8, expand=None, max_rows=None):
        """
        Fetch every field SAP declares for an entity set, without deciding upfront which
        ones matter. Used for raw extraction (src/extract_raw.py) so field mapping decisions
        happen AFTER seeing real data, not before.

        `expand` (CRUD entities only) pulls a navigation property inline and flattens it into
        "NavProp.Field" keys, so the raw dump stays a flat table. $select is dropped when
        expanding, since the expanded paths would have to be listed there too and CRUD entities
        have no field-count limit anyway.

        Two different kinds of ByDesign entity set have been seen so far:
        1. BI/OLAP aggregate query results (e.g. the *_Q0001QueryResults analytics entities):
           - Selecting more than a handful of dimension fields at once fails with
             "TOO_MANY_DRILL_DOWN_OBJECTS", so fields are fetched in chunks.
           - CONFIRMED BY TESTING: the result is grouped by whichever dimensions are selected,
             so different field chunks return DIFFERENT ROW COUNTS - merging by row position is
             unsafe. The first field whose name contains "UUID" is used as a stable merge key
             instead, kept in every chunk.
           - These entities always carry a synthetic "TotaledProperties" field alongside a
             synthetic composite "ID" (SAP-generated from whichever dimensions are selected, not
             real data, and errors if selected alone) - both are excluded, but ONLY for this
             entity shape (detected by the presence of "TotaledProperties").
        2. Plain CRUD OData (e.g. custom services like khpurchaseorder, vmumaterial,
           vmumaterialvaluationdata): no drill-down limit at all, so ALL fields are fetched in
           one request regardless of count - no chunking, no merge-key needed. "ID" is a real
           business field here (e.g. the PO number), not excluded.
        """
        all_fields, unselectable = self.get_entity_fields(service, entity_set)
        is_olap_entity = "TotaledProperties" in all_fields
        excluded = ({"TotaledProperties", "ID"} if is_olap_entity else set()) | unselectable
        fields = [
            f for f in all_fields
            if not f.startswith("P_") and not f.startswith("PARA_") and f not in excluded
        ]

        if not fields:
            logger.warning("%s/%s: no fields found in $metadata", service, entity_set)
            return [], all_fields

        if not is_olap_entity:
            # Plain CRUD - no drill-down limit, so no need to chunk or find a merge key at all.
            if expand:
                rows = self.get_entity_set(service, entity_set, expand=expand, max_rows=max_rows)
                return [_flatten_expanded(r) for r in rows], all_fields
            return self.get_entity_set(service, entity_set, select=fields, max_rows=max_rows), all_fields

        key_field = _olap_merge_key(fields)
        if key_field is None:
            chunks = [fields[i : i + chunk_size] for i in range(0, len(fields), chunk_size)]
            if len(chunks) == 1:
                # Only one chunk needed - no cross-chunk merge ambiguity, so this is a plain fetch.
                return self.get_entity_set(service, entity_set, select=chunks[0]), all_fields
            # Nothing stable to merge on, and merging by row position is known-unsafe here.
            # Return the first chunk only rather than a differently-shaped result that would
            # silently corrupt every caller downstream.
            logger.warning(
                "%s/%s: no merge key among %d fields - returning only the first %d fields",
                service, entity_set, len(fields), len(chunks[0]),
            )
            return self.get_entity_set(service, entity_set, select=chunks[0]), all_fields

        other_fields = [f for f in fields if f != key_field]
        chunks = [other_fields[i : i + chunk_size - 1] for i in range(0, len(other_fields), chunk_size - 1)] or [[]]

        merged_by_key = {}
        widest_chunk = 0
        for chunk in chunks:
            select = [key_field] + chunk
            rows = self.get_entity_set(service, entity_set, select=select)
            widest_chunk = max(widest_chunk, len(rows))
            for row in rows:
                key_value = row.get(key_field)
                if not key_value:
                    continue
                merged_by_key.setdefault(key_value, {})[key_field] = key_value
                merged_by_key[key_value].update(row)

        # If any chunk returned more rows than there are distinct key values, that chunk was
        # grouped more finely than the key - so some of its rows overwrote each other and the
        # merged result is an arbitrary one-per-key sample. Say so loudly rather than quietly
        # publishing a lossy table.
        if widest_chunk > len(merged_by_key):
            logger.warning(
                "%s/%s: merge key %s is not unique - %d distinct values but one chunk returned "
                "%d rows, so non-key fields are a sample, not a complete join",
                service, entity_set, key_field, len(merged_by_key), widest_chunk,
            )

        logger.info("%s/%s: merged %d chunks on %s -> %d rows",
                    service, entity_set, len(chunks), key_field, len(merged_by_key))
        return list(merged_by_key.values()), all_fields
