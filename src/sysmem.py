"""
Small, dependency-free system-resource checks, shared by anything that runs concurrent work or
holds large amounts of data in memory (src/extract_raw.py's --workers, src/export_full_csv.py's
per-service loop).

Why this exists: the first full-scale run against the live tenant got OOM-killed by the kernel
at 7.45GB RSS (see export_full_csv.py's fix). The machine this pipeline runs on also runs a
normal desktop workload (browser, IDE, chat apps, antivirus) that competes for the same memory,
and that competition is real and observed - swap has been seen sitting at 90%+ full from those
apps alone, independent of anything this project does. A concurrency feature that ignores that
and always uses exactly what was asked for is how a laptop gets slowed to a crawl or crashed
again. These functions let callers ask "is it safe to use N workers / load more data right now"
and get a real, current answer instead of assuming.
"""

import logging
import os

logger = logging.getLogger("sap2odoo.sysmem")

# Kept in reserve for the rest of the system (other apps, the OS) - concurrency is scaled down
# rather than allowed to eat into this. Deliberately generous: this pipeline's job is not worth
# making the machine it runs on unusable.
MIN_HEADROOM_MB = 2048

# Rough memory cost of one concurrent extraction worker holding one entity set's rows resident
# at once - sized from the live tenant's worst single entity sets (100-700k rows), rounded up
# with margin. Not exact (row width varies a lot), just conservative enough to avoid a repeat.
MB_PER_WORKER = 700


def available_memory_mb():
    """
    Currently available memory in MB (the kernel's own "could be given to a new process without
    swapping" estimate - MemAvailable, not MemFree, which under-counts reclaimable cache).
    Returns None if this can't be determined (non-Linux, or /proc unavailable) - callers should
    treat that as "unknown, proceed as normal" rather than as zero.
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except (FileNotFoundError, ValueError, IndexError, OSError):
        return None
    return None


def safe_worker_count(requested, mb_per_worker=MB_PER_WORKER, min_headroom_mb=MIN_HEADROOM_MB):
    """
    How many concurrent workers to actually use, given what's currently available - never more
    than requested, but scaled down (down to 1, never 0) if memory is tight right now. Logs a
    warning when it has to reduce, so a quieter run isn't a silent one.
    """
    if requested <= 1:
        return requested
    available = available_memory_mb()
    if available is None:
        return requested  # can't check - don't block on it, just proceed as asked

    usable = available - min_headroom_mb
    affordable = max(1, int(usable // mb_per_worker)) if usable > 0 else 1
    safe = min(requested, affordable)
    if safe < requested:
        logger.warning(
            "Only %.0f MB available (keeping %d MB in reserve for the rest of the system) - "
            "reducing --workers %d to %d rather than risk another OOM kill",
            available, min_headroom_mb, requested, safe,
        )
    return safe


def low_memory(min_headroom_mb=MIN_HEADROOM_MB):
    """True if available memory has dropped below the safety margin right now."""
    available = available_memory_mb()
    return available is not None and available < min_headroom_mb
