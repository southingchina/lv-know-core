from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "sync_dify_single_document.py"
SYNC_REFERENCE = ROOT / "references" / "dify-single-document-sync.md"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_dify_single_document_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def atoms(count: int) -> list[dict]:
    return [
        {
            "atom_id": f"atom-{index:04d}",
            "version": 1,
            "content_hash": f"{index:064x}",
            "answerable_question": f"问题{index}",
            "knowledge_status": "单一博主观点",
            "primary_theme": "主题",
            "related_themes": [],
            "source_card_ids": [f"card-{index:04d}"],
            "source_candidate_ids": [f"candidate-{index:04d}"],
            "question_keys": [f"question-{index:04d}"],
            "related_atom_ids": [],
            "source_vault": "测试库",
            "source_relative_paths": [f"01-母卡/card-{index:04d}.md"],
            "source_card_sha256s": {f"card-{index:04d}": f"{index + 1000:064x}"},
            "evidence_quotes": [f"原文证据{index}"],
            "evidence_locators": [f"original:card-lines:{index}-{index}"],
            "boundary": "测试边界",
            "claim": f"观点{index}",
        }
        for index in range(1, count + 1)
    ]


class FakeTransport:
    def __init__(self, datasets: list[dict], documents: dict[str, list[dict]], markers: dict[str, dict[str, str]]):
        self.datasets = datasets
        self.documents = documents
        self.markers = markers
        self.calls: list[tuple] = []

    def list_datasets(self):
        return self.datasets

    def list_documents(self, dataset_id):
        return self.documents.get(dataset_id, [])

    def marker_map(self, dataset_id, document_id):
        return dict(self.markers.get(document_id, {}))

    def create_dataset(self, name, description, embedding_provider, embedding_model):
        self.calls.append(("create_dataset", name))
        dataset = {"id": "dataset-created", "name": name}
        self.datasets.append(dataset)
        self.documents["dataset-created"] = []
        return dataset

    def create_document(self, dataset_id, name, text, process_rule):
        self.calls.append(("create_document", dataset_id, name))
        document = {"id": "document-created", "name": name}
        self.documents.setdefault(dataset_id, []).append(document)
        self.markers["document-created"] = self.pending_markers
        return {"document_id": "document-created", "batch": "batch-created"}

    def update_document(self, dataset_id, document_id, name, text, process_rule):
        self.calls.append(("update_document", dataset_id, document_id, name))
        self.markers[document_id] = self.pending_markers
        return {"document_id": document_id, "batch": "batch-updated"}

    def wait_available(self, dataset_id, document_id, timeout):
        self.calls.append(("wait_available", dataset_id, document_id))
        return "completed"


class DifySingleDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_plan_first_create_and_unique_target_guards(self):
        projection = self.module.build_projection(atoms(2))
        plan = self.module.plan_sync(projection, [], {}, dataset_name="geo博主", document_name="geo博主-三层知识点-全量.txt", allow_create_dataset=True)
        self.assertEqual(plan["action"], "create_dataset")
        with self.assertRaisesRegex(self.module.DifySyncError, "dataset_not_found"):
            self.module.plan_sync(projection, [], {}, dataset_name="geo博主", document_name="geo博主-三层知识点-全量.txt", allow_create_dataset=False)
        duplicates = [{"id": "d1", "name": "geo博主", "documents": []}, {"id": "d2", "name": "geo博主", "documents": []}]
        with self.assertRaisesRegex(self.module.DifySyncError, "dataset_not_unique"):
            self.module.plan_sync(projection, duplicates, {}, dataset_name="geo博主", document_name="geo博主-三层知识点-全量.txt", allow_create_dataset=True)

    def test_projection_is_one_parent_per_atom_and_keeps_layer2_return_path(self):
        projection = self.module.build_projection(atoms(1))
        text = projection["text"]
        self.assertEqual(projection["atom_count"], 1)
        self.assertEqual(text.count(self.module.PARENT_SEPARATOR), 0)
        self.assertEqual(text.count("[ATOM_ID:atom-0001]"), 1)
        self.assertEqual(text.count("[CONTENT_HASH:"), 1)
        self.assertIn("[SOURCE_VAULT:测试库]", text)
        self.assertIn("[SOURCE_CARD_ID:card-0001]", text)
        self.assertIn("[SOURCE_PATH:01-母卡/card-0001.md]", text)
        self.assertIn(
            "[EVIDENCE_LOCATOR:original:card-lines:1-1]",
            text,
        )
        self.assertIn("[QUESTION:问题1]", text)
        self.assertIn("[CLAIM:观点1]", text)
        self.assertIn("[BOUNDARY:测试边界]", text)
        self.assertIn("[EVIDENCE:原文证据1]", text)
        self.assertNotIn("[PARENT_KIND:", text)
        self.assertNotIn("[RELATED_ATOM_IDS", text)
        self.assertNotIn("[SOURCE_CARD_SHA256S", text)
        self.assertNotIn("[SOURCE_CANDIDATE_IDS", text)

    def test_parent_projection_creates_small_semantic_child_chunks_for_retrieval(self):
        parent = self.module.build_projection(atoms(1))["text"].strip()
        child_chunks = [
            chunk.strip()
            for chunk in parent.split(self.module.CHILD_SEPARATOR)
            if chunk.strip()
        ]

        self.assertEqual(len(child_chunks), 5)
        self.assertIn("[ATOM_ID:atom-0001]", child_chunks[0])
        self.assertIn("[SOURCE_PATH:01-母卡/card-0001.md]", child_chunks[0])
        self.assertEqual(child_chunks[1], "[QUESTION:问题1]")
        self.assertEqual(child_chunks[2], "[CLAIM:观点1]")
        self.assertEqual(child_chunks[3], "[BOUNDARY:测试边界]")
        self.assertEqual(child_chunks[4], "[EVIDENCE:原文证据1]")

        child_rule = self.module.process_rule()["rules"]["subchunk_segmentation"]
        self.assertEqual(child_rule["separator"], self.module.CHILD_SEPARATOR)
        self.assertLessEqual(child_rule["max_tokens"], 256)

    def test_long_evidence_stays_in_one_bounded_parent(self):
        row = atoms(1)
        long_quote = "很长的原文证据" * 2000
        row[0]["evidence_quotes"] = [long_quote]
        row[0]["source_candidate_ids"] = [
            f"candidate-{index:04d}"
            for index in range(1000)
        ]
        row[0]["related_atom_ids"] = [
            f"related-{index:04d}"
            for index in range(1000)
        ]
        text = self.module.build_projection(row)["text"]

        parents = [
            part
            for part in text.split(self.module.PARENT_SEPARATOR)
            if part.strip()
        ]
        self.assertEqual(len(parents), 1)
        self.assertIn("[ATOM_ID:atom-0001]", parents[0])
        self.assertIn("[SOURCE_PATH:01-母卡/card-0001.md]", parents[0])
        self.assertIn(
            "[EVIDENCE_LOCATOR:original:card-lines:1-1]",
            parents[0],
        )
        self.assertLessEqual(len(parents[0]), 1800)
        self.assertNotIn(long_quote, parents[0])

    def test_projection_rejects_atom_without_traceability_fields(self):
        incomplete = atoms(1)
        del incomplete[0]["evidence_quotes"]
        with self.assertRaisesRegex(self.module.DifySyncError, "atom_projection_fields_missing"):
            self.module.build_projection(incomplete)

    def test_plan_updates_1560_to_1591_and_recovers_remote_correct(self):
        old_projection = self.module.build_projection(atoms(1560))
        new_projection = self.module.build_projection(atoms(1591))
        remote = [{
            "id": "dataset-1",
            "name": "geo博主",
            "documents": [{"id": "document-1", "name": "geo博主-三层知识点-全量.txt", "markers": old_projection["atom_hashes"]}],
        }]
        plan = self.module.plan_sync(new_projection, remote, {}, dataset_name="geo博主", document_name="geo博主-三层知识点-全量.txt")
        self.assertEqual(plan["action"], "update")
        remote[0]["documents"][0]["markers"] = new_projection["atom_hashes"]
        plan = self.module.plan_sync(new_projection, remote, {}, dataset_name="geo博主", document_name="geo博主-三层知识点-全量.txt")
        self.assertEqual(plan["action"], "recover")
        completed_state = {
            "phase": "completed",
            "dataset_id": "dataset-1",
            "document_id": "document-1",
            "projection_sha256": new_projection["sha256"],
        }
        plan = self.module.plan_sync(new_projection, remote, completed_state, dataset_name="geo博主", document_name="geo博主-三层知识点-全量.txt")
        self.assertEqual(plan["action"], "skip")

    def test_force_projection_update_reindexes_when_atom_markers_are_unchanged(self):
        projection = self.module.build_projection(atoms(2))
        remote = [{
            "id": "dataset-1",
            "name": "geo博主",
            "documents": [{
                "id": "document-1",
                "name": "geo博主-三层知识点-全量.txt",
                "markers": projection["atom_hashes"],
            }],
        }]

        plan = self.module.plan_sync(
            projection,
            remote,
            {},
            dataset_name="geo博主",
            document_name="geo博主-三层知识点-全量.txt",
            force_projection_update=True,
        )

        self.assertEqual(plan["action"], "update")
        self.assertEqual(plan["document_id"], "document-1")

    def test_sync_reference_documents_projection_format_reindex_gate(self):
        text = SYNC_REFERENCE.read_text(encoding="utf-8")

        self.assertIn("--force-projection-update", text)
        self.assertIn("投影格式", text)
        self.assertIn("完整恢复包", text)
        self.assertIn("一条 atom 对应一个父块", text)
        self.assertIn("SOURCE_PATH", text)
        self.assertIn("EVIDENCE_LOCATOR", text)
        self.assertIn("CLAIM", text)
        self.assertIn("审计字段不参与向量检索", text)

    def test_duplicate_official_document_and_wrong_dataset_write_are_rejected(self):
        projection = self.module.build_projection(atoms(2))
        remote = [{
            "id": "dataset-1",
            "name": "geo博主",
            "documents": [
                {"id": "document-1", "name": "geo博主-三层知识点-全量.txt", "markers": {}},
                {"id": "document-2", "name": "geo博主-三层知识点-全量.txt", "markers": {}},
            ],
        }]
        with self.assertRaisesRegex(self.module.DifySyncError, "document_not_unique"):
            self.module.plan_sync(projection, remote, {}, dataset_name="geo博主", document_name="geo博主-三层知识点-全量.txt")
        with self.assertRaisesRegex(self.module.DifySyncError, "write_guard"):
            self.module.guard_dataset_id("dataset-1", "dataset-other")

    def test_update_returning_original_document_id_is_not_a_delete_case(self):
        result = self.module.validate_update_receipt("document-1", {"document_id": "document-1", "batch": "b1"})
        self.assertEqual(result["document_id"], "document-1")
        self.assertEqual(result["updated_document_count"], 1)
        self.assertNotIn("delete", json.dumps(result))

    def test_update_uses_current_route_and_preserves_hierarchical_document_type(self):
        class RecordingTransport(self.module.DifyHttpTransport):
            def __init__(self):
                self.calls = []

            def request(self, method, api_path, body=None):
                self.calls.append((method, api_path, body))
                return {
                    "document": {"id": "document-1"},
                    "batch": "batch-1",
                }

        transport = RecordingTransport()
        rule = self.module.process_rule()
        receipt = transport.update_document(
            "dataset-1",
            "document-1",
            "geo博主-三层知识点-全量.txt",
            "content",
            rule,
        )

        self.assertEqual(receipt["document_id"], "document-1")
        self.assertEqual(
            transport.calls,
            [
                (
                    "POST",
                    "/datasets/dataset-1/documents/document-1/update-by-text",
                    {
                        "name": "geo博主-三层知识点-全量.txt",
                        "text": "content",
                        "doc_form": "hierarchical_model",
                        "doc_language": "Chinese",
                        "process_rule": rule,
                    },
                )
            ],
        )

    def test_remote_already_correct_rebuilds_state_without_upload(self):
        projection = self.module.build_projection(atoms(3))
        transport = FakeTransport(
            [{"id": "dataset-1", "name": "geo博主"}],
            {"dataset-1": [{"id": "document-1", "name": "geo博主-三层知识点-全量.txt"}]},
            {"document-1": projection["atom_hashes"]},
        )
        transport.pending_markers = projection["atom_hashes"]
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            report = self.module.sync_projection(
                transport,
                projection,
                state,
                dataset_name="geo博主",
                document_name="geo博主-三层知识点-全量.txt",
            )
            self.assertEqual(report["action"], "recover")
            self.assertFalse(any(call[0] in {"create_document", "update_document"} for call in transport.calls))
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(saved["phase"], "completed")
            self.assertTrue(saved["recovered_remote_correct"])

    def test_changed_document_updates_in_place_and_verifies_markers(self):
        old_projection = self.module.build_projection(atoms(2))
        projection = self.module.build_projection(atoms(3))
        transport = FakeTransport(
            [{"id": "dataset-1", "name": "geo博主"}],
            {"dataset-1": [{"id": "document-1", "name": "geo博主-三层知识点-全量.txt"}]},
            {"document-1": old_projection["atom_hashes"]},
        )
        transport.pending_markers = projection["atom_hashes"]
        with tempfile.TemporaryDirectory() as tmp:
            report = self.module.sync_projection(
                transport,
                projection,
                Path(tmp) / "state.json",
                dataset_name="geo博主",
                document_name="geo博主-三层知识点-全量.txt",
            )
            self.assertEqual(report["action"], "update")
            updates = [call for call in transport.calls if call[0] == "update_document"]
            self.assertEqual(updates, [("update_document", "dataset-1", "document-1", "geo博主-三层知识点-全量.txt")])
            self.assertEqual(report["verified_atom_count"], 3)


if __name__ == "__main__":
    unittest.main()
