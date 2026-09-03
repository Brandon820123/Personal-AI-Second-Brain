"""Supabase Storage access with an offline-first local file cache."""

import json
import mimetypes
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

try:
    from .supabase_client import (
        SupabaseRequestError,
        create_supabase_client,
    )
except ImportError:
    from supabase_client import SupabaseRequestError, create_supabase_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = PROJECT_ROOT / "data" / "cache"
DEFAULT_BUCKET = "ai-files"
CLOUD_PREFIXES = ("documents", "avatars", "config")
CLOUD_UNAVAILABLE_WARNING = "Cloud storage unavailable. Using local cache."
MANIFEST_FILENAME = ".cloud_manifest.json"
LIST_PAGE_SIZE = 100


class CloudStorageError(RuntimeError):
    """Raised for invalid paths or unsuccessful cloud file operations."""


@dataclass(frozen=True)
class CloudFile:
    """Metadata for one object in the configured Storage bucket."""

    path: str
    size: int = 0
    updated_at: str = ""
    etag: str = ""

    @property
    def fingerprint(self):
        return {
            "size": self.size,
            "updated_at": self.updated_at,
            "etag": self.etag,
        }


@dataclass
class CacheSyncResult:
    """Summary of a non-destructive cloud-to-cache synchronization."""

    configured: bool
    available: bool
    downloaded: list = field(default_factory=list)
    reused: list = field(default_factory=list)
    cached_files: list = field(default_factory=list)
    warning: str = ""

    @property
    def downloaded_count(self):
        return len(self.downloaded)

    @property
    def cached_count(self):
        return len(self.cached_files)


def normalize_remote_path(remote_path):
    """Return a safe bucket-relative POSIX path."""

    raw_path = str(remote_path).replace("\\", "/").strip("/")
    path = PurePosixPath(raw_path)

    if not raw_path or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise CloudStorageError(f"Invalid cloud object path: {remote_path!r}")

    return path.as_posix()


