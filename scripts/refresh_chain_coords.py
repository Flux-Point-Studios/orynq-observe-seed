#!/usr/bin/env python3
"""Walk receipt-submitter and anchor-worker logs to enrich the manifest with
on-chain coordinates that were not captured at submission time.

This is a back-fill helper for the seed run. The seed script writes
content_hash + accepted-at on submission; this script binds each content
hash to its materios_tx + cardano_anchor_tx by parsing public service
logs.

Sources:
  - receipt-submitter container logs (for materios_tx + materios block)
  - blob-gateway /trace/api/lineage/{contentHash} (for cardano_anchor_tx,
    once the attestation quorum lands)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

import httpx

SUBMIT_RE = re.compile(
    r"200 submitted content=([0-9a-f]{64}) receipt_id=(0x[0-9a-f]{64}) tx=(0x[0-9a-f]{64}) block=(\d+)"
)


def _read_submitter_log(container: str, lines: int = 5000) -> List[str]:
    proc = subprocess.run(
        ["docker", "logs", container, "--tail", str(lines)],
        capture_output=True,
        text=True,
        check=False,
    )
    out = proc.stdout + proc.stderr
    return out.splitlines()


def _scan_for_materios_tx(
    lines: List[str], by_content: Dict[str, Dict[str, Any]],
) -> int:
    """Annotate each known content_hash with materios_tx + receipt_id + block.
    Returns the count of newly-bound entries.
    """
    bound = 0
    for line in lines:
        m = SUBMIT_RE.search(line)
        if not m:
            continue
        content_hash, receipt_id, tx_hash, block_str = m.groups()
        entry = by_content.get(content_hash)
        if entry is None:
            continue
        if entry.get("materios_tx"):
            continue
        entry["materios_tx"] = tx_hash
        entry["materios_receipt_id"] = receipt_id
        entry["materios_block"] = int(block_str)
        bound += 1
    return bound


def _trace_cardano_anchor(
    content_hash: str, gateway_url: str
) -> Optional[Dict[str, Any]]:
    """Fetch the trace lineage for a content_hash and return the anchor node
    when present. The blob-gateway populates the anchor node only after the
    cert-daemon attestation quorum lands and anchor-worker batches the
    receipt into a Cardano L1 metadata tx."""
    base = gateway_url.rstrip("/")
    url = f"{base}/trace/api/lineage/{content_hash}"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url)
        if r.status_code != 200:
            return None
        body = r.json()
    except Exception:
        return None
    nodes = body.get("nodes") if isinstance(body, dict) else None
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if isinstance(node, dict) and node.get("kind") == "anchor":
            return node
    return None


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument(
        "--submitter-container",
        default="materios-node-receipt-submitter-preprod-1",
    )
    p.add_argument(
        "--gateway-url",
        default="https://materios.fluxpointstudios.com/preprod-blobs",
    )
    p.add_argument(
        "--scan-lines",
        type=int,
        default=20000,
        help="how many recent submitter log lines to scan",
    )
    args = p.parse_args(argv)

    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    entries = manifest.get("entries", [])
    by_content: Dict[str, Dict[str, Any]] = {
        e["content_hash"]: e for e in entries if e.get("content_hash")
    }

    # Pass 1: scan submitter log for materios_tx
    log_lines = _read_submitter_log(args.submitter_container, args.scan_lines)
    bound = _scan_for_materios_tx(log_lines, by_content)
    print(f"materios_tx bound: {bound}/{len(by_content)}")

    # Pass 2: walk trace lineage per entry for cardano anchor
    anchor_bound = 0
    for ch, entry in by_content.items():
        if entry.get("cardano_anchor_tx"):
            continue
        node = _trace_cardano_anchor(ch, args.gateway_url)
        if node is None:
            continue
        hashes = node.get("hashes", {}) if isinstance(node, dict) else {}
        cardano_tx = hashes.get("cardanoTxHash") if isinstance(hashes, dict) else None
        if isinstance(cardano_tx, str):
            entry["cardano_anchor_tx"] = cardano_tx
            anchor_bound += 1
        meta = node.get("meta", {}) if isinstance(node, dict) else {}
        if isinstance(meta, dict):
            blk = meta.get("cardanoBlockHeight")
            if isinstance(blk, int):
                entry["cardano_block_height"] = blk
            net = meta.get("cardanoNetwork")
            if isinstance(net, str):
                entry["cardano_network"] = net
    print(f"cardano_anchor_tx bound: {anchor_bound}")

    # Mark fully-anchored entries
    for entry in entries:
        if entry.get("materios_tx") and entry.get("cardano_anchor_tx"):
            entry["status"] = "anchored"
        elif entry.get("materios_tx"):
            entry["status"] = "submitted-on-chain"

    with open(args.manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"manifest updated: {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
