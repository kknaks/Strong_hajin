"""Local-only, fixed-manifest corpus evaluation. Stdout contains aggregate/hash metadata only.

manifest --output /tmp/.../manifest.json: select authorized notes without copying their text.
run --manifest /tmp/.../manifest.json --output /tmp/.../metrics.json: ephemeral DB/storage, no provider.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir
from time import perf_counter

ROOTS = {
    "work_brief": "00_Inbox/Work Briefs",
    "literature": "01_Notes/01_Literature",
    "permanent": "01_Notes/02_Permanent",
    "resource": "02_PARA/03_Resources",
}
EXCLUDED = re.compile(r"private|people|daily|tracking|credential|secret|api[ _-]?key|password|token|profile|personal|계좌|비밀번호|개인|회고|일기|프로필|연락처", re.I)
CONTENT_EXCLUDED = re.compile(r"credential|password|api[ _-]?key|client[ _-]?secret|access[ _-]?token|bearer\s|BEGIN .*PRIVATE KEY|\b(?:sk-|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|\bAKIA[A-Z0-9]{16}\b|비밀번호|계좌번호|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.I)
MAX_BYTES = 1_048_576
WORD = re.compile(r"(?<![가-힣])[가-힣]{3,12}(?![가-힣])")


def digest(value: bytes) -> str:
    return sha256(value).hexdigest()


def git_status_hash(root: Path) -> str:
    branch = subprocess.run(["git", "-C", str(root), "branch", "--show-current"], capture_output=True, check=True).stdout.strip()
    if branch != b"main":
        raise ValueError("vault must be the main checkout")
    return digest(subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z"], capture_output=True, check=True).stdout)


def allowed_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() != ".md":
        raise ValueError("path is outside the manifest allowlist")
    if not any(path.is_relative_to(base) for base in ROOTS.values()):
        raise ValueError("path is outside the manifest allowlist")
    if any(part.startswith(".") or EXCLUDED.search(part) for part in path.parts):
        raise ValueError("excluded path")
    candidate = root / path
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents) if parent != root and parent.is_relative_to(root)):
        raise ValueError("symlink is excluded")
    if not candidate.is_file() or not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("source is unavailable")
    return candidate


def public_manifest_summary(manifest: dict) -> dict:
    sizes = sorted(row["bytes"] for row in manifest["entries"])
    return {"profile": "scax-material-corpus-v1", "phase": "manifest", "files": len(sizes),
            "source_classes": dict(Counter(row["source_class"] for row in manifest["entries"])),
            "bytes": sum(sizes), "min_bytes": sizes[0] if sizes else 0,
            "median_bytes": sizes[len(sizes)//2] if sizes else 0, "max_bytes": sizes[-1] if sizes else 0,
            "excluded": manifest["excluded"],
            "corpus_hash": digest(json.dumps([(row["corpus_id"], row["sha256"]) for row in manifest["entries"]], sort_keys=True).encode())}


def build_manifest(root: Path, *, per_class: int = 24) -> dict:
    root = root.resolve()
    before = git_status_hash(root)
    entries, excluded = [], Counter()
    for kind, base in ROOTS.items():
        candidates = []
        for path in (root / base).rglob("*.md"):
            relative = path.relative_to(root).as_posix()
            try:
                path = allowed_path(root, relative)
            except ValueError:
                excluded["path"] += 1
                continue
            size = path.stat().st_size
            if not 0 < size <= MAX_BYTES:
                excluded["size"] += 1
                continue
            candidates.append((relative, size))
        large = sorted(candidates, key=lambda row: (-row[1], row[0]))[:max(1, per_class//3)]
        rest = sorted((row for row in candidates if row not in large), key=lambda row: digest(row[0].encode()))
        selected = 0
        for relative, size in [*large, *rest]:
            if selected >= per_class:
                break
            data = allowed_path(root, relative).read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                excluded["encoding"] += 1
                continue
            if CONTENT_EXCLUDED.search(text):
                excluded["content_candidate"] += 1
                continue
            entries.append({"path": relative, "source_class": kind, "corpus_id": digest(relative.encode())[:20],
                            "bytes": len(data), "sha256": digest(data)})
            selected += 1
    if before != git_status_hash(root):
        raise ValueError("vault status changed during manifest preparation")
    return {"format_version": 1, "root": str(root), "vault_status_sha256": before,
            "entries": entries, "excluded": dict(excluded), "selection": "largest-third then deterministic path hash; conservative exclusions"}


def read_manifest_sources(manifest: dict) -> list[tuple[dict, bytes]]:
    if manifest.get("format_version") != 1:
        raise ValueError("unknown manifest format")
    root = Path(manifest["root"])
    git_status_hash(root)
    sources, seen = [], set()
    for row in manifest["entries"]:
        path = allowed_path(root, row["path"])
        if row["path"] in seen or row["source_class"] not in ROOTS or not Path(row["path"]).is_relative_to(ROOTS[row["source_class"]]):
            raise ValueError("invalid manifest entry")
        seen.add(row["path"])
        if not 0 < path.stat().st_size <= MAX_BYTES:
            raise ValueError("source changed")
        data = path.read_bytes()
        if digest(data) != row["sha256"] or len(data) != row["bytes"]:
            raise ValueError("source changed")
        if CONTENT_EXCLUDED.search(data.decode("utf-8")):
            raise ValueError("excluded content candidate")
        sources.append((row, data))
    if not sources:
        raise ValueError("manifest is empty")
    return sources


def query_cases(sources: list[tuple[dict, bytes]]) -> list[dict]:
    documents = {row["corpus_id"]: data.decode("utf-8") for row, data in sources}
    terms = defaultdict(set)
    for identifier, text in documents.items():
        for word in set(WORD.findall(text)):
            terms[word].add(identifier)
    cases = []
    for identifier in documents:
        unique = [word for word, ids in terms.items() if ids == {identifier}]
        if unique:
            term = min(unique, key=lambda word: digest(word.encode()))
            cases.append({"kind": "unique_korean", "query": term, "expected": {identifier}})
    ambiguous = sorted((word for word, ids in terms.items() if 3 <= len(ids) <= max(3, len(documents)//2)), key=lambda word: digest(word.encode()))[:12]
    cases.extend({"kind": "ambiguous_korean", "query": word, "expected": terms[word]} for word in ambiguous)
    for literal, alternate in (("권한", "인가"), ("추출", "파싱"), ("업무", "작업"), ("검색", "탐색")):
        expected = {identifier for identifier, text in documents.items() if literal in text}
        if expected:
            cases.append({"kind": "synonym_probe", "query": alternate, "expected": expected})
    dates = defaultdict(set)
    for identifier, text in documents.items():
        for date in set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text)):
            dates[date].add(identifier)
    cases.extend({"kind": "mentioned_date", "query": date, "expected": dates[date]} for date in sorted(dates)[:6])
    cases.append({"kind": "no_result", "query": "scaxabsentcorpusphraseunmatched", "expected": set()})
    return cases


class NoExternalProvider:
    def generate(self, *args, **kwargs):
        raise RuntimeError("external providers are disabled for this profile")
    def converse(self, *args, **kwargs):
        raise RuntimeError("external providers are disabled for this profile")


def evaluate(manifest: dict) -> dict:
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select
    from ax_workspace.bootstrap.material_worker import MaterialExtractionWorker
    from ax_workspace.bootstrap.settings import RuntimeProfile, Settings
    from ax_workspace.entrypoints.http import create_app
    from ax_workspace.entrypoints.reset_demo import reset_database
    from ax_workspace.platform.persistence import MaterialChunkRecord, MaterialExtractionRecord, make_session_factory

    sources = read_manifest_sources(manifest)
    root = Path(manifest["root"])
    before = git_status_hash(root)
    result = {**public_manifest_summary(manifest), "phase": "local_evaluation", "provider_calls": 0, "database": "ephemeral_sqlite"}
    with TemporaryDirectory(prefix="scax-corpus-eval-") as temporary:
        work = Path(temporary)
        database_url = f"sqlite:///{work / 'demo.db'}"
        reset_database(database_url)
        settings = Settings(RuntimeProfile.TEST, database_url, materials_dir=str(work / "materials"), recordings_dir=str(work / "recordings"))
        app = create_app(settings, report_provider=NoExternalProvider())
        application = app.state.workflow_application
        client = TestClient(app)
        principal = application.authenticated_principal("mina")
        headers = {"X-Demo-Persona": "mina"}
        folders = {}
        for kind in ("personal", "team"):
            response = client.post("/api/material-folders", headers=headers, json={"kind": kind, "title": f"corpus-{kind}", **({"organization_id": "product"} if kind == "team" else {})})
            if response.status_code != 201:
                raise ValueError("folder setup failed")
            folders[kind] = response.json()["folder_id"]
        task = client.post("/api/tasks", headers=headers, json={"title": "corpus-task"}).json()["task_id"]
        owners = ("personal_folder", "team_folder", "task")
        artifacts = {}
        began = perf_counter()
        for index, (row, data) in enumerate(sources):
            owner = owners[index % len(owners)]
            filename = f"corpus-{row['corpus_id']}.md"
            if owner == "task":
                response = client.post(f"/api/tasks/{task}/materials", headers=headers, data={"kind": "input"}, files={"file": (filename, data, "text/markdown")})
                key = "attachment_id"
            else:
                folder = folders[owner.removesuffix("_folder")]
                response = client.post(f"/api/material-folders/{folder}/materials", headers=headers, files={"file": (filename, data, "text/markdown")})
                key = "material_id"
            if response.status_code != 201:
                raise ValueError("corpus upload failed")
            artifacts[response.json()[key]] = {**row, "owner": owner}
        result["load_seconds"] = round(perf_counter() - began, 3)
        began = perf_counter()
        worker = MaterialExtractionWorker(settings, queue_factory=lambda session: application.memory_job_queue)
        async def drain():
            while await worker.run_once():
                pass
        asyncio.run(drain())
        result["extract_seconds"] = round(perf_counter() - began, 3)
        sessions = make_session_factory(database_url)
        with sessions() as session:
            result["chunks"] = session.scalar(select(func.count()).select_from(MaterialChunkRecord))
        result["owners"] = dict(Counter(row["owner"] for row in artifacts.values()))
        metrics = []
        source_text = {row["corpus_id"]: data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n") for row, data in sources}
        locator_checks, locator_failures, source_span_failures = 0, 0, 0
        for case in query_cases(sources):
            times = []
            for _ in range(2):
                began = perf_counter()
                found = application.search_materials(principal, case["query"], limit=8)
                times.append(round((perf_counter() - began) * 1000, 3))
            hits = found["results"]
            ids = [artifacts[hit["material_id"]]["corpus_id"] for hit in hits]
            rank = next((rank for rank, identifier in enumerate(ids, 1) if identifier in case["expected"]), None)
            with sessions() as session:
                from uuid import UUID
                for hit in hits:
                    chunk = session.get(MaterialChunkRecord, UUID(hit["chunk_id"]))
                    locator = hit["source_locator"]
                    locator_checks += 1
                    if chunk is None or locator["char_start"] != chunk.char_start or locator["char_end"] != chunk.char_end or hit["integrity_ref"] != "sha256:" + artifacts[hit["material_id"]]["sha256"]:
                        locator_failures += 1
                    if chunk is not None:
                        original = source_text[artifacts[hit["material_id"]]["corpus_id"]]
                        original_span = original[locator["char_start"]:locator["char_end"]].strip()
                        if original_span != chunk.text or hit["excerpt"].strip("…") not in " ".join(original_span.split()):
                            source_span_failures += 1
            relevant = len(set(ids) & case["expected"])
            selected_recovery = None
            projection_contains_query = None
            query_index_overlap = None
            if case["kind"] == "unique_korean" and rank is None:
                from uuid import UUID
                target = next(identifier for identifier, row in artifacts.items() if row["corpus_id"] in case["expected"])
                selected_recovery = bool(application.search_materials(principal, case["query"], material_id=UUID(target))["results"])
                if not selected_recovery:
                    from ax_workspace.modules.work.material_extraction import query_tokens
                    with sessions() as session:
                        chunks = list(session.scalars(select(MaterialChunkRecord).join(MaterialExtractionRecord).where(MaterialExtractionRecord.attachment_id == UUID(target))))
                    projection_contains_query = any(case["query"] in chunk.text for chunk in chunks)
                    indexed_tokens = {word for chunk in chunks for word in (chunk.search_text or "").split()}
                    query_index_overlap = len(set(query_tokens(case["query"])) & indexed_tokens)
            metrics.append({"query_id": digest(case["query"].encode())[:20], "kind": case["kind"],
                "expected_count": len(case["expected"]), "expected_source_classes": sorted({row["source_class"] for row, _ in sources if row["corpus_id"] in case["expected"]}),
                "rank": rank, "relevant_hits": relevant, "hit_count": len(hits), "latency_ms": times, "selected_recovery": selected_recovery, "projection_contains_query": projection_contains_query, "query_index_overlap": query_index_overlap,
                "recall_at_8": round(relevant / len(case["expected"]), 4) if case["expected"] else None,
                "searched_materials": found["searched_materials"], "unavailable_materials_count": found["unavailable_materials_count"]})
        result["queries"] = metrics
        result["locator_checks"] = locator_checks
        result["locator_failures"] = locator_failures
        result["source_span_failures"] = source_span_failures
        denied = application.search_materials(application.authenticated_principal("sora"), "검색")
        result["unauthorized_metadata_absent"] = denied["searched_materials"] == denied["unavailable_materials_count"] == len(denied["results"]) == 0
        for member, allowed_owners in (("jiho", {"team_folder"}), ("mina", set(owners))):
            found = application.search_materials(application.authenticated_principal(member), "검색", limit=8)
            result[f"{member}_searched_materials"] = found["searched_materials"]
            expected_materials = sum(row["owner"] in allowed_owners for row in artifacts.values())
            result[f"{member}_owner_scope_valid"] = (found["searched_materials"] == expected_materials
                and found["unavailable_materials_count"] == 0
                and all(artifacts[hit["material_id"]]["owner"] in allowed_owners for hit in found["results"]))
    read_manifest_sources(manifest)
    result["source_hashes_unchanged"] = True
    result["vault_status_unchanged"] = before == git_status_hash(root)
    result["temporary_data_removed"] = not work.exists()
    result["correctness_pass"] = (result["source_hashes_unchanged"] and result["vault_status_unchanged"]
        and result["temporary_data_removed"] and result["unauthorized_metadata_absent"]
        and result["mina_owner_scope_valid"] and result["jiho_owner_scope_valid"]
        and result["locator_checks"] > 0 and result["locator_failures"] == result["source_span_failures"] == 0
        and all(row["searched_materials"] == len(sources) and row["unavailable_materials_count"] == 0 for row in metrics)
        and all(row["hit_count"] == 0 for row in metrics if row["kind"] == "no_result"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    select_parser = sub.add_parser("manifest")
    select_parser.add_argument("--root", type=Path, default=Path.home() / "git/Main")
    select_parser.add_argument("--per-class", type=int, default=24)
    select_parser.add_argument("--output", type=Path, required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        output = options.output.resolve()
        if not output.is_relative_to(Path(gettempdir()).resolve()):
            raise ValueError("profile output must stay in the OS temporary directory")
        if options.output.exists():
            raise ValueError("output must be a new file")
        if options.command == "manifest":
            if not 1 <= options.per_class <= 64:
                raise ValueError("per-class must be 1 to 64")
            payload = build_manifest(options.root, per_class=options.per_class)
            summary = public_manifest_summary(payload)
        else:
            payload = evaluate(json.loads(options.manifest.read_text()))
            summary = {key: value for key, value in payload.items() if key != "queries"}
            summary["query_count"] = len(payload["queries"])
        options.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if payload.get("correctness_pass", True) else 1
    except Exception as error:
        # SQL/decoder errors can contain source values. Never print exception text or a traceback.
        print(json.dumps({"phase": options.command, "error_type": type(error).__name__, "status": "failed"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
