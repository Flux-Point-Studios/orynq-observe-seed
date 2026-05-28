#!/usr/bin/env python3
"""Render manifest.json into the markdown table in observations.md.

The marker comments `<!-- BEGIN AUTOGEN -->` and `<!-- END AUTOGEN -->`
in observations.md bracket the auto-generated section. Hand-written prose
outside those markers is preserved.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BEGIN = "<!-- BEGIN AUTOGEN -->"
END = "<!-- END AUTOGEN -->"


def _shorten(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _row(entry: dict) -> str:
    mtx = entry.get("materios_tx") or ""
    ctx = entry.get("cardano_anchor_tx") or ""
    content_hash = entry.get("content_hash") or ""
    cardano_link = (
        f"[`{_shorten(ctx, 16)}`](https://preprod.cardanoscan.io/transaction/{ctx})"
        if ctx
        else "_pending_"
    )
    materios_short = f"`{_shorten(mtx, 18)}`" if mtx else "_pending_"
    content_short = f"`{_shorten(content_hash, 18)}`" if content_hash else "_n/a_"
    source = entry.get("source_paper") or ""
    section = entry.get("source_section") or ""
    url = entry.get("source_url") or ""
    return "| {slug} | `{tax}` | {severity} | [{src}]({url}) — {section} | {ch} | {mtx} | {ctx} |".format(
        slug=entry.get("slug", ""),
        tax=entry.get("taxonomy_id", ""),
        severity=entry.get("severity", ""),
        src=_shorten(source, 60),
        url=url,
        section=_shorten(section, 50),
        ch=content_short,
        mtx=materios_short,
        ctx=cardano_link,
    )


def _table(manifest: dict) -> str:
    head = (
        "| Slug | Taxonomy | Severity | Source | Content hash | Materios tx | Cardano anchor tx |\n"
        "| --- | --- | --- | --- | --- | --- | --- |"
    )
    rows = [_row(e) for e in manifest.get("entries", [])]
    return head + "\n" + "\n".join(rows)


def _replace_section(text: str, body: str) -> str:
    start = text.find(BEGIN)
    end = text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(
            "observations.md is missing BEGIN/END AUTOGEN markers — restore them first"
        )
    prefix = text[: start + len(BEGIN)]
    suffix = text[end:]
    return prefix + "\n" + body + "\n" + suffix


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    with open(args.manifest, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    summary_lines = [
        f"**Observer SS58:** `{manifest.get('observer_ss58', '_unset_')}`",
        f"**Network:** {manifest.get('network', 'preprod')}",
        f"**Gateway:** {manifest.get('gateway_url', '_unset_')}",
        f"**Entries:** {len(manifest.get('entries', []))}",
        "",
    ]
    body = "\n".join(summary_lines) + "\n" + _table(manifest)

    with open(args.out, "r", encoding="utf-8") as f:
        text = f.read()
    new_text = _replace_section(text, body)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"rendered manifest into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
