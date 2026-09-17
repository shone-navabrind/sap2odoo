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
_ENTITY_SET_RE = re.compile(r'<EntitySet Name="([^"]+)"[^>]*EntityType="[^"]*\.([^"]+)"')


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

    def get_entity_set(self, service, entity_set, select=None, filter_expr=None):
        """Fetch every row of an OData entity set, paginating with $skip/$top."""
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
            skip += page_size

        return rows

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
        Return the full list of field names SAP declares for an entity set.

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
        logger.info("%s/%s: %d fields from %s", service, entity_set, len(fields), source)
        return fields

    def get_entity_set_all_fields(self, service, entity_set, chunk_size=8):
        """
        Fetch every field SAP declares for an entity set, without deciding upfront which
        ones matter. Used for raw extraction (src/extract_raw.py) so field mapping decisions
        happen AFTER seeing real data, not before.

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
        all_fields = self.get_entity_fields(service, entity_set)
        is_olap_entity = "TotaledProperties" in all_fields
        excluded = {"TotaledProperties", "ID"} if is_olap_entity else set()
        fields = [
            f for f in all_fields
            if not f.startswith("P_") and not f.startswith("PARA_") and f not in excluded
        ]

        if not fields:
            logger.warning("%s/%s: no fields found in $metadata", service, entity_set)
            return [], all_fields

        if not is_olap_entity:
            # Plain CRUD - no drill-down limit, so no need to chunk or find a merge key at all.
            return self.get_entity_set(service, entity_set, select=fields), all_fields

        key_field = next((f for f in fields if "UUID" in f), None)
        if key_field is None:
            chunks = [fields[i : i + chunk_size] for i in range(0, len(fields), chunk_size)]
            if len(chunks) == 1:
                # Only one chunk needed - no cross-chunk merge ambiguity, so this is a plain fetch.
                return self.get_entity_set(service, entity_set, select=chunks[0]), all_fields
            logger.warning(
                "%s/%s: no *_UUID field found to merge %d chunks on - returning unmerged chunks",
                service, entity_set, len(chunks),
            )
            return [self.get_entity_set(service, entity_set, select=c) for c in chunks], all_fields

        other_fields = [f for f in fields if f != key_field]
        chunks = [other_fields[i : i + chunk_size - 1] for i in range(0, len(other_fields), chunk_size - 1)] or [[]]

        merged_by_key = {}
        for chunk in chunks:
            select = [key_field] + chunk
            rows = self.get_entity_set(service, entity_set, select=select)
            for row in rows:
                key_value = row.get(key_field)
                if not key_value:
                    continue
                merged_by_key.setdefault(key_value, {})[key_field] = key_value
                merged_by_key[key_value].update(row)

        return list(merged_by_key.values()), all_fields
