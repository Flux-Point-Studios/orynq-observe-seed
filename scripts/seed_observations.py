#!/usr/bin/env python3
"""Re-anchor published AI capability observations to the Materios chain.

The script reads `sources/observations.json`, signs each entry with the FPS
Public Re-anchoring observer key, and submits it to the Materios blob
gateway. Submissions are sponsored — the gateway operator pays the chain
fee on the observer's behalf.

Idempotency: the canonical CBOR pre-image of an observation is fully
determined by the input record. Re-running the script with no input
changes produces the same content_hash. The gateway short-circuits
duplicates as `status: replay` (no second on-chain submission). Re-running
after editing an entry produces a new content_hash and a new chain anchor.

Output: a JSON manifest `manifest.json` next to the observations file
listing each entry's slug, taxonomy_id, source URL, content_hash,
materios_tx, and cardano_anchor_tx (when present). The companion
`observations.md` is a human-readable rendering of the same data.

Usage:
    seed_observations.py \\
        --wallet /home/deci/.config/orynq-observe/fps-reanchor.json \\
        --token-file /home/deci/.config/orynq-observe/preprod-token.txt \\
        --observations /home/deci/work/orynq-observe-seed/sources/observations.json \\
        --manifest /home/deci/work/orynq-observe-seed/manifest.json \\
        --network preprod \\
        --wait-for-anchor 120

Exit codes:
    0  — all entries submitted (or already on chain), manifest written.
    2  — gateway rejected one or more entries; partial manifest written.
    3  — fatal config error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from orynq_observe import Observation, ObserverKeypair
from orynq_observe.submit import GatewayError, SubmitError


def _load_observations(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"{path} must be a JSON array of observation specs")
    return data


def _load_token(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        token = f.read().strip()
    if not token:
        raise SystemExit(f"{path} contains no token text")
    return token


def _load_manifest(path: str) -> Dict[str, Any]:
    """Read prior manifest, or initialise a fresh one when absent."""
    if not os.path.isfile(path):
        return {
            "version": "1",
            "registry_url": "https://materios.fluxpointstudios.com/api/observations",
            "observer_ss58": None,
            "entries": [],
        }
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(path: str, manifest: Dict[str, Any]) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def _build_obs(spec: Dict[str, Any], observer_ss58: str) -> Observation:
    """Build the Observation from a spec entry."""
    obs = Observation(
        model_name=spec["model_name"],
        model_version=spec["model_version"],
        taxonomy_id=spec["taxonomy_id"],
        severity=spec["severity"],
        observer_context=spec["observer_context"],
        occurred_at=spec.get("occurred_at"),
    )
    obs.add_evidence(prompt=spec["prompt"], response=spec["response"])
    if spec.get("artifact_ref"):
        obs.add_artifact(spec["artifact_ref"])
    return obs


def _entry_for(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "slug": spec["slug"],
        "taxonomy_id": spec["taxonomy_id"],
        "severity": spec["severity"],
        "model_name": spec["model_name"],
        "model_version": spec["model_version"],
        "source_paper": spec["source_paper"],
        "source_url": spec["source_url"],
        "source_section": spec["source_section"],
        "artifact_ref": spec.get("artifact_ref"),
        "content_hash": None,
        "materios_tx": None,
        "cardano_anchor_tx": None,
        "status": "pending",
        "last_attempt_at": None,
    }


def _registry_detail(content_hash: str, gateway_url: str) -> Optional[Dict[str, Any]]:
    """Fetch the registry detail for a content_hash.

    The gateway's read-side registry endpoint at
    `/api/observations/{contentHash}` returns the pallet ReceiptRecord
    enriched with the manifest body. The `receiptId` is the pallet
    receipt id; we also fetch the chain anchor by walking the recent
    anchor-worker rolling-merkle entries.

    Returns None if the receipt isn't yet on chain (404).
    """
    import httpx

    base = gateway_url.rstrip("/")
    url = f"{base}/api/observations/{content_hash}"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(url)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            return None
        body = r.json()
        return body.get("observation") if isinstance(body, dict) else None
    except Exception:
        return None


def _refresh_until(
    receipt,
    *,
    gateway_url: str,
    api_key: str,
    deadline_seconds: float,
    poll_interval: float = 5.0,
) -> Dict[str, Any]:
    """Poll the registry for chain coordinates until done or deadline.

    Returns the registry observation row when one is observed; falls back
    to an empty dict on timeout. The materios_tx is the receiptId field
    (the on-chain receipt key, derived from contentHash via the pallet's
    deriveReceiptId rule). The cardano_anchor_tx is resolved separately
    via the anchor-worker's published anchor entries.
    """
    deadline = time.time() + deadline_seconds
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        row = _registry_detail(receipt.content_hash, gateway_url)
        if row is not None:
            last = row
            if row.get("receiptId"):
                # On-chain landed. Caller resolves cardano anchor separately.
                return row
        time.sleep(poll_interval)
    return last


def _gateway_url_for(network: str, override: Optional[str]) -> str:
    if override:
        return override
    if network == "preprod":
        return "https://materios.fluxpointstudios.com/preprod-blobs"
    if network == "mainnet":
        return "https://materios.fluxpointstudios.com/mainnet-blobs"
    raise SystemExit(f"unknown network {network!r}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Re-anchor published AI capability observations to Materios.",
    )
    p.add_argument("--wallet", required=True, help="path to observer JSON keyfile")
    p.add_argument(
        "--token-file",
        required=True,
        help="path to Bearer token (single line, matra_... prefix)",
    )
    p.add_argument(
        "--observations",
        required=True,
        help="path to observation spec JSON array",
    )
    p.add_argument(
        "--manifest",
        required=True,
        help="path to write/update the chain-coordinates manifest",
    )
    p.add_argument(
        "--network", default="preprod", choices=("preprod", "mainnet")
    )
    p.add_argument(
        "--gateway-url",
        default=None,
        help="override the default gateway URL for the network",
    )
    p.add_argument(
        "--wait-for-anchor",
        type=float,
        default=120.0,
        help="seconds to poll for materios_tx + cardano_anchor_tx per entry",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="optional cap on number of entries processed this run",
    )
    args = p.parse_args(argv)

    try:
        kp = ObserverKeypair.load(args.wallet)
    except Exception as e:
        print(f"error: could not load wallet {args.wallet}: {e}", file=sys.stderr)
        return 3
    token = _load_token(args.token_file)
    gateway_url = _gateway_url_for(args.network, args.gateway_url)

    specs = _load_observations(args.observations)
    if args.limit is not None:
        specs = specs[: args.limit]
    manifest = _load_manifest(args.manifest)
    manifest["observer_ss58"] = kp.ss58_address
    manifest["network"] = args.network
    manifest["gateway_url"] = gateway_url

    # Index existing entries by slug for in-place update.
    by_slug: Dict[str, Dict[str, Any]] = {
        e["slug"]: e for e in manifest.get("entries", [])
    }

    rejected = 0
    for spec in specs:
        slug = spec["slug"]
        entry = by_slug.get(slug)
        if entry is None:
            entry = _entry_for(spec)
            manifest.setdefault("entries", []).append(entry)
            by_slug[slug] = entry
        else:
            # Refresh top-level metadata in case the spec was edited.
            entry.update(
                {
                    "taxonomy_id": spec["taxonomy_id"],
                    "severity": spec["severity"],
                    "model_name": spec["model_name"],
                    "model_version": spec["model_version"],
                    "source_paper": spec["source_paper"],
                    "source_url": spec["source_url"],
                    "source_section": spec["source_section"],
                    "artifact_ref": spec.get("artifact_ref"),
                }
            )

        # Build the SDK observation + compute its canonical content_hash so we
        # can short-circuit on prior success even when the gateway is briefly
        # unreachable.
        obs = _build_obs(spec, kp.ss58_address)
        expected_hash = obs.content_hash(kp.ss58_address)
        prior_hash = entry.get("content_hash")
        prior_status = entry.get("status")

        if (
            prior_hash == expected_hash
            and prior_status == "submitted"
            and entry.get("materios_tx")
        ):
            # Already on chain with the exact same canonical bytes — skip.
            print(
                f"[skip] {slug}: already on chain content_hash={expected_hash} "
                f"materios_tx={entry['materios_tx']}"
            )
            continue

        print(
            f"[submit] {slug} taxonomy={spec['taxonomy_id']} severity={spec['severity']}"
        )
        entry["last_attempt_at"] = int(time.time())
        try:
            receipt = obs.submit(
                wallet=kp,
                network=args.network,
                gateway_url=args.gateway_url,
                api_key=token,
            )
        except GatewayError as e:
            print(
                f"  GatewayError status={e.status} message={e.message}",
                file=sys.stderr,
            )
            entry["status"] = f"rejected:gateway:{e.status}"
            entry["last_error"] = str(e.message)[:300]
            entry["content_hash"] = expected_hash
            rejected += 1
            _save_manifest(args.manifest, manifest)
            continue
        except SubmitError as e:
            print(f"  SubmitError {e}", file=sys.stderr)
            entry["status"] = "rejected:submit"
            entry["last_error"] = str(e)[:300]
            entry["content_hash"] = expected_hash
            rejected += 1
            _save_manifest(args.manifest, manifest)
            continue

        entry["content_hash"] = receipt.content_hash
        entry["gateway_status"] = receipt.gateway_status
        entry["status"] = "submitted"
        print(f"  accepted content_hash={receipt.content_hash}")
        _save_manifest(args.manifest, manifest)

        # Poll for materios_tx + cardano_anchor_tx.
        if args.wait_for_anchor > 0:
            _refresh_until(
                receipt,
                gateway_url=gateway_url,
                api_key=token,
                deadline_seconds=args.wait_for_anchor,
            )
        if receipt.materios_tx:
            entry["materios_tx"] = receipt.materios_tx
        if receipt.cardano_anchor_tx:
            entry["cardano_anchor_tx"] = receipt.cardano_anchor_tx
        print(
            f"  materios_tx={entry.get('materios_tx')} "
            f"cardano_anchor_tx={entry.get('cardano_anchor_tx')}"
        )
        _save_manifest(args.manifest, manifest)

    _save_manifest(args.manifest, manifest)
    if rejected:
        print(f"\n{rejected} entries rejected — see manifest for details", file=sys.stderr)
        return 2
    print(f"\nAll {len(specs)} entries processed. Manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
