"""Bulk export of Quality Control Material Inspection records: fields + attachments.

Why this is a browser-automation script and not another `extract_raw.py` source: there is no
OData/API path for Material Inspection on this tenant (confirmed - no `kh*` service exists for
it, and the live UI's own data calls go through SAP's internal obfuscated `/ap/ui/json` RPC
protocol, session-bound and not a callable REST endpoint). Driving the real UI is the only way
to reach this data and its PDF attachments. The tenant has ~10,000 inspection records (the list
view's own binding reports `aria-rowcount`/binding length of exactly 10000 - SAP's hard display
cap for this list, so there may be more; this script exports whatever the list actually returns).

Two phases:
  1. Enumerate every Inspection ID in the "All Inspections" monitoring list. Fast: pages the
     list's SAPUI5 table control directly via its own `setFirstVisibleRowIndex` API and reads the
     rendered row text - no per-record navigation needed for this part.
  2. For each ID: open its record (double-click the row), scrape every labeled field that has a
     value from the read-only "Inspection Overview" screen - this walks `.sapBUiExtIdrElement`
     label/value pairs generically, so it captures whatever fields a given record's screen shows
     rather than a fixed hardcoded list - then switch to Edit mode and download attachments from
     both the Decision tab and the Miscellaneous tab's Attachments sub-tabs (the two places
     attachments live).

Resumable: IDs already present in material_inspections.csv are skipped on re-run. The enumerated
ID list is cached to `_ids.json` so a re-run doesn't repeat the ~10-20 minute enumeration pass
(pass --re-enumerate to force a refresh).

Three subcommands, so the long scraping run never needs a visible window or your attention:
  python -m src.quality_inspections.export login   # once: visible browser, log in yourself,
                                                     # saves the session to _session.json
  python -m src.quality_inspections.export run      # headless, unattended, safe to background
                                                     # (nohup ... &) and disconnect from
  python -m src.quality_inspections.export status   # anytime, offline: done/failed/remaining,
                                                     # rate, ETA, whether a run is in progress

`run --shard K/N` runs N headless browsers in parallel, each taking every Nth id from the
cached `_ids.json` and keeping its own csv/progress/failed/pid/session files (so they never
overwrite each other); `status` sums across all of them:
  for k in 0 1 2 3; do nohup python -m src.quality_inspections.export run --shard $k/4 \
      > qi_shard$k.log 2>&1 & done
Shards skip ids already exported by any shard or by an earlier unsharded run.

This script never sees or handles your password - `login` is the only subcommand that opens a
real window, specifically so you can type it yourself.
"""

import argparse
import calendar
import csv
import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.config import load_config

LIST_COMPONENT = "/SAP_BYD_APPLICATION_UI/scm/qaqc/materialinspectionlist.OWL.uicomponent"
ALL_INSPECTIONS_INPORT = "MaterialInspectionRootMIAllIn"


def list_url(base_url: str) -> str:
    return (
        f"{base_url}/sap/public/ap/ui/repository/SAP_UI/HTMLOBERON5/client.html"
        f"?app.component={LIST_COMPONENT}&rootWindow=X"
    )


class LoginRequiredError(RuntimeError):
    """Raised in headless mode when the saved session is missing/expired and a human has to
    log in - there is no one to wait for in a headless/background run, so fail fast instead of
    sitting on a 30-minute poll."""


def wait_for_login(page, headless: bool, timeout_s: int = 1800) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.locator('input[type="password"]').count() == 0 and (
            page.locator('text=Select Inport').count() > 0
            or page.locator('text=Monitoring: Inspections').count() > 0
        ):
            return
        if page.locator('input[type="password"]').count() > 0:
            if headless:
                raise LoginRequiredError(
                    "No saved login session (or it expired). Run "
                    "'python -m src.quality_inspections.export login' once to log in, "
                    "then re-run 'run'."
                )
            print("Waiting for manual login in the opened browser window...")
        time.sleep(3)
    raise TimeoutError("Timed out waiting for manual SAP login.")


def open_all_inspections_list(page, base_url: str, headless: bool = False) -> None:
    page.goto(list_url(base_url), wait_until="domcontentloaded", timeout=60000)
    wait_for_login(page, headless=headless)
    if page.locator('text=Select Inport').count() > 0:
        # Same DOM-recycling behavior seen in the main grid can detach this dialog's own
        # elements mid-click; force=True plus a retry avoids a rare 30s hang/failure here.
        for attempt in range(2):
            try:
                page.get_by_text(ALL_INSPECTIONS_INPORT, exact=True).click(force=True, timeout=5000)
                page.get_by_role("button", name="Select").click(force=True, timeout=5000)
                break
            except Exception:  # noqa: BLE001
                if attempt == 1:
                    raise
                time.sleep(1.0)
    page.wait_for_selector('text=Monitoring: Inspections', timeout=30000)
    page.wait_for_selector('[role="grid"]', timeout=30000)
    # The grid renders immediately but its data binding resolves a moment later via an
    # async call - table_row_count() reads 0 until that completes.
    deadline = time.time() + 30
    while time.time() < deadline and table_row_count(page) == 0:
        time.sleep(1)


