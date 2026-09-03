"""Small, storage-only Supabase REST client.

The project intentionally does not initialize the Supabase database SDK here.
Only the Storage HTTP API needed by the local cache is exposed.
"""

import os
from dataclasses import dataclass
from urllib.parse import quote

import requests


DEFAULT_TIMEOUT_SECONDS = 20


class SupabaseConfigurationError(RuntimeError):
    """Raised when required Supabase environment variables are unavailable."""


class SupabaseRequestError(RuntimeError):
    """Raised when the Supabase Storage API cannot complete a request."""


@dataclass(frozen=True)
class SupabaseSettings:
    """Connection settings loaded without persisting any secrets."""

    url: str
    key: str

    @classmethod
    def from_environment(cls):
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        key = os.getenv("SUPABASE_KEY", "").strip()

        if not url or not key:
            raise SupabaseConfigurationError(
                "SUPABASE_URL and SUPABASE_KEY are required for cloud storage."
            )

        return cls(url=url, key=key)


class SupabaseStorageClient:
    """Authenticated HTTP client for Supabase Storage object endpoints."""

    def __init__(self, url, key, session=None, timeout=DEFAULT_TIMEOUT_SECONDS):
        self.url = str(url).strip().rstrip("/")
        self.key = str(key).strip()
        self.session = session or requests.Session()
        self.timeout = timeout

        if not self.url or not self.key:
            raise SupabaseConfigurationError(
                "SUPABASE_URL and SUPABASE_KEY are required for cloud storage."
            )

    @classmethod
    def from_environment(cls, session=None, timeout=DEFAULT_TIMEOUT_SECONDS):
        settings = SupabaseSettings.from_environment()
        return cls(settings.url, settings.key, session=session, timeout=timeout)

    @property
    def headers(self):
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }

    def request(self, method, endpoint, *, expected_statuses=(200,), **kwargs):
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}))

        try:
            response = self.session.request(
                method,
                f"{self.url}{endpoint}",
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as error:
            raise SupabaseRequestError(f"Supabase Storage request failed: {error}") from error

        if response.status_code not in expected_statuses:
            detail = response.text.strip() or response.reason or "unknown error"
            raise SupabaseRequestError(
                f"Supabase Storage returned HTTP {response.status_code}: {detail}"
            )

        return response

    @staticmethod
    def object_endpoint(bucket, object_path=""):
        bucket_part = quote(str(bucket).strip(), safe="")
        normalized_path = str(object_path).replace("\\", "/").strip("/")

        if not normalized_path:
            return f"/storage/v1/object/{bucket_part}"

        return (
            f"/storage/v1/object/{bucket_part}/"
            f"{quote(normalized_path, safe='/')}"
        )


def create_supabase_client(required=False, **kwargs):
    """Return a configured client, or ``None`` for optional cloud support."""

    try:
        return SupabaseStorageClient.from_environment(**kwargs)
    except SupabaseConfigurationError:
        if required:
            raise
        return None
