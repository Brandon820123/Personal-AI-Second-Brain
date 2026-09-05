"""Connect local file discovery to the existing knowledge import pipeline."""

import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from .file_scanner import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_INDEX_PATH,
        load_file_index,
        load_scanner_config,
        save_file_index,
        scan_folder,
    )
except ImportError:
    from file_scanner import (
        DEFAULT_CONFIG_PATH,
        DEFAULT_INDEX_PATH,
        load_file_index,
        load_scanner_config,
        save_file_index,
        scan_folder,
    )


def sync_new_documents(
    *,
    config_path=DEFAULT_CONFIG_PATH,
    index_path=DEFAULT_INDEX_PATH,
    watch_folders=None,
    scan_function=scan_folder,
    import_function=None,
    reindex_function=None,
    list_documents_function=None,
    log_function=print,
):
    """Scan configured roots and import each pending document independently."""

    if import_function is None or reindex_function is None:
        try:
            from .ai_service import import_document, reindex_library_document
        except ImportError:
            from ai_service import import_document, reindex_library_document

        import_function = import_function or import_document
        reindex_function = reindex_function or reindex_library_document

    if list_documents_function is None:
        try:
            from .knowledge_library import list_documents
        except ImportError:
            from knowledge_library import list_documents

        list_documents_function = list_documents

    config = load_scanner_config(config_path)
    roots = list(watch_folders) if watch_folders is not None else config["watch_folders"]
    summary = {
        "scanned_folders": [],
        "imported": [],
        "reindexed": [],
        "skipped": [],
        "failed": [],
        "scan_results": [],
    }
    log_function("Knowledge Sync Started")

    try:
        known_documents = list_documents_function()
    except Exception as error:
        known_documents = []
        log_function(f"Knowledge library lookup warning: {error}")

    for root in roots:
        root_path = Path(root).expanduser().resolve()
        log_function("")
        log_function("Scanning:")
        log_function(root_path.as_posix())

        try:
            scan_result = scan_function(
                root_path,
                index_path=index_path,
                ignored_folders=config["ignored_folders"],
            )
        except Exception as error:
            failure = {"path": root_path.as_posix(), "error": str(error), "stage": "scan"}
            summary["failed"].append(failure)
            log_function(f"Scan failed: {error}")
            continue

        summary["scanned_folders"].append(root_path.as_posix())
        summary["scan_results"].append(scan_result)

        for scan_error in scan_result.get("errors", []):
            summary["failed"].append(
                {"path": root_path.as_posix(), "error": scan_error, "stage": "scan"}
            )
            log_function(f"Scan warning: {scan_error}")

        index = load_file_index(index_path)
        indexed_by_path = {
            _comparison_path(record.get("path", "")): record
            for record in index["files"]
            if record.get("path")
        }
        candidates = []

        for record in scan_result["new_files"]:
            candidates.append(("new", record))

        for record in scan_result["modified_files"]:
            candidates.append(("modified", record))

        for record in scan_result["unchanged_files"]:
            indexed_record = indexed_by_path.get(_comparison_path(record["path"]), {})

            if indexed_record.get("processed", False):
                summary["skipped"].append(record)
            else:
                candidates.append(("pending", record))

        for position, (change_type, record) in enumerate(candidates, start=1):
            indexed_record = indexed_by_path.get(_comparison_path(record["path"]), {})
            existing_document = _find_existing_document(
                record,
                indexed_record,
                known_documents,
            )
            relative_path = _relative_display_path(record["path"], root_path)

            if change_type == "new":
                log_function("")
                log_function("New document detected:")
            elif change_type == "modified":
                log_function("")
                log_function("Modified document detected:")
            else:
                log_function("")
                log_function("Pending document retry:")

            log_function(relative_path)
            log_function("Processing...")
            log_function(f"Progress: {position} / {len(candidates)}")

            try:
                progress = lambda message: log_function(f"  {message}")

                if existing_document is not None:
                    operation_result = reindex_function(
                        existing_document,
                        on_progress=progress,
                    )
                    action = "reindexed"
                else:
                    operation_result = import_function(
                        record["path"],
                        on_progress=progress,
                    )
                    action = "imported"

                known_documents = list_documents_function()
                stored_document = _find_existing_document(record, {}, known_documents)

                if stored_document is None or not stored_document.get("source_id"):
                    raise RuntimeError(
                        "The document import completed but no knowledge record was found."
                    )

                indexed_at = datetime.now(timezone.utc).isoformat()
                _update_index_record(
                    record["path"],
                    index_path=index_path,
                    processed=True,
                    knowledge_id=stored_document["source_id"],
                    last_indexed=indexed_at,
                    last_error=None,
                    scan_status="indexed",
                )
                indexed_by_path[_comparison_path(record["path"])] = {
                    **indexed_record,
                    "processed": True,
                    "knowledge_id": stored_document["source_id"],
                    "last_indexed": indexed_at,
                }
                completed = {
                    **record,
                    "knowledge_id": stored_document["source_id"],
                    "result": operation_result,
                }
                summary[action].append(completed)
                log_function("✓ Loaded")
                log_function("✓ Chunked")
                log_function("✓ Embedded")
                log_function(
                    "✓ Re-indexed in knowledge base"
                    if action == "reindexed"
                    else "✓ Added to knowledge base"
                )
            except Exception as error:
                try:
                    _update_index_record(
                        record["path"],
                        index_path=index_path,
                    processed=False,
                    last_error=str(error),
                    scan_status="failed",
                    )
                except Exception as index_error:
                    log_function(f"Index status warning: {index_error}")

                failure = {
                    **record,
                    "change_type": change_type,
                    "error": str(error),
                    "stage": "index",
                }
                summary["failed"].append(failure)
                log_function(f"✗ Failed: {error}")

    log_function("")
    log_function("Sync completed.")
    log_function(
        f"Imported: {len(summary['imported'])}, "
        f"re-indexed: {len(summary['reindexed'])}, "
        f"unchanged: {len(summary['skipped'])}, "
        f"failed: {len(summary['failed'])}."
    )
    return summary


def _find_existing_document(record, indexed_record, documents):
    knowledge_id = indexed_record.get("knowledge_id")

    if knowledge_id:
        by_id = next(
            (
                document
                for document in documents
                if document.get("source_id") == knowledge_id
            ),
            None,
        )

        if by_id is not None:
            return by_id

    record_path = _comparison_path(record["path"])
    return next(
        (
            document
            for document in documents
            if document.get("source_path")
            and _comparison_path(document["source_path"]) == record_path
        ),
        None,
    )


def _update_index_record(path, *, index_path, **updates):
    index = load_file_index(index_path)
    comparison_path = _comparison_path(path)
    record = next(
        (
            candidate
            for candidate in index["files"]
            if candidate.get("path")
            and _comparison_path(candidate["path"]) == comparison_path
        ),
        None,
    )

    if record is None:
        raise RuntimeError(f"Scanned file is missing from the file index: {path}")

    for field, value in updates.items():
        if value is None:
            record.pop(field, None)
        else:
            record[field] = value

    save_file_index(index, index_path)


def _relative_display_path(path, root):
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).name


def _comparison_path(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))
