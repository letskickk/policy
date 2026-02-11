import json
from pathlib import Path
from typing import Any, Tuple

from backend.config import ROOT_DIR

RAG_DIR = ROOT_DIR / ".rag"
REGISTRY_PATH = RAG_DIR / "registry.json"
POLICY_ID_PATH = RAG_DIR / "vector_store_id.txt"
REGIONAL_ID_PATH = RAG_DIR / "vector_store_regional_id.txt"
WINNERS2022_ID_PATH = RAG_DIR / "vector_store_winners2022_id.txt"


def load_registry() -> dict[str, Any]:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"vector_store_ids": {}, "files": {}, "path_index": {}}


def save_registry(registry: dict[str, Any]) -> None:
    RAG_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_id(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def get_vector_store_ids() -> Tuple[str, str, str]:
    """Returns (policy_id, regional_id, winners2022_id)."""
    registry = load_registry()
    ids = registry.get("vector_store_ids", {})
    policy = (ids.get("policy") or "").strip() or _read_id(POLICY_ID_PATH)
    regional = (ids.get("regional") or "").strip() or _read_id(REGIONAL_ID_PATH)
    winners2022 = (ids.get("winners2022") or "").strip() or _read_id(WINNERS2022_ID_PATH)
    return policy, regional, winners2022


def write_vector_store_ids(
    policy_id: str = "",
    regional_id: str = "",
    winners2022_id: str = "",
) -> None:
    registry = load_registry()
    registry.setdefault("vector_store_ids", {})
    if policy_id:
        registry["vector_store_ids"]["policy"] = policy_id
    if regional_id:
        registry["vector_store_ids"]["regional"] = regional_id
    if winners2022_id:
        registry["vector_store_ids"]["winners2022"] = winners2022_id
    save_registry(registry)

    RAG_DIR.mkdir(parents=True, exist_ok=True)
    if policy_id:
        POLICY_ID_PATH.write_text(policy_id + "\n", encoding="utf-8")
    if regional_id:
        REGIONAL_ID_PATH.write_text(regional_id + "\n", encoding="utf-8")
    if winners2022_id:
        WINNERS2022_ID_PATH.write_text(winners2022_id + "\n", encoding="utf-8")
