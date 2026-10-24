#!/usr/bin/env python3
"""Safely reconcile one local Layer 3 projection with one Dify document."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:5001/v1"
PARENT_SEPARATOR = "\n[LVK_PARENT_BREAK_V1_9C4E]\n"
CHILD_SEPARATOR = "\n\n"
EVIDENCE_EXCERPT_CHARS = 600


class DifySyncError(RuntimeError):
    """Raised when the target identity or remote state is unsafe."""


def stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized_name(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    if not Path(path).is_file():
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise DifySyncError("state_invalid")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise DifySyncError(f"atom_row_invalid_{line_number}")
        rows.append(row)
    return rows


def compact_line(value: Any) -> str:
    return " ".join(str(value).split())


def source_reference_line(value: Any) -> str:
    """Keep literal identities and paths; prose whitespace rules do not apply."""
    result = str(value)
    if "\n" in result or "\r" in result:
        raise DifySyncError("source_reference_contains_newline")
    return result


def parent_text(atom: dict[str, Any]) -> str:
    required_scalars = (
        "atom_id",
        "version",
        "content_hash",
        "answerable_question",
        "knowledge_status",
        "primary_theme",
        "source_vault",
        "boundary",
        "claim",
    )
    required_collections = (
        "source_card_ids",
        "source_candidate_ids",
        "question_keys",
        "source_relative_paths",
        "source_card_sha256s",
        "evidence_quotes",
        "evidence_locators",
    )
    if any(atom.get(field) in (None, "", []) for field in required_scalars):
        raise DifySyncError("atom_projection_fields_missing")
    if any(not atom.get(field) for field in required_collections):
        raise DifySyncError("atom_projection_fields_missing")
    if not isinstance(atom.get("related_atom_ids"), list):
        raise DifySyncError("atom_projection_fields_missing")

    primary_source_card_id = str(atom["source_card_ids"][0])
    primary_source_path = str(atom["source_relative_paths"][0])
    primary_evidence_locator = str(atom["evidence_locators"][0])
    source_ids = sorted(
        {
            source_reference_line(value)
            for value in atom["source_card_ids"]
            if source_reference_line(value)
        }
    )
    evidence = compact_line(atom["evidence_quotes"][0])
    if len(evidence) > EVIDENCE_EXCERPT_CHARS:
        evidence = evidence[:EVIDENCE_EXCERPT_CHARS].rstrip() + "…"
    metadata_lines = [
        f"[ATOM_ID:{atom['atom_id']}]",
        f"[CONTENT_HASH:{atom['content_hash']}]",
        f"[SOURCE_VAULT:{source_reference_line(atom['source_vault'])}]",
        f"[SOURCE_CARD_ID:{source_reference_line(primary_source_card_id)}]",
        f"[SOURCE_PATH:{source_reference_line(primary_source_path)}]",
        f"[EVIDENCE_LOCATOR:{compact_line(primary_evidence_locator)}]",
    ]
    if len(source_ids) > 1:
        metadata_lines.append(
            "[ALL_SOURCE_CARD_IDS:"
            + stable_json(source_ids).decode("utf-8")
            + "]"
        )
    semantic_chunks = [
        f"[QUESTION:{compact_line(atom['answerable_question'])}]",
        f"[CLAIM:{compact_line(atom['claim'])}]",
        f"[BOUNDARY:{compact_line(atom['boundary'])}]",
        f"[EVIDENCE:{evidence}]",
    ]
    lines = metadata_lines + semantic_chunks
    # A non-empty verbatim quote may legitimately end in a colon. Only a
    # complete empty marker is invalid, not an arbitrary trailing substring.
    if any(re.fullmatch(r"\[[A-Z_]+:\]", line) for line in lines):
        raise DifySyncError("atom_projection_fields_missing")
    return CHILD_SEPARATOR.join(["\n".join(metadata_lines), *semantic_chunks])


def build_projection(atoms: list[dict[str, Any]]) -> dict[str, Any]:
    if not atoms:
        raise DifySyncError("empty_atoms")
    ordered = sorted(atoms, key=lambda row: str(row.get("atom_id", "")))
    atom_hashes: dict[str, str] = {}
    parts: list[str] = []
    for atom in ordered:
        atom_id = str(atom.get("atom_id", "")).strip()
        content_hash = str(atom.get("content_hash", "")).strip()
        if not atom_id or atom_id in atom_hashes:
            raise DifySyncError("duplicate_or_missing_atom_id")
        if not re.fullmatch(r"[a-f0-9]{64}", content_hash):
            raise DifySyncError("invalid_atom_content_hash")
        atom_hashes[atom_id] = content_hash
        parts.append(parent_text(atom))
    text = PARENT_SEPARATOR.join(parts) + "\n"
    payload = text.encode("utf-8")
    if len(payload) > 15 * 1024 * 1024:
        raise DifySyncError("projection_size_limit")
    return {
        "schema_version": 1,
        "text": text,
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
        "atom_count": len(ordered),
        "atom_hashes": dict(sorted(atom_hashes.items())),
        "atom_hashes_sha256": sha256_bytes(stable_json(atom_hashes)),
    }


def guard_dataset_id(expected: str, actual: str) -> None:
    if not expected or expected != actual:
        raise DifySyncError("write_guard_dataset_id_mismatch")


def validate_update_receipt(expected_document_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
    document_id = str(receipt.get("document_id", ""))
    batch = str(receipt.get("batch", ""))
    if document_id != expected_document_id or not batch:
        raise DifySyncError("update_receipt_identity_mismatch")
    return {
        "document_id": document_id,
        "batch": batch,
        "updated_document_count": 1,
    }


def plan_sync(
    projection: dict[str, Any],
    remote_datasets: list[dict[str, Any]],
    prior_state: dict[str, Any],
    *,
    dataset_name: str,
    document_name: str,
    allow_create_dataset: bool = False,
    force_projection_update: bool = False,
) -> dict[str, Any]:
    target_normalized = normalized_name(dataset_name)
    aliases = [
        row
        for row in remote_datasets
        if normalized_name(row.get("name", "")) == target_normalized
        and str(row.get("name", "")) != dataset_name
    ]
    exact = [row for row in remote_datasets if str(row.get("name", "")) == dataset_name]
    if aliases or len(exact) > 1:
        raise DifySyncError("dataset_not_unique")
    if not exact:
        if not allow_create_dataset:
            raise DifySyncError("dataset_not_found")
        return {"action": "create_dataset", "dataset_id": "", "document_id": ""}

    dataset = exact[0]
    dataset_id = str(dataset.get("id", ""))
    if not dataset_id:
        raise DifySyncError("dataset_identity_invalid")
    documents = list(dataset.get("documents", []))
    official = [row for row in documents if str(row.get("name", "")) == document_name]
    if len(official) > 1:
        raise DifySyncError("document_not_unique")
    if len(documents) != len(official):
        raise DifySyncError("document_set_not_single")
    if not official:
        return {"action": "create_document", "dataset_id": dataset_id, "document_id": ""}

    document = official[0]
    document_id = str(document.get("id", ""))
    if not document_id:
        raise DifySyncError("document_identity_invalid")
    remote_markers = {
        str(atom_id): str(content_hash)
        for atom_id, content_hash in dict(document.get("markers", {})).items()
    }
    if remote_markers == projection["atom_hashes"]:
        if force_projection_update:
            return {
                "action": "update",
                "dataset_id": dataset_id,
                "document_id": document_id,
            }
        bound = (
            prior_state.get("phase") == "completed"
            and prior_state.get("dataset_id") == dataset_id
            and prior_state.get("document_id") == document_id
            and prior_state.get("projection_sha256") == projection["sha256"]
        )
        return {
            "action": "skip" if bound else "recover",
            "dataset_id": dataset_id,
            "document_id": document_id,
        }
    return {"action": "update", "dataset_id": dataset_id, "document_id": document_id}


def process_rule() -> dict[str, Any]:
    return {
        "mode": "hierarchical",
        "rules": {
            "pre_processing_rules": [
                {"id": "remove_extra_spaces", "enabled": False},
                {"id": "remove_urls_emails", "enabled": False},
            ],
            "parent_mode": "paragraph",
            "segmentation": {
                "separator": PARENT_SEPARATOR,
                "max_tokens": 2000,
                "chunk_overlap": 0,
            },
            "subchunk_segmentation": {
                "separator": CHILD_SEPARATOR,
                "max_tokens": 256,
                "chunk_overlap": 0,
            },
        },
    }


def _enriched_inventory(transport: Any, dataset_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset in transport.list_datasets():
        item = dict(dataset)
        if str(item.get("name", "")) == dataset_name:
            dataset_id = str(item.get("id", ""))
            documents = []
            for document in transport.list_documents(dataset_id):
                document_item = dict(document)
                document_item["markers"] = transport.marker_map(dataset_id, str(document_item["id"]))
                documents.append(document_item)
            item["documents"] = documents
        rows.append(item)
    return rows


def sync_projection(
    transport: Any,
    projection: dict[str, Any],
    state_path: Path,
    *,
    dataset_name: str,
    document_name: str,
    allow_create_dataset: bool = False,
    embedding_provider: str = "",
    embedding_model: str = "",
    force_projection_update: bool = False,
    timeout: float = 1800,
) -> dict[str, Any]:
    state_path = Path(state_path)
    prior_state = read_json(state_path)
    inventory = _enriched_inventory(transport, dataset_name)
    plan = plan_sync(
        projection,
        inventory,
        prior_state,
        dataset_name=dataset_name,
        document_name=document_name,
        allow_create_dataset=allow_create_dataset,
        force_projection_update=force_projection_update,
    )
    action = str(plan["action"])
    if action == "create_dataset":
        if not embedding_provider or not embedding_model:
            raise DifySyncError("dataset_create_embedding_required")
        created = transport.create_dataset(
            dataset_name,
            "GEO二层素材提炼后的三层知识点",
            embedding_provider,
            embedding_model,
        )
        if str(created.get("name", "")) != dataset_name:
            raise DifySyncError("dataset_create_identity_mismatch")
        plan = {
            "action": "create_document",
            "dataset_id": str(created.get("id", "")),
            "document_id": "",
        }
        action = "create_document"

    dataset_id = str(plan.get("dataset_id", ""))
    if not dataset_id:
        raise DifySyncError("dataset_identity_invalid")
    checkpoint = {
        "schema_version": 1,
        "phase": "prepared",
        "dataset_name": dataset_name,
        "dataset_id": dataset_id,
        "document_name": document_name,
        "document_id": str(plan.get("document_id", "")),
        "projection_sha256": projection["sha256"],
        "atom_hashes_sha256": projection["atom_hashes_sha256"],
        "atom_count": projection["atom_count"],
        "action": action,
    }
    atomic_json(state_path, checkpoint)

    created_count = 0
    updated_count = 0
    recovered = action == "recover"
    if action == "create_document":
        guard_dataset_id(dataset_id, dataset_id)
        receipt = transport.create_document(
            dataset_id, document_name, projection["text"], process_rule()
        )
        document_id = str(receipt.get("document_id", ""))
        if not document_id or not str(receipt.get("batch", "")):
            raise DifySyncError("create_receipt_invalid")
        created_count = 1
    elif action == "update":
        document_id = str(plan["document_id"])
        guard_dataset_id(dataset_id, dataset_id)
        receipt = transport.update_document(
            dataset_id, document_id, document_name, projection["text"], process_rule()
        )
        validate_update_receipt(document_id, receipt)
        updated_count = 1
    else:
        document_id = str(plan["document_id"])

    if action in {"create_document", "update"}:
        checkpoint.update({"phase": "indexing", "document_id": document_id})
        atomic_json(state_path, checkpoint)
        if transport.wait_available(dataset_id, document_id, timeout) != "completed":
            raise DifySyncError("document_indexing_failed")

    verified_inventory = _enriched_inventory(transport, dataset_name)
    verified_plan = plan_sync(
        projection,
        verified_inventory,
        {},
        dataset_name=dataset_name,
        document_name=document_name,
        allow_create_dataset=False,
    )
    if verified_plan["action"] != "recover":
        raise DifySyncError("remote_marker_verification_failed")
    if str(verified_plan["dataset_id"]) != dataset_id or str(verified_plan["document_id"]) != document_id:
        raise DifySyncError("remote_identity_verification_failed")

    checkpoint.update(
        {
            "phase": "completed",
            "document_id": document_id,
            "verified_atom_count": projection["atom_count"],
            "recovered_remote_correct": recovered,
        }
    )
    atomic_json(state_path, checkpoint)
    return {
        "schema_version": 1,
        "status": "passed",
        "action": action,
        "dataset_name": dataset_name,
        "dataset_id": dataset_id,
        "document_name": document_name,
        "document_id": document_id,
        "projection_sha256": projection["sha256"],
        "atom_hashes_sha256": projection["atom_hashes_sha256"],
        "verified_atom_count": projection["atom_count"],
        "created_document_count": created_count,
        "updated_document_count": updated_count,
        "skipped_document_count": 1 if action == "skip" else 0,
        "recovered_remote_correct": recovered,
        "remaining_document_count": 1,
    }


class DifyHttpTransport:
    def __init__(self, base_url: str, api_key: str):
        if not api_key or "\n" in api_key or "\r" in api_key:
            raise DifySyncError("credential_unavailable")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key

    def request(self, method: str, api_path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if not api_path.startswith("/") or ".." in api_path:
            raise DifySyncError("unsafe_api_path")
        request = urllib.request.Request(
            self.base_url + api_path,
            data=None if body is None else stable_json(body),
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            raise DifySyncError(f"http_{error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DifySyncError("transport_failed") from None
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise DifySyncError("invalid_json_response") from None
        if not isinstance(parsed, dict):
            raise DifySyncError("invalid_response_shape")
        return parsed

    def collection(self, api_path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, 101):
            separator = "&" if "?" in api_path else "?"
            payload = self.request("GET", f"{api_path}{separator}page={page}&limit=100")
            batch = payload.get("data", [])
            if not isinstance(batch, list):
                raise DifySyncError("collection_invalid")
            rows.extend(row for row in batch if isinstance(row, dict))
            if not payload.get("has_more") or not batch:
                return rows
        raise DifySyncError("pagination_limit")

    def list_datasets(self):
        return self.collection("/datasets")

    def list_documents(self, dataset_id: str):
        return self.collection(f"/datasets/{dataset_id}/documents")

    def marker_map(self, dataset_id: str, document_id: str) -> dict[str, str]:
        markers: dict[str, str] = {}
        segments = self.collection(f"/datasets/{dataset_id}/documents/{document_id}/segments")
        for segment in segments:
            content = str(segment.get("content", ""))
            atom_match = re.search(r"\[ATOM_ID:([^\]\r\n]+)\]", content)
            hash_match = re.search(r"\[CONTENT_HASH:([a-f0-9]{64})\]", content)
            if not atom_match or not hash_match:
                continue
            atom_id = atom_match.group(1).strip()
            content_hash = hash_match.group(1)
            if atom_id in markers and markers[atom_id] != content_hash:
                raise DifySyncError("remote_duplicate_atom_marker")
            markers[atom_id] = content_hash
        return dict(sorted(markers.items()))

    def create_dataset(self, name, description, embedding_provider, embedding_model):
        return self.request(
            "POST",
            "/datasets",
            {
                "name": name,
                "description": description,
                "permission": "only_me",
                "provider": "vendor",
                "indexing_technique": "high_quality",
                "embedding_model_provider": embedding_provider,
                "embedding_model": embedding_model,
            },
        )

    @staticmethod
    def _receipt(payload: dict[str, Any]) -> dict[str, str]:
        document = payload.get("document")
        if not isinstance(document, dict):
            raise DifySyncError("document_receipt_missing")
        document_id = str(document.get("id", ""))
        batch = str(payload.get("batch", ""))
        if not document_id or not batch:
            raise DifySyncError("document_receipt_invalid")
        return {"document_id": document_id, "batch": batch}

    def create_document(self, dataset_id, name, text, rule):
        payload = self.request(
            "POST",
            f"/datasets/{dataset_id}/document/create-by-text",
            {
                "name": name,
                "text": text,
                "indexing_technique": "high_quality",
                "doc_form": "hierarchical_model",
                "doc_language": "Chinese",
                "process_rule": rule,
            },
        )
        return self._receipt(payload)

    def update_document(self, dataset_id, document_id, name, text, rule):
        payload = self.request(
            "POST",
            f"/datasets/{dataset_id}/documents/{document_id}/update-by-text",
            {
                "name": name,
                "text": text,
                "doc_form": "hierarchical_model",
                "doc_language": "Chinese",
                "process_rule": rule,
            },
        )
        return self._receipt(payload)

    def wait_available(self, dataset_id: str, document_id: str, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            detail = self.request("GET", f"/datasets/{dataset_id}/documents/{document_id}")
            if detail.get("display_status") == "available" and detail.get("indexing_status") == "completed":
                return "completed"
            if detail.get("display_status") == "error" or detail.get("indexing_status") == "error":
                return "failed"
            time.sleep(2)
        return "timeout"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("DIFY_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--document-name", required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-create-dataset", action="store_true")
    parser.add_argument("--force-projection-update", action="store_true")
    parser.add_argument("--embedding-provider", default="")
    parser.add_argument("--embedding-model", default="")
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()

    try:
        projection = build_projection(read_jsonl(args.master / "atoms.jsonl"))
        official_root = args.master / "dify-projection" / "official"
        official_root.mkdir(parents=True, exist_ok=True)
        official_path = official_root / args.document_name
        official_path.write_text(projection["text"], encoding="utf-8")
        atomic_json(
            official_root / "manifest.json",
            {
                key: value
                for key, value in projection.items()
                if key not in {"text", "atom_hashes"}
            }
            | {
                "dataset_name": args.dataset_name,
                "document_name": args.document_name,
                "document_path": str(official_path),
            },
        )
        key = os.environ.get("DIFY_API_KEY", "")
        transport = DifyHttpTransport(args.base_url, key)
        state_path = args.state or (args.master / "dify-single-document-state.json")
        report = sync_projection(
            transport,
            projection,
            state_path,
            dataset_name=args.dataset_name,
            document_name=args.document_name,
            allow_create_dataset=args.allow_create_dataset,
            embedding_provider=args.embedding_provider,
            embedding_model=args.embedding_model,
            force_projection_update=args.force_projection_update,
            timeout=args.timeout,
        )
        atomic_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (DifySyncError, OSError, ValueError, json.JSONDecodeError) as error:
        failure = {"schema_version": 1, "status": "failed", "error": str(error)}
        atomic_json(args.report, failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