def get_table_handle(page):
    return page.evaluate_handle(
        "() => sap.ui.getCore().byId(document.querySelector('[role=\"grid\"]')"
        ".closest('[id]').id)"
    )


def rendered_rows(page) -> list[tuple[str, str]]:
    """Return (inspection_id, row_index) for each currently rendered row."""
    return page.evaluate(
        """
        () => Array.from(document.querySelectorAll('[role="row"]'))
            .map(r => {
                const idx = r.getAttribute('aria-rowindex');
                const cells = r.querySelectorAll('[role="gridcell"]');
                const text = cells[2] ? cells[2].innerText.trim().split('\\n')[0] : null;
                return idx && text ? [text, idx] : null;
            })
            .filter(x => x)
        """
    )


def set_first_visible_row(page, index: int) -> None:
    page.evaluate(
        """
        (index) => {
            const grid = document.querySelector('[role="grid"]');
            const id = grid.closest('[id]').id;
            sap.ui.getCore().byId(id).setFirstVisibleRowIndex(index);
        }
        """,
        index,
    )


def table_row_count(page) -> int:
    return page.evaluate(
        """
        () => {
            const grid = document.querySelector('[role="grid"]');
            const id = grid.closest('[id]').id;
            return sap.ui.getCore().byId(id).getBinding('rows').getLength();
        }
        """
    )


def wait_for_rows_to_update(page, previous_ids: set[str], timeout_s: float = 3.0) -> list[tuple[str, str]]:
    """setFirstVisibleRowIndex() re-renders asynchronously; poll until the rendered row ids
    actually change (or the page genuinely repeats, at the timeout) instead of reading stale rows."""
    deadline = time.time() + timeout_s
    rows = rendered_rows(page)
    while time.time() < deadline:
        ids_now = {r[0] for r in rows}
        if ids_now and ids_now != previous_ids:
            return rows
        time.sleep(0.15)
        rows = rendered_rows(page)
    return rows


def enumerate_ids(page) -> list[str]:
    total = table_row_count(page)
    print(f"List reports {total} total inspection records. Enumerating...")
    seen: dict[str, None] = {}
    set_first_visible_row(page, 0)
    time.sleep(1.0)
    page_size = max(len(rendered_rows(page)), 1)
    index = 0
    previous_ids: set[str] = set()
    while index < total:
        set_first_visible_row(page, index)
        rows = wait_for_rows_to_update(page, previous_ids)
        previous_ids = {r[0] for r in rows}
        for insp_id, _ in rows:
            seen[insp_id] = None
        index += page_size
        if index % (page_size * 50) == 0:
            print(f"  ...{len(seen)} ids so far ({index}/{total})")
    print(f"Enumeration done: {len(seen)} distinct inspection ids.")
    return list(seen.keys())


def open_row(page, insp_id: str) -> bool:
    """Double-click the row showing insp_id to open its record. This SAPUI5 grid continuously
    recycles/reflows its virtualized row DOM even at rest, so Playwright's normal actionability
    wait (element must be visible AND stable AND receiving events) can hang the full 30s on a
    row that is actually fine to click - force=True skips that wait and dispatches the click
    directly. Matching by the row's own id text (not aria-rowindex) avoids clicking the wrong
    row if the grid reflows between locating the row and clicking it."""
    row_el = page.locator('[role="row"]').filter(has_text=insp_id)
    for attempt in range(2):
        try:
            row_el.first.dblclick(force=True, timeout=5000)
            page.wait_for_selector("text=Inspection Overview:", timeout=15000)
            return True
        except Exception:  # noqa: BLE001 - retry once, then let the caller treat this as not-found
            if attempt == 0:
                time.sleep(1.0)
    return False


SEARCH_FIELD_SELECTOR = "#__pane0-searchField-I"


