from __future__ import annotations

import hashlib
import inspect
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validate = load_module("validate_preview_v2_metrics_test", SCRIPTS / "validate_preview.py")
promote = load_module("promote_preview_v2_metrics_test", SCRIPTS / "promote_preview.py")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def make_preview(base: Path) -> tuple[Path, Path, Path]:
    vault = base / "vault"
    vault.mkdir()
    card = vault / "card.md"
    card.write_text("source card", encoding="utf-8")
    card_hash = sha256_file(card)
    manifest = vault / "manifest.json"
    write_json(
        manifest,
        {
            "cards": [
                {
                    "id": "card-1",
                    "blogger": "blogger-a",
                    "primary_theme": "theme-a",
                    "relative_path": "card.md",
                    "sha256": card_hash,
                }
            ]
        },
    )

    preview = base / "preview"
    preview.mkdir()
    write_json(
        preview / "scope.json",
        {
            "schema_version": 1,
            "manifest_sha256": sha256_file(manifest),
            "mode": "all",
            "count": 1,
            "cards": [
                {
                    "id": "card-1",
                    "sha256": card_hash,
                    "relative_path": "card.md",
                    "selection_reason": "all",
                }
            ],
        },
    )
    candidates = [
        {
            "candidate_id": "cand-a",
            "answerable_question": "问题A",
            "question_keys": ["q-a"],
            "claim": "观点A",
            "source_card_id": "card-1",
            "source_blogger": "blogger-a",
            "source_card_sha256": card_hash,
            "candidate_hash": "candidate-hash-a",
            "disposition": "进入三层",
        },
        {
            "candidate_id": "cand-b",
            "answerable_question": "问题B",
            "question_keys": ["q-b"],
            "claim": "观点B",
            "source_card_id": "card-1",
            "source_blogger": "blogger-b",
            "source_card_sha256": card_hash,
            "candidate_hash": "candidate-hash-b",
            "disposition": "进入三层",
        },
    ]
    write_jsonl(preview / "candidates.jsonl", candidates)
    atoms = [
        {
            "atom_id": "atom-a",
            "answerable_question": "问题A",
            "question_keys": ["q-a", "q-b"],
            "claim": "共同观点",
            "type": "判断",
            "knowledge_status": "多人共识",
            "primary_theme": "theme-a",
            "related_themes": [],
            "boundary": "适用于测试",
            "source_card_ids": ["card-1"],
            "source_candidate_ids": ["cand-a", "cand-b"],
            "related_atom_ids": [],
            "version": 1,
            "content_hash": "atom-content-hash-a",
        }
    ]
    write_jsonl(preview / "atoms.jsonl", atoms)
    write_jsonl(
        preview / "atom-index.jsonl",
        [
            {
                "atom_id": "atom-a",
                "question_keys": ["q-a", "q-b"],
                "primary_theme": "theme-a",
                "related_themes": [],
                "knowledge_status": "多人共识",
                "content_hash": "atom-content-hash-a",
                "source_count": 1,
            }
        ],
    )
    write_json(
        preview / "question-registry.json",
        {"schema_version": 1, "questions": [{"question_key": "q-a"}, {"question_key": "q-b"}]},
    )
    write_json(
        preview / "source-map.json",
        {
            "schema_version": 1,
            "card_to_atoms": {"card-1": ["atom-a"]},
            "atom_to_cards": {"atom-a": ["card-1"]},
        },
    )
    return preview, manifest, vault


class MetricsTests(unittest.TestCase):
    def test_derive_metrics_counts_final_consensus_and_records_input_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview, _, _ = make_preview(Path(tmp))
            metrics = validate.derive_metrics(preview)
            self.assertEqual(metrics["counts"]["atoms"], 1)
            self.assertEqual(metrics["counts"]["knowledge_status"]["多人共识"], 1)
            self.assertEqual(
                metrics["generated_from"]["atoms.jsonl"], sha256_file(preview / "atoms.jsonl")
            )

    def test_validate_preview_rejects_stale_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            preview, _, _ = make_preview(Path(tmp))
            metrics = validate.derive_metrics(preview)
            metrics["counts"]["knowledge_status"]["多人共识"] = 0
            write_json(preview / "build-metrics.json", metrics)
            with self.assertRaisesRegex(validate.PreviewValidationError, "build-metrics"):
                validate.validate_preview(preview)

    def test_derive_metrics_distinguishes_updated_merged_and_unchanged_atoms(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            preview, _, _ = make_preview(base)
            previous = base / "previous"
            previous.mkdir()
            previous_atom = {
                **validate.read_jsonl(preview / "atoms.jsonl")[0],
                "content_hash": "previous-content-hash",
                "source_candidate_ids": ["cand-a"],
            }
            write_jsonl(previous / "atoms.jsonl", [previous_atom])
            metrics = validate.derive_metrics(preview, previous)
            self.assertEqual(
                metrics["changes"],
                {
                    "added_atoms": 0,
                    "merged_into_existing_atoms": 1,
                    "removed_atoms": 0,
                    "unchanged_atoms": 0,
                    "updated_existing_atoms": 1,
                },
            )

    def test_promotion_manages_build_metrics(self):
        self.assertIn("build-metrics.json", promote.CORE_FILES)

    def test_promotion_defers_the_only_final_report_to_acceptance(self):
        self.assertNotIn("review-report.md", promote.CORE_FILES)
        self.assertNotIn("Dify：未请求", inspect.getsource(promote))

    def test_snapshot_set_keeps_prior_run_evidence(self):
        expected = {
            "build-metrics.json",
            "manual-approval-delta.json",
            "dify-single-document-state.json",
            "final-acceptance.json",
            "review-report.md",
        }
        self.assertTrue(expected.issubset(set(promote.SNAPSHOT_FILES)))

    def test_second_promotion_snapshots_metrics_and_manual_review_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            preview, manifest, vault = make_preview(base)
            write_json(preview / "build-metrics.json", validate.derive_metrics(preview))
            write_json(preview / "manual-approval-delta.json", {"schema_version": 1, "approved": 1})
            target = base / "master"
            first = promote.promote_preview(preview, target, manifest, vault)
            self.assertEqual(first["status"], "passed")

            write_json(preview / "build-metrics.json", validate.derive_metrics(preview, target))
            write_json(preview / "manual-approval-delta.json", {"schema_version": 1, "approved": 2})
            second = promote.promote_preview(preview, target, manifest, vault)
            snapshot = Path(second["snapshot"])
            self.assertTrue((snapshot / "build-metrics.json").is_file())
            self.assertEqual(
                json.loads((snapshot / "manual-approval-delta.json").read_text(encoding="utf-8"))["approved"],
                1,
            )
            self.assertEqual(
                json.loads((target / "manual-approval-delta.json").read_text(encoding="utf-8"))["approved"],
                2,
            )

    def test_snapshot_can_be_forced_into_dedicated_backup_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            preview, manifest, vault = make_preview(base)
            write_json(preview / "build-metrics.json", validate.derive_metrics(preview))
            target = base / "mirror"
            backup_root = base / "lv-know-backups"
            promote.promote_preview(
                preview, target, manifest, vault, backup_root=backup_root
            )
            write_json(preview / "build-metrics.json", validate.derive_metrics(preview, target))

            second = promote.promote_preview(
                preview, target, manifest, vault, backup_root=backup_root
            )

            snapshot = Path(second["snapshot"])
            self.assertTrue(snapshot.is_relative_to(backup_root))
            self.assertFalse((target / "snapshots").exists())


if __name__ == "__main__":
    unittest.main()
