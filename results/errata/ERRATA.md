# Dataset errata and provenance notes

**Errata version 1, issued 2026-09-13.**
Applies to the formal campaign archive `fam-formal-raw-b260ac4df1f524a5.tar.gz`, deposited as
[10.5281/zenodo.22727175](https://doi.org/10.5281/zenodo.22727175).

The campaign archive is **preserved byte for byte**. Its digest is unchanged
from the original deposit:

```text
archive   fam-formal-raw-b260ac4df1f524a5.tar.gz
sha256    c9ded3458c0cc4717be812e6b693561934fd1c69caa67565f6e8c4551b855d6d

aggregate 391712ab516049b19689f3b897c398096c8b8d05df358493b470d7b99838cc07
          over 433 files, defined in environment/raw-archive-inventory.json
```

The aggregate is taken over the inventory rather than over a container, so it
depends on what the files contain and what they are called and not on tar
format, compression or timestamps. It is reproducible from the archive alone —
entries sorted **by path**, not by the joined line, which would sort by the
digest that starts it; two spaces between the fields; lines joined with LF and
not terminated by one; hashed as UTF-8:

```text
python - <<'EOF'
import hashlib, json
inv = json.load(open('environment/raw-archive-inventory.json'))
rows = sorted(inv['files'], key=lambda e: e['path'])
# Two spaces; joined with LF, not terminated by one.
payload = '\n'.join(f"{e['sha256']}  {e['path']}" for e in rows)
print(hashlib.sha256(payload.encode('utf-8')).hexdigest())
EOF
```

The tarball holds 435 files: the 433 the
inventory covers, plus the two it excludes by construction.

The files of this errata layer were added to the Zenodo record **beside** the
archive, not inside it. Nothing in the archive was edited, replaced or removed.

A machine-readable form of everything below is in
[`provenance-authority.json`](provenance-authority.json).

## How these were found

Post-deposit self-review of the deposited artifacts. No result was challenged
by a third party and no number changed. The review was prompted by the first
item, which was noticed while reading a manifest by hand.

## Nothing numerical is affected

Not affected: raw observations, run validity, experiment outcomes, processed
numerical results, published figures, reported confidence intervals.

Two independent reproductions support this, and they are different claims:

- the published analysis, run from a clean clone of the implementation
  repository against a freshly extracted copy of this archive, on Python 3.14
  under Windows against the frozen Python 3.12 under Linux, reproduced all 21
  published quantities exactly — six latency percentiles, three latency ratios
  and two throughput ratios, each with its bootstrap interval;
- a reimplementation of the estimators written from the paper's prose, which
  does **not** import the project's analysis module, recomputed the six latency
  percentiles and four throughput medians directly from the raw JSON Lines and
  obtained the same values.

The second matters more: a bug in the authors' analysis code could not hide
behind itself.

## One cause produces items 1 to 3, and item 5

Descriptive strings and fields were written during the development phase, when
they were true, and were never made conditional on the publication flag the run
actually carries.

One earlier instance was found and corrected **before** deposit: the note in
the analysis summary. A second was corrected only in the half that stopped the
campaign — the E4 session validator had required `publication_data` to be false
and would have failed three valid formal sessions — while the strings that
validator writes were left as they were. That half-fix is item 5.

Stating the cause is the point: the defects are not independent, and the class
is bounded to descriptive metadata that no tool reads.

---

## 1. Manifests that state the opposite of their own status

`123` formal manifests carry a `scope_note` asserting that the run
is development data and that `publication_data` is false.

**This is false.** Each of these manifests carries `publication_data: true`, was
executed under the protocol lock, and is formal evidence.

Affected: E3 — 120 manifests, E4 — 3 manifests.
Manifests for E0, E1, E2 carry no `scope_note` field at all and are
unaffected.

The exact strings, so they can be matched:

```text
E3:  Development E3 run. publication_data is false; these numbers are implementation validation and are not publication evidence.
E4:  Development validation. publication_data is false; C4 is not marked collected and no evidence counter is updated.
```

**The authoritative field is `publication_data`.** Every gate in the pipeline
branches on it: the pre-run lock enforcement, the collection freeze, the
integrity audit and the analysis. `scope_note` is written and never read —
`grep -rn scope_note src/ scripts/` in the implementation repository returns
writes only.

The manifests are **intentionally left unchanged**. Editing archived evidence to
correct a descriptive field would make the deposited archive something other
than what the campaign produced, which costs more than the defect does.

## 2. Which processed summary is authoritative

The archive contains 5 analysis summaries. Only one is
authoritative:

```text
AUTHORITATIVE  processed/experiment-summary-20260907T084415Z.json
               sha256 ba988e3287cd04507c6f6edd67df7babdab249609acacac2eef15635d5acf834
```

This is the copy committed to `results/processed/` in the implementation
repository, and the one the reported figures and tables derive from.

```text
non-authoritative  processed/experiment-summary-20260906T193827Z.json
                   Development validation. publication_data is false; this is not publica…
non-authoritative  processed/experiment-summary-20260906T193934Z.json
                   Development validation. publication_data is false; this is not publica…
non-authoritative  processed/experiment-summary-20260906T194040Z.json
                   Development validation. publication_data is false; this is not publica…
```

Those three were produced while the provenance fields were being completed and
carry development-era labels.

One further file is **not** a defect:

```text
reproducibility rerun  processed/experiment-summary-20260907T085121Z.json
                       sha256 c48822fb5d629e505371a4983b36696030baf9a908b39ba3555354b26f92ceb1
```

It is an independent rerun of the same analysis over the same archive. It
differs from the authoritative summary in `generated_at` and in no analytical
field, and exists because the reproduction was checked rather than asserted.

## 3. Container image not observable from inside the runner

In 120 E3 manifests, `host_diagnostics.synapse_image` reads `unset`.

This records unavailability at that observation point, not an unknown
experimental configuration: the runner executes inside a container that cannot
query Docker, and 120 of those same manifests record
`host_diagnostics.docker_cli_present: false` in the same object.

The pinned images and their digests are recorded authoritatively in
`environment/environment-latest.json`, `environment/image-digests.json` and
`environment/protocol-lock.json`, all present in this archive.

## 4. Which revision produced each derived artifact

Seven derived artifacts record their `analysis_code_commit` as a label naming a
working tree — `task-02-working-tree` and siblings — rather than a commit.
Nobody can check out a working tree, so §54 is satisfied in form and not in
substance for these files.

```text
processed/e1-20260906T160546Z-01.federation-comparison.json
   sha256 8e49e03f76c20eea0b9daf9eebbdd29d139f4558d1a20599cbd0b12bffe98c94
processed/e1-20260906T160546Z-02.federation-comparison.json
   sha256 fb5edf47006b8297437d6d16bfa081d51dcbbbbc024bb360189c4d7bf004bd8b
processed/e1-20260906T160546Z-03.federation-comparison.json
   sha256 7f60b56b6c13c35a9a87c38dbf86ee93fe4ba1ea5a7af76d5962c7f89920def9
processed/e2-20260906T160654Z-01.recovery-comparison.json
   sha256 8d7b704fa3ed14d702891bf512c07e1a2aaf45c1cebecf68da7f235d1521d266
processed/e2-20260906T160654Z-02.recovery-comparison.json
   sha256 7a4d82a3bb089a352718ae68719afe0efe469ac854c9117ee5e4f3cd4ff7ab83
processed/e2-20260906T160654Z-03.recovery-comparison.json
   sha256 e608217a2a9d63ec612cc0a3fd2227207ea72f0cdeb6fff9e59c1111717bbff8
processed/e4-validation-latest.json
   sha256 2a73fa71ab598dd556b53dfeecd03e0d97bae9af4dbd1ffc296e07e734fc7aac
```

**The revision behind those labels is not recoverable, and this erratum does not
guess at it.** What is recoverable is the result.

`protocol_git_commit` reads `cd3f3c2aaae54be74728bb242f6bd7d606768ef8` in all seven — the
tagged commit the campaign executed — and identifies the code that produced the
raw data. Only the revision of
the code that derived from it is missing.

Those digests are listed because the run manifests reference these files **by
path without a digest**, unlike the raw streams, which carry one. The files are
covered by the archive aggregate digest, so tampering after deposit is
detectable; within the manifest chain they are named and not digested.

### Regeneration

`reproduced/` holds derived content rebuilt from the archived raw streams by
commit `6bd35ad5423d`.

**E2 — regenerated and matched.** Three recovery comparisons, 22
fields each, no mismatch. Derived independently from the raw runner and agent
streams rather than by re-running the experiment's own code, so the match is
not merely a demonstration that the original implementation is deterministic.

**E4 — re-run and matched.** The project's own validator, over the archived
manifests and transcripts, produces the archived verdict. This is weaker than
the E2 entries and is labelled as such in the layer: it shows the archived
inputs still yield the archived result, not that the result follows from an
independent reading of them.

**E1 — not regenerable.** The federation comparison rests on what each
homeserver returned when queried live during the run. Those domain views were
never written to a raw stream, and both homeservers were destroyed with the
formal host. **The archived comparison artifacts are the only copy of that
observation**, which is why their digests are pinned above.

That is a limitation of the evidence model, not of this erratum: E1 supplies the
structural half of C4 and all of C5, and its primary observation exists only as
a derived artifact.

## 5. The E4 summary disclaims a result the publication reports

`processed/e4-validation-latest.json` is the only file in the archive that
summarises E4. It carries `publication_data: false` and this note:

```text
Development validation. C4 is not marked collected and no evidence counter is updated (Task 06 §33).
```

**Both are false of the sessions it describes.** All 3 E4 run manifests carry
`publication_data: true`, and this same file records the verdict
`3/3 PASS` over 3 sessions — which it could not have done
under the development-era rule that demanded the flag be false.

That is the shape of the defect: the validator's gating was corrected before
the formal sessions ran, its descriptive fields were not. The file therefore
passes the sessions and disowns them in the same breath.

```text
processed/e4-validation-latest.json
   sha256 2a73fa71ab598dd556b53dfeecd03e0d97bae9af4dbd1ffc296e07e734fc7aac
```

E4 supplies the human component of C4, so this is the file a reader is most
likely to open first and the one most likely to mislead. The session manifests,
the archived transcripts and the revalidation under `reproduced/e4` all agree
with each other and against it.

## 6. A drift note that contradicts the numbers beside it

In `processed/experiment-summary-20260907T084415Z.json`,
the field `e3_summary.environment_drift.note` states:

```text
runs get FASTER as the campaign proceeds
```

**The runs get slightly slower, not faster.** From the same object:

```text
federated   first half  184.413 ms   second half  185.755 ms   +0.73%   pearson +0.7670
local       first half  103.579 ms   second half  104.567 ms   +0.95%   pearson +0.3859
```

The note uses that direction as an argument: it reports execution position
rather than accumulated rooms as the primary axis on the grounds that a
settling effect explains a speed-up and room accumulation does not. With the
real direction the argument does not hold, and it is **withdrawn rather than
restated with the sign reversed** — a slowdown as rooms accumulate is
consistent with room accumulation, which makes the two candidates harder to
separate, not easier. What stands is the rest of the note: position and
accumulated rooms both increase monotonically across a campaign, so they are
perfectly confounded and neither correlation can separate them.

Nothing numerical is affected. `p50_by_campaign_half`, the correlation
coefficients and the per-run records are correct, and they are what the
published limitation cites: roughly one percent of drift across the campaign,
against which the paired comparison is protected by counterbalancing, both
topologies of a block executing adjacently. No reported result depends on which
mechanism produces the drift.

## 7. Lesser items

`e3_summary.campaign_inventory.*.runtime_code_revision` is `null`. It is
initialised and never assigned, because run manifests do not carry the field.
The formal runtime revision is
`task-07-r4`, recorded in the protocol lock and in the
campaign ledger.

2 earlier environment snapshots carry `unset` image fields. They
are retained as provenance history and no formal run references them: all 132
formal manifests name `environment/environment-latest.json`.

`e3-campaign.log` is the campaign's console log, kept for provenance. It lies
outside the frozen directory layout and no analysis reads it. It was scanned
for credentials with the rest of the collection.

## 8. What remains true of the archive

The integrity audit over this archive passes 23 of 23 checks: no success beyond
the frozen timeout, no timeout carrying a completion timestamp, no negative
round-trip time, no clock reversal, concurrency never above the frozen bound, no
request appearing twice in one phase, no request completing twice across 82,939
completions, no success without a response event id, duplicate-ACK evidence
internally consistent, every measured body exactly 256 bytes, every measurement
window exactly 60.000 s, the executed schedule matching the lock run for run, and
no credential anywhere in 431 scanned files.
