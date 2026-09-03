"""Tests for the storage-only Supabase HTTP client."""

import os
import unittest
from unittest.mock import patch

from app.supabase_client import (
    SupabaseConfigurationError,
    SupabaseStorageClient,
    create_supabase_client,
)


class FakeResponse:
    status_code = 200
    text = ""
    reason = "OK"


class RecordingSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse()


class SupabaseClientTests(unittest.TestCase):
    def test_missing_environment_is_optional_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(create_supabase_client())

            with self.assertRaises(SupabaseConfigurationError):
                create_supabase_client(required=True)

    def test_request_uses_storage_auth_without_exposing_it_in_url(self):
        session = RecordingSession()
        client = SupabaseStorageClient(
            "https://example.supabase.co/",
            "private-test-key",
            session=session,
        )

        client.request("GET", "/storage/v1/object/ai-files/documents/a.pdf")

        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            url,
            "https://example.supabase.co/storage/v1/object/ai-files/documents/a.pdf",
        )
        self.assertEqual(kwargs["headers"]["apikey"], "private-test-key")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer private-test-key")
        self.assertNotIn("private-test-key", url)

    def test_object_paths_are_url_encoded_but_keep_folders(self):
        endpoint = SupabaseStorageClient.object_endpoint(
            "ai-files",
            "documents/用户 notes.pdf",
        )

        self.assertEqual(
            endpoint,
            "/storage/v1/object/ai-files/documents/%E7%94%A8%E6%88%B7%20notes.pdf",
        )


if __name__ == "__main__":
    unittest.main()
