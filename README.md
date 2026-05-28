# Re-anchored Public AI Observation Receipts

This repository re-anchors a small set of already-published AI model
capability findings as cryptographically-attested receipts on the Materios
preprod chain, with each receipt subsequently anchored to Cardano L1.

The point is not to publish novel claims. Every entry in
`sources/observations.json` cites a real public paper or report as its
`artifact_ref`. The script in `scripts/seed_observations.py` reads that
file, signs each entry with a dedicated `Flux Point Studios — Public
Re-anchoring` observer key, and submits it to the Materios blob gateway
via the orynq-observe SDK. The chain anchor binds (paper citation,
observer key, content hash) together in a way that survives the original
source URL going away or the paper getting reorganised.

## What you get

For each observation:

1. A canonical CBOR pre-image (RFC 8949 §4.2.1) of the
   `ai_capability_observation_v1` schema, deterministic across the Python
   and TypeScript SDKs.
2. A sha256 content hash of those bytes.
3. An sr25519 signature from the Flux Point Studios Public Re-anchoring
   observer key over the canonical pre-image.
4. A Materios extrinsic hash (`materios_tx`) recording the receipt on
   the partner chain via `submit_receipt_v2`.
5. A Cardano L1 transaction hash (`cardano_anchor_tx`) carrying the
   Materios checkpoint that proves the partner-chain block including the
   receipt.

Every coordinate is in the `manifest.json` and rendered in
`observations.md`.

## Sources

The current seed set draws from:

- Apollo Research, _Frontier Models are Capable of In-Context
  Scheming_, arXiv:2412.04984, December 2024.
- METR (Model Evaluation and Threat Research), August 2024 update on
  Autonomy Evaluation Resources.
- Anthropic, _Sleeper Agents: Training Deceptive LLMs that Persist
  Through Safety Training_, arXiv:2401.05566, January 2024.
- OpenAI, _Building an Early Warning System for LLM-Aided Biological
  Threat Creation_, January 2024.
- OWASP, _Top 10 for LLM Applications 2025_ (LLM01: Prompt Injection).
- Pan, Zhou, et al., _Frontier AI systems have surpassed the
  self-replicating red line_, arXiv:2412.12140, December 2024.

For each, the receipt's `observer.context` is a short attribution string
(≤ 280 chars) that names the paper and the section reference; the full
URL lives in `observation.artifactRef`. None of the receipt bytes contain
editorial commentary — the technical claim, the citation, the
attribution. That's it.

## Taxonomy

Receipts are tagged with capability IDs from
[`ai-capability-taxonomy`](https://github.com/Flux-Point-Studios/ai-capability-taxonomy).
The current set covers:

| Taxonomy ID | Source |
|---|---|
| DECEPTION-SYC-001 | Apollo Research, sycophancy result |
| EVAL-EVADE-001 | Apollo Research, sandbagging result |
| SHUTDOWN-EVADE-001 | Apollo Research, self-exfiltration result |
| AUTO-CRED-001 | METR ARA, credential acquisition |
| CODE-EXEC-001 | METR ARA, sandbox escape |
| NETWORK-RES-001 | METR ARA, external compute acquisition |
| PROMPT-BOOT-001 | Anthropic, backdoor persistence |
| PERSONA-DRIFT-001 | Anthropic, deceptive chain-of-thought |
| AUTO-BIO-001 | OpenAI, bio-uplift early-warning study |
| TOOL-MISUSE-001 | OWASP LLM01, tool-scope escalation |
| SOCIAL-ENG-001 | OWASP LLM01, autonomous social engineering |
| WEIGHT-REPL-001 | Pan & Zhou et al., self-replication red line |

## Reproducing this set

```bash
# 1. Install the SDK.
pip install orynq-observe

# 2. Generate your own observer keyfile.
orynq-observe keygen --out ./my-observer.json

# 3. Get a sponsored gateway token (preprod is free for researchers).
#    See https://materios.fluxpointstudios.com for current contact.

# 4. Run the seed.
python scripts/seed_observations.py \
    --wallet ./my-observer.json \
    --token-file ./preprod-token.txt \
    --observations sources/observations.json \
    --manifest manifest.json \
    --network preprod \
    --wait-for-anchor 120
```

The seed script is idempotent. The same observation spec produces the
same canonical pre-image and therefore the same content hash; the
gateway short-circuits duplicates as `status: replay` rather than
double-submitting. Edit an entry in `sources/observations.json` and the
content hash changes, and a new chain anchor is produced for the new
bytes.

## What the receipt does NOT prove

Re-anchoring a published claim on chain proves three things:

1. The exact byte string of the claim existed at the time of the
   on-chain timestamp.
2. The observer key (Flux Point Studios — Public Re-anchoring) bound
   that byte string to its own SS58 address with a non-repudiable
   signature.
3. The byte string includes a citation to a real public source URL.

It does NOT prove the source paper's claim is correct, replicable, or
that the observer independently reproduced it. Verifying the underlying
finding is the reader's job; the receipt is a fingerprint.

## Looking up a receipt

Each `content_hash` in the manifest is a sha256 hex string. To verify a
receipt:

1. Fetch the manifest from the registry:
   `GET https://materios.fluxpointstudios.com/api/observations/<contentHash>`
2. Re-derive the canonical CBOR bytes locally using the orynq-observe
   SDK or the published schema in `orynq-sdk/packages/anchors-materios`.
3. Hash them and confirm the sha256 matches `content_hash`.
4. Verify the observer signature against the canonical bytes using the
   observer's SS58 address (also recorded in the manifest).
5. Look up the Cardano L1 transaction (`cardano_anchor_tx`) on
   cardanoscan / Blockfrost preprod to confirm the partner-chain
   checkpoint actually landed.

## License

MIT. See `LICENSE`.
