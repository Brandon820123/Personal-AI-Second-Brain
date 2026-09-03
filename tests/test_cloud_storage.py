"""Tests for Supabase file operations and offline cache behavior."""

import io
import tempfile
import unittest
import os
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.cloud_storage import (
    CLOUD_UNAVAILABLE_WARNING,
    CloudStorage,
    CloudStorageError,
    cached_avatar_path,
    sync_cloud_cache,
)
from app.document_loader import load_document
from app.supabase_client import SupabaseRequestError, SupabaseStorageClient


class FakeResponse:
    def __init__(self, *, content=b"", json_data=None, status_code=200):
        self.content = content
        self._json_data = json_data
        self.status_code = status_code
        self.text = ""
        self.reason = "OK"

    def json(self):
        return self._json_data


class MemoryStorageClient:
    object_endpoint = staticmethod(SupabaseStorageClient.object_endpoint)

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.versions = {path: 1 for path in self.objects}
        self.downloads = []
        self.available = True

    def request(self, method, endpoint, **kwargs):
        if not self.available:
            raise SupabaseRequestError("network disabled")

        if endpoint.startswith("/storage/v1/object/list/"):
            return FakeResponse(json_data=self._list_entries(kwargs["json"]["prefix"]))

        bucket_marker = "/storage/v1/object/ai-files"

        if endpoint == bucket_marker and method == "DELETE":
            for path in kwargs["json"]["prefixes"]:
                self.objects.pop(path, None)
            return FakeResponse(json_data=[])

        encoded_path = endpoint[len(bucket_marker) :].strip("/")
        from urllib.parse import unquote

        path = unquote(encoded_path)

        if method == "GET":
            self.downloads.append(path)
            return FakeResponse(content=self.objects[path])

        if method == "POST":
            self.objects[path] = kwargs["data"].read()
            self.versions[path] = self.versions.get(path, 0) + 1
            return FakeResponse(status_code=201)

        raise AssertionError(f"Unexpected request: {method} {endpoint}")

    def _list_entries(self, prefix):
        prefix_parts = () if not prefix else PurePosixPath(prefix).parts
        entries = {}

        for path, content in self.objects.items():
            parts = PurePosixPath(path).parts

            if parts[: len(prefix_parts)] != prefix_parts or len(parts) <= len(prefix_parts):
                continue

            name = parts[len(prefix_parts)]

            if len(parts) > len(prefix_parts) + 1:
                entries[name] = {"name": name, "id": None, "metadata": None}
            else:
                entries[name] = {
                    "name": name,
                    "id": path,
                    "updated_at": f"version-{self.versions[path]}",
                    "metadata": {"size": len(content), "eTag": str(self.versions[path])},
                }

        return list(entries.values())


def pdf_bytes(text):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class CloudStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cache_root = Path(self.temporary_directory.name) / "cache"
        self.client = MemoryStorageClient(
            {
                "documents/reference.pdf": pdf_bytes("Cloud RAG document."),
                "avatars/fairy.png": b"fake-png",
                "config/persona.json": b"{}",
            }
        )
        self.storage = CloudStorage(self.client, cache_root=self.cache_root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_sync_restores_pdf_and_existing_loader_reads_cached_path(self):
        first = self.storage.sync_all()
        cached_pdf = self.cache_root / "documents" / "reference.pdf"

        self.assertEqual(first.downloaded_count, 3)
        self.assertTrue(cached_pdf.is_file())
        self.assertEqual(load_document(cached_pdf), "Cloud RAG document.")

        cached_pdf.unlink()
        restored = self.storage.sync_all()

        self.assertIn(cached_pdf, restored.downloaded)
        self.assertEqual(load_document(cached_pdf), "Cloud RAG document.")

    def test_unchanged_objects_reuse_cache_and_updated_objects_download(self):
        self.storage.sync_all()
        initial_download_count = len(self.client.downloads)

        unchanged = self.storage.sync_all()
        self.assertEqual(unchanged.downloaded_count, 0)
        self.assertEqual(len(self.client.downloads), initial_download_count)

        path = "documents/reference.pdf"
        self.client.objects[path] = pdf_bytes("Updated cloud document.")
        self.client.versions[path] += 1
        updated = self.storage.sync_all()

        self.assertEqual(updated.downloaded, [self.cache_root / "documents" / "reference.pdf"])
        self.assertEqual(load_document(updated.downloaded[0]), "Updated cloud document.")

    def test_file_operations_cover_upload_exists_download_and_delete(self):
        local_file = Path(self.temporary_directory.name) / "notes.txt"
        local_file.write_text("local upload", encoding="utf-8")

        self.storage.upload_file(local_file, "documents/notes.txt")
        self.assertTrue(self.storage.file_exists("documents/notes.txt"))
        downloaded = self.storage.download_file("documents/notes.txt")
        self.assertEqual(downloaded.read_text(encoding="utf-8"), "local upload")

        self.storage.delete_file("documents/notes.txt")
        self.assertFalse(self.storage.file_exists("documents/notes.txt"))
        self.assertFalse(downloaded.exists())

    def test_offline_sync_preserves_and_reports_local_cache(self):
        cached_document = self.cache_root / "documents" / "offline.md"
        cached_document.parent.mkdir(parents=True)
        cached_document.write_text("offline knowledge", encoding="utf-8")
        self.client.available = False

        result = sync_cloud_cache(client=self.client, cache_root=self.cache_root)

        self.assertFalse(result.available)
        self.assertIn(CLOUD_UNAVAILABLE_WARNING, result.warning)
        self.assertEqual(result.cached_files, [cached_document])
        self.assertEqual(load_document(cached_document), "offline knowledge")

    def test_missing_configuration_reports_warning_without_crashing(self):
        cached_document = self.cache_root / "documents" / "cached.txt"
        cached_document.parent.mkdir(parents=True)
        cached_document.write_text("still available", encoding="utf-8")

        with patch.dict(os.environ, {}, clear=True):
            result = sync_cloud_cache(cache_root=self.cache_root)

        self.assertFalse(result.configured)
        self.assertFalse(result.available)
        self.assertEqual(result.warning, CLOUD_UNAVAILABLE_WARNING)
        self.assertEqual(result.cached_files, [cached_document])

    def test_cached_avatar_and_path_traversal_guards(self):
        avatar = self.cache_root / "avatars" / "delamain.png"
        avatar.parent.mkdir(parents=True)
        avatar.write_bytes(b"avatar")

        self.assertEqual(cached_avatar_path("delamain.png", self.cache_root), avatar)

        with self.assertRaises(CloudStorageError):
            self.storage.cache_path("documents/../../secret.txt")


if __name__ == "__main__":
    unittest.main()
