# Fail-closed active learning V1

## Outcome

This control plane reduces the first review surface from 1,012 questions to 96
review packets: 64 information-gain items and 32 independently sampled audit
items. It learns proof-policy patterns, not answers. No learned policy can
materialize a value, certify a claim, promote a model, or release a submission.

The current frozen cycle is
`artifacts/research/active_learning_cycle_v3_20260824`; its manifest SHA-256 is
`dffe1e4b86fed72cb9b4b3f9689df5ce7cc80134ef3043bb563c5f9e2fe8735a`.
It is replay-verified, has zero adjudicated training records and zero promoted
policies, and remains release-blocked.

## Five planes

1. **Source** — immutable raw/V2/V3 assets and the V13 evidence-closure manifest.
2. **Proposal** — route, metric, period, cell, formula and operand candidates;
   every output is provisional and may abstain.
3. **Certification** — typed claim requirements, exact-cell evidence, Decimal
   replay and independent receipts. Model confidence cannot create `PASS`.
4. **Learning control** — versioned sampling, reviewer authority, append-only
   decisions, same-item proposer/critic consensus, adjudication and calibration.
5. **Release** — the existing full-corpus proof, production ledger and submission
   compiler gates. Active learning never bypasses them.

## What can learn

The learner may improve component proposals such as a formula family, a metric
binding rule, a temporal interpretation or a retrieval ranking. It cannot copy
a numeric answer, reuse an exact cell without revalidation, or turn similarity
into semantic proof. A learned policy is rerun against each question's frozen
requirements and mutation checks.

Prefer rule/fingerprint learning before fine-tuning: one adjudicated component
decision can test a whole cluster, while every affected question still receives
its own deterministic certificate. Fine-tuning is useful later for proposal and
retrieval only, after the label inventory and holdout gates are large enough.

## Two disjoint review lanes

The probability audit is frozen **before** active sampling. Its 32 questions are
drawn from all 1,012 with recorded inclusion probability `32/1012`. The active
lane then excludes those IDs and chooses 64 diverse/high-support cluster items.

- `active_learning`: optimizes information gain; it cannot estimate population
  precision because uncertainty/diversity sampling is biased.
- `independent_population_audit`: estimates risk from independently adjudicated
  `CORRECT`/`INCORRECT` labels. `ACCEPT_PROPOSAL` is not treated as correctness.

Later cycles may pin a prior audit ledger. Already-audited IDs are excluded,
the new inclusion probability is recorded, and duplicate IDs fail closed.

## Decision and promotion state machine

```text
PENDING packet
  -> ChatGPT proposal + independent critic on the same immutable packet
  -> agreement: MACHINE_PROVISIONAL policy candidate
  -> authorized adjudication: proposal-training record only
  -> independent probability audit + source-group holdout + risk bound
  -> ELIGIBLE_FOR_EXPLICIT_PROMOTION_REVIEW
  -> explicit promotion process (not implemented here)
```

Reviewer identities and scopes come from a pinned authority registry. A
decision must bind the immutable packet hash and resolve every source reference
to a hash already present in that packet. Reviewers cannot supply their own
issuer/document group. A single decision is never training-eligible; agent
agreement remains non-materializable and non-authorizing.

The statistical gate requires at least 125 independently adjudicated audit
labels and a one-sided Wilson 95% lower bound of at least 0.97, per supported
source group/stratum. Even 125 unanimous `ACCEPT_PROPOSAL` decisions do not
count unless an independent audit labels them correct. Unknown groups abstain.

## Human-minimization plan

1. Review the 64 active items as component policies, not full answers.
2. Run proposer and blind critic on the identical hash-bound packet.
3. Escalate disagreements, missing evidence and novel clusters to an authorized
   adjudicator; do not adjudicate easy agreements by default.
4. Keep the 32 probability-audit items blind and separate from training.
5. Rerun accepted rules over their clusters; mutation failures return only the
   affected items to review.
6. Accumulate versioned audit cycles until the risk gate is established.

This first cycle reduces the review-item surface by about 90.5%, but it does
not prove a 90.5% reduction in total human minutes or any release readiness.

## Build and verify

```bash
.venv/bin/python scripts/build_active_learning_cycle_v1.py \
  --config configs/active_learning_cycle_v1.json \
  --output-dir artifacts/research/active_learning_cycle_v3_20260824

.venv/bin/python scripts/verify_active_learning_cycle_v1.py \
  artifacts/research/active_learning_cycle_v3_20260824/active_learning_cycle.manifest.json

.venv/bin/python -m pytest -q tests/test_active_learning.py
```

`scripts/auto_review_bundle*.py` remain historical research entry points. New
learning cycles should use this single manifest-bound facade; the old scripts
must not feed training, promotion, release or submission.