def find_and_open(page, insp_id: str) -> bool:
    """Locate insp_id via the list's own search box and double-click it to open the record.

    Scrolling/paging through the virtualized grid to match a row by position was unreliable:
    confirmed live that the row order this list returns is NOT stable across requeries - after
    closing one record and resetting to the top, scanning forward landed on a completely
    different set of ids than the same scan had shown moments earlier. The search box instead
    filters the grid server-side to just the matching id(s), confirmed live to return exactly
    one row for a real id and zero for a nonexistent one."""
    search = page.locator(SEARCH_FIELD_SELECTOR)
    search.fill("")
    search.fill(insp_id)
    search.press("Enter")
    deadline = time.time() + 8.0
    while time.time() < deadline and table_row_count(page) == 0:
        time.sleep(0.2)
    if table_row_count(page) == 0:
        return False
    return open_row(page, insp_id)


FIELD_ITEM_SELECTOR = ".sapBUiExtIdrElement, .sapBUiSGrpItm"


def wait_for_stable_count(page, selector: str, timeout_s: float = 12.0) -> None:
    """Wait until the field-item count holds steady for a full second before scraping,
    in case any section is still being appended."""
    deadline = time.time() + timeout_s
    previous = -1
    stable_since = time.time()
    while time.time() < deadline:
        current = page.locator(selector).count()
        if current != previous:
            previous = current
            stable_since = time.time()
        elif current > 0 and time.time() - stable_since > 1.0:
            return
        time.sleep(0.2)


