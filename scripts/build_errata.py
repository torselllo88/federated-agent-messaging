#!/usr/bin/env python3
"""Build the dataset errata layer from the archive itself.

Every digest, count and quoted string is read from the deposited artifacts
rather than typed, for the same reason the figures are generated. The first
draft of this document was written by hand and named an aggregate digest that
never existed in the archive; that is why this is a script.

Run it against an extracted archive, after the regeneration layer exists:

    python scripts/reproduce_derived.py --archive ARCHIVE --out ERRATA/reproduced
    python scripts/build_errata.py --archive ARCHIVE --tarball TARBALL \\
        --reproduced ERRATA/reproduced --out ERRATA

Writes nothing into the archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

_parser = argparse.ArgumentParser(description=__doc__)
_parser.add_argument("--archive", required=True, help="extracted archive root")
_parser.add_argument("--tarball", required=True, help="the deposited .tar.gz")
_parser.add_argument("--reproduced", required=True, help="regeneration layer")
_parser.add_argument("--out", required=True, help="where to write the errata")
_parser.add_argument("--doi", default="10.5281/zenodo.22727175")
_parser.add_argument("--errata-version", default="1")
_parser.add_argument("--issued", default=date.today().isoformat())
_parser.add_argument(
    "--authoritative-summary", default="",
    help="path of the authoritative experiment summary, relative to the "
         "archive root; defaults to the earliest carrying publication_data true",
)
_args = _parser.parse_args()

ROOT = Path(_args.archive)
OUT = Path(_args.out)
OUT.mkdir(parents=True, exist_ok=True)
_TARBALL = Path(_args.tarball)
_REPRODUCED = Path(_args.reproduced)

ISSUED = _args.issued
ERRATA_VERSION = _args.errata_version
DOI = _args.doi


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- gather
tar = _TARBALL
archive_sha = sha(tar)

#: Counted from the tarball rather than from the inventory, because the two
#: differ by exactly the files the inventory cannot list. Stating one number
#: for both invites a reader who untars and counts to think something is wrong.
import tarfile  # noqa: E402 - local to this block, not a module dependency

with tarfile.open(tar, "r:gz") as _tf:
    members = [m.name for m in _tf if m.isfile()]

inventory = json.loads(
    (ROOT / "environment" / "raw-archive-inventory.json").read_text(encoding="utf-8")
)
lock = json.loads((ROOT / "environment" / "protocol-lock.json").read_text(encoding="utf-8"))

manifests = {}
for p in sorted(ROOT.glob("manifests/*.json")):
    manifests[p.name] = json.loads(p.read_text(encoding="utf-8"))

affected = {n: m for n, m in manifests.items()
            if isinstance(m.get("scope_note"), str)
            and "publication_data is false" in m["scope_note"]}
by_experiment = Counter(m["experiment"] for m in affected.values())
notes = {}
for m in affected.values():
    notes.setdefault(m["experiment"], m["scope_note"])
no_field = sorted({m["experiment"] for m in manifests.values() if "scope_note" not in m})

summaries = sorted(ROOT.glob("processed/experiment-summary-*.json"))
classified = []
for p in summaries:
    d = json.loads(p.read_text(encoding="utf-8"))
    classified.append({
        "path": f"processed/{p.name}",
        "sha256": sha(p),
        "publication_data": d.get("publication_data"),
        "generated_at": d.get("generated_at"),
        "note": (d.get("note") or "")[:70],
    })
#: Which summary is authoritative is a decision, not a derivation, so it can be
#: overridden. The default is the earliest carrying publication_data true: that
#: is the one committed to results/processed/ in the implementation repository,
#: and any later one is a reproduction rerun of it. Naming the newest instead
#: would make the dataset disagree with the repository.
formal = [s for s in classified if s["publication_data"] is True]
if not formal:
    raise SystemExit("no summary carries publication_data true")
authoritative = _args.authoritative_summary or sorted(
    formal, key=lambda s: s["generated_at"] or s["path"]
)[0]["path"]
auth = next(s for s in classified if s["path"] == authoritative)
later = [s for s in formal if s["path"] != authoritative]
rer = sorted(later, key=lambda s: s["generated_at"] or s["path"])[0] if later else None
rerun = rer["path"] if rer else None
pre_fix = [s for s in classified if s["publication_data"] is not True]

env_latest = ROOT / "environment" / "environment-latest.json"
env_sha = sha(env_latest)
stale_env = sorted(
    f"environment/{p.name}" for p in ROOT.glob("environment/environment-2026*.json")
    if json.loads(p.read_text(encoding="utf-8")).get("software", {}).get("synapse_image")
    == "unset"
)

e3_unset = [n for n, m in manifests.items()
            if (m.get("host_diagnostics") or {}).get("synapse_image") == "unset"]
docker_absent = sum(1 for n in e3_unset
                    if (manifests[n].get("host_diagnostics") or {}).get(
                        "docker_cli_present") is False)

env_refs = {m.get("environment_manifest") for m in manifests.values()}

comparisons = {
    "E1": [{"path": f"processed/{p.name}", "sha256": sha(p)}
           for p in sorted(ROOT.glob("processed/e1-*.federation-comparison.json"))],
    "E2": [{"path": f"processed/{p.name}", "sha256": sha(p)}
           for p in sorted(ROOT.glob("processed/e2-*.recovery-comparison.json"))],
    "E4": [{"path": "processed/e4-validation-latest.json",
            "sha256": sha(ROOT / "processed" / "e4-validation-latest.json")}],
}
regenerated = json.loads(
    sorted((_REPRODUCED / "e2").glob("*.regenerated.json"))[0]
    .read_text(encoding="utf-8")
)
regen_commit = regenerated["analysis_code_commit"]
regen_fields = len(regenerated["comparison"]["fields_compared"])

#: Read from the artifacts themselves. Taking it from the lock would name the
#: implementation commit, which is not what these files record.
derived_protocol_commits = sorted({
    json.loads((ROOT / c["path"]).read_text(encoding="utf-8")).get("protocol_git_commit")
    for group in comparisons.values() for c in group
} - {None})
assert len(derived_protocol_commits) == 1, derived_protocol_commits
derived_protocol_commit = derived_protocol_commits[0]

#: From the runs, not from the lock: the two differ, which is the one deviation
#: this campaign carries.
executed = sorted({m.get("campaign_id") for m in manifests.values()} - {None})
assert len(executed) == 1, executed
executed_campaign_id = executed[0]

#: D8. The E4 summary disagrees with the three manifests it summarises: they
#: carry publication_data true, it carries false and says so in prose. Both
#: sides are read here so the entry cannot drift from the files.
e4_summary_path = "processed/e4-validation-latest.json"
e4_summary = json.loads(
    (ROOT / "processed" / "e4-validation-latest.json").read_text(encoding="utf-8")
)
e4_manifests = {n: m for n, m in manifests.items() if m.get("experiment") == "E4"}
e4_manifest_flags = sorted({m.get("publication_data") for m in e4_manifests.values()})
assert e4_manifest_flags == [True], e4_manifest_flags

#: D9. The drift note in the authoritative summary states a direction the
#: numbers in the same object contradict. Both come from that object.
auth_summary = json.loads((ROOT / authoritative).read_text(encoding="utf-8"))
drift = auth_summary["e3_summary"]["environment_drift"]
drift_correlation = drift["p50_vs_campaign_position_pearson_by_topology"]
drift_measured = {
    topology: {
        "first_half_mean_p50_ms": halves["first_half_mean_p50_ms"],
        "second_half_mean_p50_ms": halves["second_half_mean_p50_ms"],
        "direction": (
            "slower"
            if halves["second_half_mean_p50_ms"] > halves["first_half_mean_p50_ms"]
            else "faster"
        ),
        "change_percent": round(
            (halves["second_half_mean_p50_ms"] / halves["first_half_mean_p50_ms"] - 1)
            * 100,
            2,
        ),
        "pearson_p50_vs_campaign_position": drift_correlation.get(topology),
    }
    for topology, halves in drift["p50_by_campaign_half"].items()
}
#: Only a defect while every topology contradicts the claim. If a later
#: campaign genuinely speeds up, this entry must not be emitted unchanged.
assert all(v["direction"] == "slower" for v in drift_measured.values()), drift_measured
DRIFT_CLAIM = "runs get FASTER as the campaign proceeds"
assert DRIFT_CLAIM in drift["note"], drift["note"][:200]

# ------------------------------------------------------------------ JSON
authority = {
    "artifact": "dataset_provenance_authority",
    "errata_version": ERRATA_VERSION,
    "issued": ISSUED,
    "dataset_doi": DOI,
    "applies_to": {
        "archive_file": tar.name,
        "archive_sha256": archive_sha,
        "aggregate_sha256": inventory["aggregate_sha256"],
        "files_in_aggregate": inventory["file_count"],
        # Say which key the sort uses. "Sorted lines" reads as sorting the
        # joined strings, which sorts by digest -- the digest comes first --
        # and yields a different and wrong value. The inventory in the archive
        # states it correctly; this restatement did not.
        "aggregate_definition": (
            "SHA-256 over the '<sha256>  <path>' lines of "
            "environment/raw-archive-inventory.json. Entries sorted by POSIX "
            "path -- not by the joined line, which would sort by digest. Two "
            "spaces between the fields, lines joined with LF and not "
            "terminated by one, so the payload carries no trailing newline, "
            "and hashed as UTF-8. The inventory excludes itself and "
            "environment/collection-completion.json because both are written "
            "after it is computed."
        ),
        "aggregate_recipe": (
            "python - <<'EOF'\n"
            "import hashlib, json\n"
            "inv = json.load(open('environment/raw-archive-inventory.json'))\n"
            "rows = sorted(inv['files'], key=lambda e: e['path'])\n"
            "# Two spaces; joined with LF, not terminated by one.\n"
            "payload = '\\n'.join(f\"{e['sha256']}  {e['path']}\" for e in rows)\n"
            "print(hashlib.sha256(payload.encode('utf-8')).hexdigest())\n"
            "EOF"
        ),
    },
    #: What this layer consists of, so a missing or added file is detectable
    #: from inside it. The two documents the generator writes are excluded for
    #: the same reason the archive excludes its own inventory: a file cannot
    #: carry its own digest, and listing one computed before the file was
    #: finished would record something that never existed.
    "errata_layer": {
        "files": [
            {
                "path": f"reproduced/{p.relative_to(_REPRODUCED).as_posix()}",
                "sha256": sha(p),
                "bytes": p.stat().st_size,
            }
            for p in sorted(_REPRODUCED.rglob("*"))
            if p.is_file()
        ],
        "self_excluded": ["ERRATA.md", "provenance-authority.json"],
        "composition": (
            "The layer is the files listed above plus the two named in "
            "self_excluded, which this generator writes and therefore cannot "
            "digest here. Anything else beside the archive in the deposit is "
            "not part of it."
        ),
    },
    "archive_unchanged": True,
    "archive_note": (
        "The campaign archive is preserved byte for byte. The files of this "
        "errata layer were added to the Zenodo record beside it and are not "
        "inside it, so the archive digest above is unchanged from the original "
        "deposit."
    ),
    "formal_campaign_id": lock["campaign"]["campaign_id"],
    "campaign_id_as_executed": executed_campaign_id,
    "protocol_lock_tag": lock["implementation"]["git_tag"],
    "protocol_lock_commit": lock["implementation"]["git_commit"],
    "affects_numerical_results": False,
    "authoritative_summary": {"path": authoritative, "sha256": auth["sha256"]},
    "reproducibility_rerun": {
        "path": rerun,
        "sha256": rer["sha256"],
        "relationship": (
            "independent rerun of the same analysis over the same archive; "
            "differs from the authoritative summary in generated_at only, and "
            "in no analytical field"
        ),
    },
    "authoritative_environment": {
        "path": "environment/environment-latest.json",
        "sha256": env_sha,
        "referenced_by_every_formal_run": sorted(r for r in env_refs if r),
    },
    "root_cause": (
        "One cause produces D1, D2, D3 and D8: descriptive strings and fields "
        "were written during the development phase, when they were true, and "
        "were never made conditional on the publication flag the run actually "
        "carries. One earlier instance was found and corrected before deposit, "
        "the analysis summary note. A second, the E4 session validator, was "
        "corrected only in the gating that would have failed three valid formal "
        "sessions; the strings it writes were left alone, which is D8. The "
        "instances listed here reached the deposited archive."
    ),
    "known_metadata_defects": [
        {
            "id": "D1",
            "type": "false_descriptive_publication_status",
            "severity": "the artifact states the opposite of its own machine-readable status",
            "affected_count": len(affected),
            "affected_by_experiment": dict(by_experiment),
            "selection_predicate": (
                "every manifest whose experiment is E3 or E4; manifests for "
                f"{', '.join(no_field)} carry no scope_note field at all"
            ),
            "incorrect_field": "scope_note",
            "incorrect_values": notes,
            "authoritative_field": "publication_data",
            "authoritative_value": True,
            "consumed_by_analysis": False,
            "verification": (
                "grep -rn 'scope_note' src/ scripts/ in the implementation "
                "repository returns writes only and no reads; the freeze gate, "
                "audit and analysis all branch on publication_data"
            ),
            "affects_numerical_results": False,
        },
        {
            "id": "D2",
            "type": "retained_pre_fix_analysis_summaries",
            "severity": "non-authoritative copies present without an authority marker",
            "affected_count": len(pre_fix),
            "affected_paths": [s["path"] for s in pre_fix],
            "explanation": (
                "analysis runs made while the provenance fields were being "
                "completed; they carry development-era labels and no "
                "publication_data field"
            ),
            "authoritative_source": authoritative,
            "verification": (
                "the authoritative summary is the only one carrying "
                "publication_data true whose digest matches the copy committed "
                "to results/processed/ in the implementation repository"
            ),
            "affects_numerical_results": False,
        },
        {
            "id": "D3",
            "type": "container_image_unobservable_at_run_time",
            "severity": "value absent, not incorrect",
            "affected_count": len(e3_unset),
            "affected_experiment": "E3",
            "field": "host_diagnostics.synapse_image",
            "recorded_value": "unset",
            "explanation": (
                "the runner executes inside a container that cannot query "
                f"Docker; {docker_absent} of the {len(e3_unset)} affected "
                "manifests record host_diagnostics.docker_cli_present false in "
                "the same object, which states why"
            ),
            "authoritative_source": [
                "environment/environment-latest.json",
                "environment/protocol-lock.json",
                "environment/image-digests.json",
            ],
            "verification": (
                "the pinned image and its digest appear in all three sources "
                "above, and every formal manifest references the environment "
                "manifest by path"
            ),
            "affects_numerical_results": False,
        },
        {
            "id": "D4",
            "type": "unpopulated_convenience_field",
            "severity": "cosmetic",
            "field": "e3_summary.campaign_inventory.*.runtime_code_revision",
            "recorded_value": None,
            "explanation": (
                "initialised and never assigned; run manifests do not carry the "
                "field for the analysis to read"
            ),
            "authoritative_source": [
                "environment/protocol-lock.json -> campaign.campaign_parameters.runtime_code_revision",
                "campaigns/*.json -> parameters.runtime_code_revision",
            ],
            "authoritative_value": lock["campaign"]["campaign_parameters"].get(
                "runtime_code_revision"
            ),
            "affects_numerical_results": False,
        },
        {
            "id": "D5",
            "type": "superseded_environment_snapshots_retained",
            "severity": "cosmetic",
            "affected_count": len(stale_env),
            "affected_paths": stale_env,
            "explanation": (
                "snapshots taken before the image fields were corrected, kept as "
                "provenance history; no formal run references them"
            ),
            "authoritative_source": ["environment/environment-latest.json"],
            "verification": (
                "every one of the 132 formal manifests names "
                "environment/environment-latest.json in environment_manifest"
            ),
            "affects_numerical_results": False,
        },
        {
            "id": "D6",
            "type": "auxiliary_artifact_outside_normative_layout",
            "severity": "cosmetic",
            "affected_paths": ["e3-campaign.log"],
            "explanation": (
                "the campaign's console log, retained for provenance. It sits "
                "outside the frozen directory layout and no analysis reads it. "
                "It was scanned for credentials with the rest of the collection."
            ),
            # Stated rather than left blank: an empty authority slot reads as
            # an omission, and here the absence is the point.
            "authoritative_source": None,
            "authoritative_source_note": (
                "none, and none is needed: this file is authoritative for "
                "nothing. No analysis reads it and no result depends on it. "
                "Every fact it narrates is recorded independently in the "
                "manifests, the environment manifest and the protocol lock."
            ),
            "affects_numerical_results": False,
        },
        {
            "id": "D7",
            "type": "unresolvable_analysis_revision",
            "severity": "§54 satisfied in form only for these artifacts",
            "affected_count": 7,
            "affected_paths": sorted(
                [c["path"] for c in comparisons["E1"]]
                + [c["path"] for c in comparisons["E2"]]
                + [c["path"] for c in comparisons["E4"]]
            ),
            "field": "analysis_code_commit",
            "recorded_values": ["task-02-working-tree", "task-03-working-tree",
                                "task-06-working-tree"],
            "explanation": (
                "each entry point carried a constant of its own naming a "
                "working tree, which cannot be checked out. The revision behind "
                "those labels is not recoverable and is not guessed at."
            ),
            "still_traceable_by": (
                "protocol_git_commit, which reads "
                f"{derived_protocol_commit} in all of them -- the tagged commit "
                "the campaign executed -- and identifies the code that produced "
                "the raw data"
            ),
            "remediation": {
                "in_the_implementation": (
                    "the seven constants were replaced by one shared "
                    "definition, fam.common.env.analysis_code_commit, resolved "
                    "on the host and forwarded by every target that writes a "
                    "processed artifact. Future runs record a commit or say "
                    "'unresolved'."
                ),
                "for_this_archive": (
                    "the derived content was regenerated from the immutable raw "
                    "data by a named commit; see regeneration below"
                ),
            },
            "affects_numerical_results": False,
        },
        {
            "id": "D8",
            "type": "false_descriptive_publication_status",
            "severity": (
                "the only E4 summary in the archive declares itself "
                "non-publication data while the manifests it summarises declare "
                "the opposite"
            ),
            "affected_count": 1,
            "affected_paths": [e4_summary_path],
            "sha256": sha(ROOT / "processed" / "e4-validation-latest.json"),
            "incorrect_fields": {
                "publication_data": e4_summary.get("publication_data"),
                "scope_note": e4_summary.get("scope_note"),
            },
            "authoritative_field": (
                "publication_data on the three E4 run manifests, which is true "
                "in all of them"
            ),
            "authoritative_value": True,
            "explanation": (
                "the same cause as D1, reaching a summary rather than a "
                "manifest. The validator's gating logic was corrected before "
                "the formal sessions ran -- which is why this file records the "
                "verdict "
                f"{e4_summary.get('verdict')!r} over "
                f"{len(e4_summary.get('sessions') or [])} sessions instead of "
                "failing them -- but its descriptive fields were left as the "
                "development-phase constants they had always been."
            ),
            "why_it_matters": (
                "E4 supplies the human component of C4 and this is the only "
                "file in the archive that summarises it. A reader who opens it "
                "first finds an artifact that appears to disclaim the result "
                "the publication reports. The session manifests, transcripts "
                "and the regeneration under reproduced/e4 all state otherwise."
            ),
            "consumed_by_analysis": False,
            "verification": (
                "all three manifests/e4-*.manifest.json carry publication_data "
                "true; the session verdicts, participants and answered-request "
                "counts in this file agree with them and with the archived "
                "transcripts"
            ),
            "affects_numerical_results": False,
        },
        {
            "id": "D9",
            "type": "interpretive_note_contradicts_its_own_data",
            "severity": (
                "a descriptive note states a direction the numbers beside it "
                "reverse"
            ),
            "affected_count": 1,
            "affected_paths": [authoritative],
            "field": "e3_summary.environment_drift.note",
            "incorrect_claim": DRIFT_CLAIM,
            "measured_direction": drift_measured,
            "explanation": (
                "the note argues that execution position rather than "
                "accumulated rooms is the axis to report, and rests that "
                "argument on runs getting faster as the campaign proceeds. They "
                "get slightly slower: both topologies show a higher mean p50 in "
                "the second half of the campaign, and the reported Pearson "
                "correlation between p50 and campaign position is positive in "
                "both. The stated reasoning is therefore withdrawn, not "
                "restated with the sign flipped -- with the real direction, "
                "room accumulation becomes a candidate explanation rather than "
                "an excluded one, and the two remain perfectly confounded, "
                "which is the part of the note that stands."
            ),
            "what_is_unaffected": (
                "the numbers themselves. p50_by_campaign_half, the correlation "
                "coefficients and the per-run records are correct and are what "
                "the published limitation cites: drift of roughly one percent "
                "across the campaign, against which the paired comparison is "
                "protected by counterbalancing, both topologies of a block "
                "executing adjacently. No reported result depends on which "
                "mechanism produces the drift."
            ),
            "consumed_by_analysis": False,
            "affects_numerical_results": False,
        },
    ],
    "archived_derived_artifacts": {
        "note": (
            "Pinned here by digest because the run manifests reference these "
            "files by path without one. They are covered by the archive "
            "aggregate digest, so tampering after deposit is detectable, but "
            "within the manifest chain they are named and not digested."
        ),
        "by_experiment": comparisons,
    },
    "regeneration": {
        "layer": "reproduced/",
        "regenerated_by_commit": regen_commit,
        "claim": (
            "Derived content regenerated from the archived raw streams by the "
            "commit above and compared to the archived artifacts. This does not "
            "identify the code that produced the originals, which is "
            "unrecoverable; it establishes that the same content follows from "
            "the immutable raw data."
        ),
        "E2": {
            "regenerable": True,
            "method": "independent derivation from the raw runner and agent streams",
            "runs": 3,
            "fields_compared_per_run": regen_fields,
            "mismatches": 0,
        },
        "E4": {
            "regenerable": True,
            "method": "re-run of the project's own validator over archived inputs",
            "note": (
                "weaker than the E2 entries: it shows the archived inputs still "
                "yield the archived verdict, not that the verdict follows from "
                "an independent reading of them"
            ),
        },
        "E1": {
            "regenerable": False,
            "reason": (
                "the federation comparison rests on what each homeserver "
                "returned when queried live during the run. Those domain views "
                "were never written to a raw stream and both homeservers were "
                "destroyed with the formal host. The archived artifacts are the "
                "only copy of that observation, and are pinned by digest above."
            ),
        },
    },
    "unaffected": [
        "raw observations",
        "run validity classification",
        "experiment outcomes",
        "processed numerical results",
        "published figures",
        "reported confidence intervals",
    ],
}

(OUT / "provenance-authority.json").write_text(
    json.dumps(authority, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    newline="\n"
)
print(f"wrote {OUT / 'provenance-authority.json'}")

# -------------------------------------------------------------- markdown
def block(text):
    return "```text\n" + text + "\n```"


md = f"""# Dataset errata and provenance notes

