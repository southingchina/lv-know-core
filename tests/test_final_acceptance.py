from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

import final_acceptance  # noqa: E402
import validate_preview  # noqa: E402
from test_metrics_and_promotion import make_preview, write_json, write_jsonl  # noqa: E402


def prepare_master(base: Path) -> tuple[Path, Path, Path]:
    master, manifest, vault = make_preview(base)
    metrics = validate_preview.derive_metrics(master)
    write_json(master / "build-metrics.json", metrics)
    atoms = validate_preview.read_jsonl(master / "atoms.jsonl")
    scope = validate_preview.read_json(master / "scope.json")
    write_json(
        master / "run-state.json",
        {
            "schema_version": 1,
            "policy_version": "v2-test",
            "cards": {
                str(card["id"]): {
                    "sha256": card["sha256"],
                    "relative_path": card["relative_path"],
                }
                for card in scope["cards"]
            },
            "atoms": {
                str(atom["atom_id"]): {
                    "version": atom["version"],
                    "content_hash": atom["content_hash"],
                }
                for atom in atoms
            },
        },
    )
    write_json(master / "validation.json", validate_preview.validate_preview(master))
    return master, manifest, vault


class FinalAcceptanceTests(unittest.TestCase):
    def test_valid_master_and_matching_dify_evidence_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            master, manifest, vault = prepare_master(Path(tmp))
            metrics = json.loads((master / "build-metrics.json").read_text(encoding="utf-8"))
            sync = {
                "status": "passed",
                "dataset_name": "geo博主",
                "document_name": "geo博主-三层知识点-全量.txt",
                "remaining_document_count": 1,
                "verified_atom_count": 1,
                "atom_hashes_sha256": final_acceptance.atom_hashes_sha256(master),
                "recovered_remote_correct": False,
            }
            evidence = final_acceptance.accept(master, manifest, vault, sync, sync_mode="full_document_replace")
            self.assertEqual(evidence["status"], "passed")
            self.assertEqual(evidence["counts"]["atoms"], metrics["counts"]["atoms"])
            self.assertTrue(all(check["passed"] for check in evidence["checks"]))

    def test_index_mismatch_and_wrong_dify_count_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            master, manifest, vault = prepare_master(Path(tmp))
            index = validate_preview.read_jsonl(master / "atom-index.jsonl")
            index[0]["content_hash"] = "wrong"
            write_jsonl(master / "atom-index.jsonl", index)
            sync = {
                "status": "passed",
                "dataset_name": "geo博主",
                "document_name": "geo博主-三层知识点-全量.txt",
                "remaining_document_count": 1,
                "verified_atom_count": 99,
                "atom_hashes_sha256": "wrong",
            }
            evidence = final_acceptance.accept(master, manifest, vault, sync, sync_mode="full_document_replace")
            self.assertEqual(evidence["status"], "failed")
            failed_ids = {check["id"] for check in evidence["checks"] if not check["passed"]}
            self.assertIn("local_contract", failed_ids)
            self.assertIn("dify_atom_identity", failed_ids)

    def test_report_records_recovered_anomaly_and_is_the_only_final_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            master, manifest, vault = prepare_master(Path(tmp))
            sync = {
                "status": "passed",
                "dataset_name": "geo博主",
                "document_name": "geo博主-三层知识点-全量.txt",
                "remaining_document_count": 1,
                "verified_atom_count": 1,
                "atom_hashes_sha256": final_acceptance.atom_hashes_sha256(master),
                "recovered_remote_correct": True,
            }
            evidence = final_acceptance.accept(master, manifest, vault, sync, sync_mode="full_document_replace")
            report = final_acceptance.render_report(evidence)
            self.assertIn("已恢复异常", report)
            self.assertIn("线上内容已正确", report)
            self.assertIn("Dify", report)

    def test_external_backup_root_is_used_as_snapshot_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            master, manifest, vault = prepare_master(base)
            backup_root = base / "lv-know-backups"
            snapshot = backup_root / master.name / "run-before"
            snapshot.mkdir(parents=True)
            shutil.copy2(master / "atoms.jsonl", snapshot / "atoms.jsonl")
            write_json(
                master / "build-metrics.json",
                validate_preview.derive_metrics(master, snapshot),
            )

            evidence = final_acceptance.accept(
                master, manifest, vault, backup_root=backup_root
            )

            check = next(row for row in evidence["checks"] if row["id"] == "snapshot_evidence")
            self.assertEqual(evidence["status"], "passed")
            self.assertTrue(Path(check["detail"]).is_relative_to(backup_root))

    def test_one_report_includes_input_layer2_and_index_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            master, manifest, vault = prepare_master(Path(tmp))
            run_summary = {
                "input_transcripts": 12,
                "skipped_sources": 2,
                "staged_layer2_cards": 10,
                "committed_layer2_cards": 10,
                "layer2_index_changes": 10,
            }

            evidence = final_acceptance.accept(
                master, manifest, vault, run_summary=run_summary
            )
            report = final_acceptance.render_report(evidence)

            self.assertEqual(evidence["pipeline"], run_summary)
            self.assertIn("输入转录：12", report)
            self.assertIn("跳过来源：2", report)
            self.assertIn("正式二层新增：10", report)
            self.assertIn("二层索引变化：10", report)

    def test_ecommerce_segment_acceptance_does_not_require_one_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            master, manifest, vault = prepare_master(Path(tmp))
            atom = validate_preview.read_jsonl(master / "atoms.jsonl")[0]
            sync = {
                "status": "passed",
                "sync_mode": "segment_incremental",
                "dataset_name": "电商知识库",
                "dataset_id": "00000000-0000-4000-8000-000000000001",
                "baseline_unchanged": True,
                "mapping_consistent": True,
                "continuation_complete": True,
                "affected_segments": [
                    {
                        "atom_id": atom["atom_id"],
                        "segment_id": "segment-001",
                        "status": "completed",
                        "content_hash": atom["content_hash"],
                    }
                ],
            }

            evidence = final_acceptance.accept(
                master,
                manifest,
                vault,
                sync,
                profile="ecommerce",
                sync_mode="segment_incremental",
                expected_dataset_name="电商知识库",
            )

            self.assertEqual(evidence["status"], "passed")
            self.assertNotIn("remaining_document_count", json.dumps(evidence, ensure_ascii=False))

    def test_ai_profile_derives_its_own_single_document_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            master, manifest, vault = prepare_master(Path(tmp))
            sync = {
                "status": "passed",
                "dataset_name": "AI博主",
                "document_name": "AI博主-三层知识点-全量.txt",
                "remaining_document_count": 1,
                "verified_atom_count": 1,
                "atom_hashes_sha256": final_acceptance.atom_hashes_sha256(master),
            }

            evidence = final_acceptance.accept(
                master,
                manifest,
                vault,
                sync,
                profile="ai",
                sync_mode="full_document_replace",
            )

            self.assertEqual(evidence["status"], "passed")


if __name__ == "__main__":
    unittest.main()