def extract_overview_fields(page) -> dict:
    """Walk every labeled field on the Overview screen. Two distinct containers hold fields
    here: the top "Identification Region" banner uses `.sapBUiExtIdrElement` (label in
    `.sapBUiExtIdrLabel`, value in `.sapBUiExtIdrControl`), and each body section (Logistics
    Details, Inspection Details, Sampling Details, Decision Details) uses `.sapBUiSGrpItm`
    (label in `.sapBUiSGrpLbl`, value in `.sapBUiSGrpCntnt`) - confirmed via live DOM dump
    that these are genuinely two different containers, not one late-rendering pattern."""
    wait_for_stable_count(page, FIELD_ITEM_SELECTOR)
    pairs = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('.sapBUiExtIdrElement, .sapBUiSGrpItm')).map(el => {
            const l = el.querySelector('.sapBUiExtIdrLabel [aria-label], .sapBUiSGrpLbl [aria-label]');
            const v = el.querySelector('.sapBUiExtIdrControl, .sapBUiSGrpCntnt');
            return [l ? l.getAttribute('aria-label') : null,
                    v ? v.textContent.trim() : null];
        }).filter(([l, v]) => l && v)
        """
    )
    fields: dict[str, str] = {}
    for label, value in pairs:
        key = label
        suffix = 2
        while key in fields and fields[key] != value:
            key = f"{label}_{suffix}"
            suffix += 1
        fields[key] = value
    return fields


def extract_defects(page) -> list[dict]:
    if page.locator("text=No records found").count() > 0:
        return []
    return page.evaluate(
        """
        () => {
            const grids = Array.from(document.querySelectorAll('[role="grid"]'));
            const grid = grids.find(g => g.textContent.includes('Defect Code'));
            if (!grid) return [];
            const headers = Array.from(grid.querySelectorAll('[role="columnheader"]'))
                .map(h => h.textContent.trim());
            const rows = Array.from(grid.querySelectorAll('[role="row"]'))
                .filter(r => !r.querySelector('[role="columnheader"]'));
            return rows.map(r => {
                const cells = Array.from(r.querySelectorAll('[role="gridcell"]'))
                    .map(c => c.textContent.trim());
                const obj = {};
                headers.forEach((h, i) => { if (cells[i]) obj[h] = cells[i]; });
                return obj;
            }).filter(obj => Object.keys(obj).length > 0);
        }
        """
    )


def open_edit_mode(page) -> None:
    page.get_by_role("button", name="Edit", exact=True).click()
    page.wait_for_selector("text=Miscellaneous", timeout=30000)


def download_attachments_from_tab(page, tab_label: str, insp_id: str, attach_root: Path) -> list[str]:
    saved: list[str] = []
    tab = page.get_by_role("tab", name=tab_label, exact=True)
    if tab.count() == 0:
        return saved
    tab.first.click()
    attachments_subtab = page.get_by_role("tab", name="Attachments", exact=True)
    deadline = time.time() + 5.0
    while attachments_subtab.count() == 0 and time.time() < deadline:
        time.sleep(0.3)
    if attachments_subtab.count() == 0:
        return saved
    attachments_subtab.first.click()
    # The attachments grid itself renders in two stages: the toolbar (Download All button,
    # and even a transient "No records found" placeholder) can appear before the actual
    # attachment rows have loaded - confirmed live: polling for the button/placeholder alone
    # broke out before the row(s) existed, so a real record with attachments still read as
    # empty. Poll for the actual row links instead; only treat it as empty once that has had
    # the full timeout to appear.
    attachment_links = page.get_by_role("link", name=re.compile(r"^Link "))
    deadline = time.time() + 8.0
    while attachment_links.count() == 0 and time.time() < deadline:
        time.sleep(0.3)
    if attachment_links.count() == 0:
        return saved
    download_all = page.get_by_role("button", name=re.compile("Download All"))
    if download_all.count() == 0:
        return saved
    out_dir = attach_root / insp_id
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with page.expect_download(timeout=60000) as dl_info:
            download_all.first.click()
        download = dl_info.value
        zip_path = out_dir / download.suggested_filename
        download.save_as(str(zip_path))
        if zip_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(out_dir)
                saved = zf.namelist()
            zip_path.unlink()
        else:
            saved = [zip_path.name]
    except Exception as exc:  # noqa: BLE001 - log and continue, one record must not kill the run
        print(f"  [{insp_id}] attachment download failed on '{tab_label}': {exc}")
    return saved


def close_record(page) -> None:
    close_btn = page.get_by_role("button", name="Close", exact=True)
    if close_btn.count() > 0:
        close_btn.first.click()
        time.sleep(0.5)


def load_existing_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(csv_path: Path, rows: list[dict]) -> None:
    fieldnames: list[str] = ["id"]
    seen = {"id"}
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def load_failed(failed_path: Path) -> list[dict]:
    if not failed_path.exists():
        return []
    return json.loads(failed_path.read_text())


def write_failed(failed_path: Path, failed: list[dict]) -> None:
    failed_path.write_text(json.dumps(failed, indent=2))


def write_progress(progress_path: Path, **fields) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    progress_path.write_text(json.dumps(fields, indent=2))


def cmd_login(args: argparse.Namespace) -> None:
    cfg = load_config()
    export_dir = Path(args.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    session_path = export_dir / "_session.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            accept_downloads=True,
            storage_state=str(session_path) if session_path.exists() else None,
        )
        page = context.new_page()
        open_all_inspections_list(page, cfg.sap_base_url, headless=False)
        context.storage_state(path=str(session_path))
        browser.close()
    print(f"Login confirmed, session saved to {session_path}. You can now run 'run' headless.")


def cmd_run(args: argparse.Namespace) -> None:
    cfg = load_config()
    export_dir = Path(args.export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    attach_root = export_dir / "attachments"
    # --shard K/N: this process takes every Nth id from the shared _ids.json and keeps its own
    # csv/progress/failed/pid/session files, so N processes never write the same file
    shard, shards = map(int, args.shard.split("/")) if args.shard else (0, 1)
    suffix = f".shard{shard}" if args.shard else ""
    csv_path = export_dir / f"material_inspections{suffix}.csv"
    ids_cache_path = export_dir / "_ids.json"
    session_path = export_dir / f"_session{suffix}.json"
    if not session_path.exists():
        session_path = export_dir / "_session.json"
    failed_path = export_dir / f"_failed{suffix}.json"
    progress_path = export_dir / f"_progress{suffix}.json"
    pid_path = export_dir / f"_run{suffix}.pid"
    headless = not args.headed

    rows = load_existing_rows(csv_path)
    # skip anything already exported by any shard or by an earlier unsharded run
    done_ids = {r["id"] for path in export_dir.glob("material_inspections*.csv")
                for r in load_existing_rows(path)}
    failed = load_failed(failed_path)
    print(f"{len(done_ids)} records already exported, will be skipped.")

    pid_path.write_text(str(os.getpid()))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                accept_downloads=True,
                storage_state=str(session_path) if session_path.exists() else None,
            )
            page = context.new_page()

            open_all_inspections_list(page, cfg.sap_base_url, headless=headless)
            session_path = export_dir / f"_session{suffix}.json"
            context.storage_state(path=str(session_path))

            if ids_cache_path.exists() and not args.re_enumerate:
                all_ids = json.loads(ids_cache_path.read_text())
                print(f"Using cached id list ({len(all_ids)} ids). Pass --re-enumerate to refresh.")
            else:
                if args.shard:
                    raise SystemExit("--shard needs _ids.json: run once without --shard first")
                all_ids = enumerate_ids(page)
                ids_cache_path.write_text(json.dumps(all_ids))
            all_ids = all_ids[shard::shards]

            todo = [i for i in all_ids if i not in done_ids]
            if args.limit:
                todo = todo[: args.limit]
            print(f"Processing {len(todo)} records this run.")
            run_started_at = time.time()
            done_at_start = len(rows)

            for n, insp_id in enumerate(todo, 1):
                print(f"[{n}/{len(todo)}] Inspection {insp_id}")
                try:
                    if not find_and_open(page, insp_id):
                        print(f"  could not locate row for {insp_id}, skipping")
                        failed.append({"id": insp_id, "error": "could not locate row"})
                        continue
                    fields = extract_overview_fields(page)
                    defects = extract_defects(page)
                    open_edit_mode(page)
                    attachments = download_attachments_from_tab(
                        page, "Decision", insp_id, attach_root
                    ) + download_attachments_from_tab(page, "Miscellaneous", insp_id, attach_root)
                    close_record(page)

                    row = {"id": insp_id, **fields}
                    row["defects"] = json.dumps(defects) if defects else ""
                    row["attachment_count"] = len(attachments)
                    row["attachment_files"] = json.dumps(attachments)
                    rows.append(row)
                except Exception as exc:  # noqa: BLE001 - one bad record must not kill a 10k-record run
                    print(f"  FAILED on {insp_id}: {exc}")
                    failed.append({"id": insp_id, "error": str(exc)})
                    close_record(page)
                    continue
                finally:
                    elapsed_h = (time.time() - run_started_at) / 3600
                    done_this_run = len(rows) - done_at_start
                    rate = done_this_run / elapsed_h if elapsed_h > 0 else 0
                    remaining = len(all_ids) - len(rows) - len(failed)
                    write_progress(
                        progress_path,
                        total=len(all_ids),
                        done=len(rows),
                        failed=len(failed),
                        remaining=remaining,
                        percent=round(100 * (len(rows) + len(failed)) / len(all_ids), 2),
                        last_id=insp_id,
                        records_per_hour=round(rate, 1),
                        eta_hours=round(remaining / rate, 1) if rate > 0 else None,
                    )

                if n % 25 == 0:
                    write_csv(csv_path, rows)
                    write_failed(failed_path, failed)
                    context.storage_state(path=str(session_path))

            write_csv(csv_path, rows)
            write_failed(failed_path, failed)
            browser.close()
    finally:
        pid_path.unlink(missing_ok=True)

    print(f"Done. {len(rows)} total records in {csv_path}, {len(failed)} failed (see {failed_path}).")


def cmd_status(args: argparse.Namespace) -> None:
    export_dir = Path(args.export_dir)
    ids_cache_path = export_dir / "_ids.json"

    total = len(json.loads(ids_cache_path.read_text())) if ids_cache_path.exists() else None
    # sums the unsharded files and every --shard's own files
    done = sum(len(load_existing_rows(path)) for path in export_dir.glob("material_inspections*.csv"))
    failed = sum(len(load_failed(path)) for path in export_dir.glob("_failed*.json"))

    running_pids = []
    for pid_file in export_dir.glob("_run*.pid"):
        pid = int(pid_file.read_text())
        try:
            os.kill(pid, 0)
            running_pids.append(str(pid))
        except ProcessLookupError:
            pass

    print(f"Quality Inspections export status ({export_dir}/)")
    print(f"  Process:          {'RUNNING (pid ' + ', '.join(running_pids) + ')' if running_pids else 'not running'}")
    if total is None:
        print("  No id list enumerated yet - run 'run' at least once.")
        return
    remaining = total - done - failed
    percent = 100 * (done + failed) / total if total else 0
    print(f"  Total ids:        {total}")
    print(f"  Completed:        {done} ({100 * done / total:.1f}%)")
    print(f"  Failed/skipped:   {failed}")
    print(f"  Remaining:        {remaining}")
    print(f"  Overall progress: {percent:.1f}%")
    for progress_file in sorted(export_dir.glob("_progress*.json")):
        p = json.loads(progress_file.read_text())
        print(f"  [{progress_file.name}] {p.get('records_per_hour')} records/hour, "
              f"ETA {p.get('eta_hours')} h, last {p.get('last_id')} at {p.get('updated_at')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="one-time: visible browser, log in yourself")
    p_login.add_argument("--export-dir", default="quality_inspections_export")
    p_login.set_defaults(func=cmd_login)

    p_run = sub.add_parser("run", help="headless (default), unattended scraping run")
    p_run.add_argument("--export-dir", default="quality_inspections_export")
    p_run.add_argument("--limit", type=int, default=None, help="process at most N new records")
    p_run.add_argument("--re-enumerate", action="store_true", help="ignore cached id list")
    p_run.add_argument("--headed", action="store_true", help="show the browser window (debugging)")
    p_run.add_argument("--shard", help="K/N: process every Nth id starting at K (run N of these in parallel)")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="offline progress report")
    p_status.add_argument("--export-dir", default="quality_inspections_export")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
