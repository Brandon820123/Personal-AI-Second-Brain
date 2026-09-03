"""Recursive, local-only discovery and change tracking for knowledge files."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "data" / "config" / "scanner.json"
DEFAULT_INDEX_PATH = PROJECT_ROOT / "data" / "config" / "file_index.json"
SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})
DEFAULT_IGNORED_FOLDERS = frozenset(
    {".git", "__pycache__", "pycache", "node_modules", "venv", ".venv", ".env"}
)
HASH_CHUNK_SIZE = 1024 * 1024
INDEX_VERSION = 1


class FileScannerError(RuntimeError):
    """Raised when scanner configuration or a requested root is invalid."""


def calculate_file_hash(file_path, chunk_size=HASH_CHUNK_SIZE):
    """Calculate a SHA-256 hash without loading the whole file into memory."""

    path = Path(file_path)
    digest = hashlib.sha256()

    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(chunk_size), b""):
            digest.update(chunk)

    return digest.hexdigest()


def build_file_info(file_path):
    """Build the stable metadata structure used by scanner consumers."""

    path = Path(file_path).expanduser().resolve()
    stat = path.stat()
    return {
        "path": path.as_posix(),
        "name": path.name,
        "extension": path.suffix.casefold(),
        "size": stat.st_size,
        "modified_time": stat.st_mtime,
        "hash": calculate_file_hash(path),
    }


def load_scanner_config(config_path=DEFAULT_CONFIG_PATH):
    """Load scanner settings and merge required safety exclusions."""

    path = Path(config_path)

    if not path.is_file():
        return {
            "watch_folders": [],
            "ignored_folders": sorted(DEFAULT_IGNORED_FOLDERS),
        }

    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FileScannerError(f"Could not read scanner configuration: {error}") from error

    if not isinstance(config, dict):
        raise FileScannerError("Scanner configuration must be a JSON object.")

    watch_folders = config.get("watch_folders", [])
    ignored_folders = config.get("ignored_folders", [])

    if not isinstance(watch_folders, list) or not isinstance(ignored_folders, list):
        raise FileScannerError(
            "Scanner watch_folders and ignored_folders must be JSON arrays."
        )

    merged_ignored = DEFAULT_IGNORED_FOLDERS | {
        str(folder).strip().casefold()
        for folder in ignored_folders
        if str(folder).strip()
    }
    return {
        "watch_folders": [str(folder).strip() for folder in watch_folders if str(folder).strip()],
        "ignored_folders": sorted(merged_ignored),
    }


def load_file_index(index_path=DEFAULT_INDEX_PATH):
    """Load the current index, accepting the early list-only schema as input."""

    path = Path(index_path)

    if not path.is_file():
        return _empty_index()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_index()

    if isinstance(data, list):
        data = {"files": data}

    if not isinstance(data, dict) or not isinstance(data.get("files", []), list):
        return _empty_index()

    return {
        "version": data.get("version", INDEX_VERSION),
        "last_scan_time": data.get("last_scan_time"),
        "scans": data.get("scans", {}) if isinstance(data.get("scans", {}), dict) else {},
        "files": [record for record in data.get("files", []) if isinstance(record, dict)],
    }


def save_file_index(index, index_path=DEFAULT_INDEX_PATH):
    """Persist scanner state atomically for scanner and sync consumers."""

    if not isinstance(index, dict) or not isinstance(index.get("files"), list):
        raise FileScannerError("File index must contain a files array.")

    path = Path(index_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        f"{json.dumps(index, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def scan_folder(
    folder_path,
    *,
    index_path=DEFAULT_INDEX_PATH,
    ignored_folders=None,
    save_index=True,
):
    """Recursively scan one root and classify new, modified, and unchanged files."""

    root = Path(folder_path).expanduser().resolve()

    if not root.exists():
        raise FileNotFoundError(f"Scan folder not found: {root}")

    if not root.is_dir():
        raise NotADirectoryError(f"Scan path is not a folder: {root}")

    ignored = DEFAULT_IGNORED_FOLDERS | {
        str(folder).strip().casefold()
        for folder in (ignored_folders or ())
        if str(folder).strip()
    }
    previous_index = load_file_index(index_path)
    previous_records = {
        _comparison_path(record.get("path", "")): record
        for record in previous_index["files"]
        if record.get("path") and _path_is_within(record["path"], root)
    }
    folders = []
    files = []
    scan_errors = []

    def record_walk_error(error):
        scan_errors.append(str(error))

    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=record_walk_error,
        followlinks=False,
    ):
        directory_names[:] = sorted(
            (
                name
                for name in directory_names
                if name.casefold() not in ignored
            ),
            key=str.casefold,
        )
        current_path = Path(current_root)

        for directory_name in directory_names:
            folders.append((current_path / directory_name).relative_to(root).as_posix())

        for file_name in sorted(file_names, key=str.casefold):
            file_path = current_path / file_name

            if file_path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                continue

            try:
                files.append(build_file_info(file_path))
            except (OSError, PermissionError) as error:
                scan_errors.append(f"{file_path}: {error}")

    files.sort(key=lambda record: record["path"].casefold())
    new_files = []
    modified_files = []
    unchanged_files = []
    indexed_records = []

    for record in files:
        previous = previous_records.get(_comparison_path(record["path"]))

        if previous is None:
            classification = new_files
            processed = False
        elif previous.get("hash") != record["hash"]:
            classification = modified_files
            processed = False
        else:
            classification = unchanged_files
            processed = bool(previous.get("processed", False))

        classification.append(record)
        indexed_record = {
            **record,
            "processed": processed,
            "knowledge_id": previous.get("knowledge_id") if previous else None,
            "last_indexed": previous.get("last_indexed") if previous else None,
        }

        indexed_records.append(indexed_record)

    current_paths = {_comparison_path(record["path"]) for record in files}
    removed_files = [
        record
        for comparison_path, record in previous_records.items()
        if comparison_path not in current_paths
    ]
    untouched_records = [
        record
        for record in previous_index["files"]
        if record.get("path") and not _path_is_within(record["path"], root)
    ]
    scanned_at = datetime.now(timezone.utc).isoformat()
    updated_index = {
        "version": INDEX_VERSION,
        "last_scan_time": scanned_at,
        "scans": {
            **previous_index["scans"],
            root.as_posix(): scanned_at,
        },
        "files": sorted(
            untouched_records + indexed_records,
            key=lambda record: str(record.get("path", "")).casefold(),
        ),
    }

    if save_index:
        save_file_index(updated_index, index_path)

    counts = {
        extension.lstrip(".").upper(): sum(
            record["extension"] == extension for record in files
        )
        for extension in sorted(SUPPORTED_EXTENSIONS)
    }
    return {
        "root": root.as_posix(),
        "scanned_at": scanned_at,
        "folders": sorted(folders, key=str.casefold),
        "files": files,
        "document_counts": counts,
        "new_files": new_files,
        "modified_files": modified_files,
        "unchanged_files": unchanged_files,
        "removed_files": removed_files,
        "errors": scan_errors,
    }


def scan_configured_folders(config_path=DEFAULT_CONFIG_PATH, index_path=DEFAULT_INDEX_PATH):
    """Scan every configured root while sharing one persistent file index."""

    config = load_scanner_config(config_path)
    return [
        scan_folder(
            folder,
            index_path=index_path,
            ignored_folders=config["ignored_folders"],
        )
        for folder in config["watch_folders"]
    ]


def format_scan_report(result):
    """Format a compact console report without coupling scanning to a UI."""

    lines = ["Scanning...", "", "Folders:"]
    lines.extend(f"- {folder}" for folder in result["folders"])

    if not result["folders"]:
        lines.append("- None")

    lines.extend(["", "Documents found:"])

    for extension, count in result["document_counts"].items():
        lines.append(f"{extension}: {count}")

    for heading, key in (
        ("New files", "new_files"),
        ("Modified files", "modified_files"),
        ("Unchanged", "unchanged_files"),
    ):
        lines.extend(["", f"{heading}:"])
        records = result[key]
        lines.extend(f"- {record['name']}" for record in records)

        if not records:
            lines.append("- None")

    return "\n".join(lines)


def _empty_index():
    return {
        "version": INDEX_VERSION,
        "last_scan_time": None,
        "scans": {},
        "files": [],
    }


def _comparison_path(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def _path_is_within(candidate, root):
    try:
        return os.path.commonpath(
            (_comparison_path(candidate), _comparison_path(root))
        ) == _comparison_path(root)
    except ValueError:
        return False