class CloudStorage:
    """File operations and incremental synchronization for one Storage bucket."""

    def __init__(self, client, bucket=DEFAULT_BUCKET, cache_root=DEFAULT_CACHE_ROOT):
        if client is None:
            raise CloudStorageError("A configured Supabase client is required.")

        self.client = client
        self.bucket = str(bucket).strip() or DEFAULT_BUCKET
        self.cache_root = Path(cache_root).resolve()
        self.manifest_path = self.cache_root / MANIFEST_FILENAME
        self._manifest_lock = threading.RLock()

    def cache_path(self, remote_path):
        normalized_path = normalize_remote_path(remote_path)
        candidate = (self.cache_root / Path(*PurePosixPath(normalized_path).parts)).resolve()

        if candidate != self.cache_root and self.cache_root not in candidate.parents:
            raise CloudStorageError(f"Cloud path escapes the cache: {remote_path!r}")

        return candidate

    def list_files(self, prefix=""):
        normalized_prefix = ""

        if str(prefix).strip("/\\"):
            normalized_prefix = normalize_remote_path(prefix)

        files = []
        pending_folders = [normalized_prefix]

        while pending_folders:
            folder = pending_folders.pop(0)
            offset = 0

            while True:
                response = self.client.request(
                    "POST",
                    f"/storage/v1/object/list/{self.bucket}",
                    json={
                        "prefix": folder,
                        "limit": LIST_PAGE_SIZE,
                        "offset": offset,
                        "sortBy": {"column": "name", "order": "asc"},
                    },
                )

                try:
                    entries = response.json()
                except ValueError as error:
                    raise CloudStorageError("Supabase returned an invalid file listing.") from error

                if not isinstance(entries, list):
                    raise CloudStorageError("Supabase returned an invalid file listing.")

                for entry in entries:
                    name = str(entry.get("name", "")).strip("/\\")

                    if not name:
                        continue

                    remote_path = f"{folder}/{name}" if folder else name
                    metadata = entry.get("metadata") or {}
                    is_folder = entry.get("id") is None and not metadata

                    if is_folder:
                        pending_folders.append(normalize_remote_path(remote_path))
                        continue

                    files.append(
                        CloudFile(
                            path=normalize_remote_path(remote_path),
                            size=int(metadata.get("size") or entry.get("size") or 0),
                            updated_at=str(
                                entry.get("updated_at")
                                or entry.get("updatedAt")
                                or ""
                            ),
                            etag=str(
                                metadata.get("eTag")
                                or metadata.get("etag")
                                or entry.get("etag")
                                or ""
                            ).strip('"'),
                        )
                    )

                if len(entries) < LIST_PAGE_SIZE:
                    break

                offset += LIST_PAGE_SIZE

        return sorted(files, key=lambda cloud_file: cloud_file.path.casefold())

    def upload_file(self, local_path, remote_path, overwrite=True):
        source = Path(local_path)

        if not source.is_file():
            raise FileNotFoundError(f"Upload file not found: {source}")

        normalized_path = normalize_remote_path(remote_path)
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        headers = {"Content-Type": content_type}

        if overwrite:
            headers["x-upsert"] = "true"

        with source.open("rb") as source_file:
            self.client.request(
                "POST",
                self.client.object_endpoint(self.bucket, normalized_path),
                data=source_file,
                headers=headers,
                expected_statuses=(200, 201),
            )

        return normalized_path

    def download_file(self, remote_path, destination=None):
        normalized_path = normalize_remote_path(remote_path)
        target = Path(destination).resolve() if destination else self.cache_path(normalized_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_target = target.with_name(f".{target.name}.download")
        response = self.client.request(
            "GET",
            self.client.object_endpoint(self.bucket, normalized_path),
            stream=True,
        )

        try:
            with temporary_target.open("wb") as target_file:
                if hasattr(response, "iter_content"):
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            target_file.write(chunk)
                else:
                    target_file.write(response.content)

            temporary_target.replace(target)
        finally:
            if temporary_target.exists():
                temporary_target.unlink()

        return target

    def delete_file(self, remote_path, remove_cached_copy=True):
        normalized_path = normalize_remote_path(remote_path)
        self.client.request(
            "DELETE",
            f"/storage/v1/object/{self.bucket}",
            json={"prefixes": [normalized_path]},
            expected_statuses=(200,),
        )

        if remove_cached_copy:
            cached_path = self.cache_path(normalized_path)

            if cached_path.is_file():
                cached_path.unlink()

            manifest = self._load_manifest()
            manifest.pop(normalized_path, None)
            self._save_manifest(manifest)

    def file_exists(self, remote_path):
        normalized_path = normalize_remote_path(remote_path)
        path = PurePosixPath(normalized_path)
        parent = "" if str(path.parent) == "." else path.parent.as_posix()
        return any(cloud_file.path == normalized_path for cloud_file in self.list_files(parent))

    def sync_prefix(self, prefix):
        normalized_prefix = normalize_remote_path(prefix)
        manifest = self._load_manifest()
        downloaded = []
        reused = []

        for cloud_file in self.list_files(normalized_prefix):
            target = self.cache_path(cloud_file.path)

            if target.is_file() and manifest.get(cloud_file.path) == cloud_file.fingerprint:
                reused.append(target)
                continue

            downloaded.append(self.download_file(cloud_file.path, target))
            manifest[cloud_file.path] = cloud_file.fingerprint

        self._save_manifest(manifest)
        return downloaded, reused

    def sync_all(self, prefixes=CLOUD_PREFIXES):
        downloaded = []
        reused = []

        for prefix in prefixes:
            prefix_downloaded, prefix_reused = self.sync_prefix(prefix)
            downloaded.extend(prefix_downloaded)
            reused.extend(prefix_reused)

        return CacheSyncResult(
            configured=True,
            available=True,
            downloaded=downloaded,
            reused=reused,
            cached_files=list_cached_files(self.cache_root),
        )

    def _load_manifest(self):
        with self._manifest_lock:
            if not self.manifest_path.is_file():
                return {}

            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

            return data if isinstance(data, dict) else {}

    def _save_manifest(self, manifest):
        with self._manifest_lock:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            temporary_path = self.manifest_path.with_suffix(".tmp")
            temporary_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary_path.replace(self.manifest_path)


def list_cached_files(cache_root=DEFAULT_CACHE_ROOT):
    """List usable cached files without including cache bookkeeping."""

    root = Path(cache_root)

    if not root.is_dir():
        return []

    return sorted(
        (
            path
            for prefix in CLOUD_PREFIXES
            for path in (root / prefix).rglob("*")
            if path.is_file()
        ),
        key=lambda path: str(path).casefold(),
    )


def cached_avatar_path(filename, cache_root=DEFAULT_CACHE_ROOT):
    """Return a cached avatar path when the cloud copy is available."""

    path = Path(cache_root) / "avatars" / Path(filename).name
    return path if path.is_file() else None


def sync_cloud_cache(client=None, cache_root=DEFAULT_CACHE_ROOT, bucket=DEFAULT_BUCKET):
    """Synchronize cloud files, degrading to the existing cache on any outage."""

    configured_client = client or create_supabase_client(required=False)

    if configured_client is None:
        return CacheSyncResult(
            configured=False,
            available=False,
            cached_files=list_cached_files(cache_root),
            warning=CLOUD_UNAVAILABLE_WARNING,
        )

    try:
        return CloudStorage(
            configured_client,
            bucket=bucket,
            cache_root=cache_root,
        ).sync_all()
    except (CloudStorageError, SupabaseRequestError, OSError) as error:
        return CacheSyncResult(
            configured=True,
            available=False,
            cached_files=list_cached_files(cache_root),
            warning=f"{CLOUD_UNAVAILABLE_WARNING} {error}",
        )