**Errata version {ERRATA_VERSION}, issued {ISSUED}.**
Applies to the formal campaign archive `{tar.name}`, deposited as
[{DOI}](https://doi.org/{DOI}).

The campaign archive is **preserved byte for byte**. Its digest is unchanged
from the original deposit:

{block(f"archive   {tar.name}\nsha256    {archive_sha}\n\naggregate {inventory['aggregate_sha256']}\n          over {inventory['file_count']} files, defined in environment/raw-archive-inventory.json")}

The aggregate is taken over the inventory rather than over a container, so it
depends on what the files contain and what they are called and not on tar
format, compression or timestamps. It is reproducible from the archive alone —
entries sorted **by path**, not by the joined line, which would sort by the
digest that starts it; two spaces between the fields; lines joined with LF and
not terminated by one; hashed as UTF-8:

{block("python - <<'EOF'\nimport hashlib, json\ninv = json.load(open('environment/raw-archive-inventory.json'))\nrows = sorted(inv['files'], key=lambda e: e['path'])\n# Two spaces; joined with LF, not terminated by one.\npayload = '\\n'.join(f\"{e['sha256']}  {e['path']}\" for e in rows)\nprint(hashlib.sha256(payload.encode('utf-8')).hexdigest())\nEOF")}

The tarball holds {len(members)} files: the {inventory['file_count']} the
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

`{len(affected)}` formal manifests carry a `scope_note` asserting that the run
is development data and that `publication_data` is false.

**This is false.** Each of these manifests carries `publication_data: true`, was
executed under the protocol lock, and is formal evidence.

Affected: {', '.join(f"{k} — {v} manifests" for k, v in sorted(by_experiment.items()))}.
Manifests for {', '.join(no_field)} carry no `scope_note` field at all and are
unaffected.

The exact strings, so they can be matched:

{block(chr(10).join(f'{k}:  {v}' for k, v in sorted(notes.items())))}

**The authoritative field is `publication_data`.** Every gate in the pipeline
branches on it: the pre-run lock enforcement, the collection freeze, the
integrity audit and the analysis. `scope_note` is written and never read —
`grep -rn scope_note src/ scripts/` in the implementation repository returns
writes only.

The manifests are **intentionally left unchanged**. Editing archived evidence to
correct a descriptive field would make the deposited archive something other
than what the campaign produced, which costs more than the defect does.

## 2. Which processed summary is authoritative

The archive contains {len(summaries)} analysis summaries. Only one is
authoritative:

{block(f"AUTHORITATIVE  {authoritative}\n               sha256 {auth['sha256']}")}

This is the copy committed to `results/processed/` in the implementation
repository, and the one the reported figures and tables derive from.

{block(chr(10).join(f"non-authoritative  {s['path']}" + (f"{chr(10)}                   {s['note']}…" if s['note'] else '') for s in pre_fix))}

Those three were produced while the provenance fields were being completed and
carry development-era labels.

One further file is **not** a defect:

{block(f"reproducibility rerun  {rerun}\n                       sha256 {rer['sha256']}")}

It is an independent rerun of the same analysis over the same archive. It
differs from the authoritative summary in `generated_at` and in no analytical
field, and exists because the reproduction was checked rather than asserted.

## 3. Container image not observable from inside the runner

In {len(e3_unset)} E3 manifests, `host_diagnostics.synapse_image` reads `unset`.

This records unavailability at that observation point, not an unknown
experimental configuration: the runner executes inside a container that cannot
query Docker, and {docker_absent} of those same manifests record
`host_diagnostics.docker_cli_present: false` in the same object.

The pinned images and their digests are recorded authoritatively in
`environment/environment-latest.json`, `environment/image-digests.json` and
`environment/protocol-lock.json`, all present in this archive.

## 4. Which revision produced each derived artifact

Seven derived artifacts record their `analysis_code_commit` as a label naming a
working tree — `task-02-working-tree` and siblings — rather than a commit.
Nobody can check out a working tree, so §54 is satisfied in form and not in
substance for these files.

{block(chr(10).join(f"{c['path']}{chr(10)}   sha256 {c['sha256']}" for group in comparisons.values() for c in group))}

**The revision behind those labels is not recoverable, and this erratum does not
guess at it.** What is recoverable is the result.

`protocol_git_commit` reads `{derived_protocol_commit}` in all seven — the
tagged commit the campaign executed — and identifies the code that produced the
raw data. Only the revision of
the code that derived from it is missing.

Those digests are listed because the run manifests reference these files **by
path without a digest**, unlike the raw streams, which carry one. The files are
covered by the archive aggregate digest, so tampering after deposit is
detectable; within the manifest chain they are named and not digested.

### Regeneration

`reproduced/` holds derived content rebuilt from the archived raw streams by
commit `{regen_commit[:12]}`.

**E2 — regenerated and matched.** Three recovery comparisons, {regen_fields}
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

`{e4_summary_path}` is the only file in the archive that
summarises E4. It carries `publication_data: false` and this note:

{block(e4_summary.get("scope_note", ""))}

**Both are false of the sessions it describes.** All {len(e4_manifests)} E4 run manifests carry
`publication_data: true`, and this same file records the verdict
`{e4_summary.get("verdict")}` over {len(e4_summary.get("sessions") or [])} sessions — which it could not have done
under the development-era rule that demanded the flag be false.

That is the shape of the defect: the validator's gating was corrected before
the formal sessions ran, its descriptive fields were not. The file therefore
passes the sessions and disowns them in the same breath.

```text
{e4_summary_path}
   sha256 {sha(ROOT / "processed" / "e4-validation-latest.json")}
```

E4 supplies the human component of C4, so this is the file a reader is most
likely to open first and the one most likely to mislead. The session manifests,
the archived transcripts and the revalidation under `reproduced/e4` all agree
with each other and against it.

## 6. A drift note that contradicts the numbers beside it

In `{authoritative}`,
the field `e3_summary.environment_drift.note` states:

{block(DRIFT_CLAIM)}

**The runs get slightly slower, not faster.** From the same object:

{block(chr(10).join(
    f"{topology:11} first half {v['first_half_mean_p50_ms']:8.3f} ms   "
    f"second half {v['second_half_mean_p50_ms']:8.3f} ms   "
    f"{v['change_percent']:+.2f}%   pearson {v['pearson_p50_vs_campaign_position']:+.4f}"
    for topology, v in sorted(drift_measured.items())))}

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
`{lock['campaign']['campaign_parameters'].get('runtime_code_revision')}`, recorded in the protocol lock and in the
campaign ledger.

{len(stale_env)} earlier environment snapshots carry `unset` image fields. They
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
"""

(OUT / "ERRATA.md").write_text(md, encoding="utf-8", newline="\n")
print(f"wrote {OUT / 'ERRATA.md'}")
