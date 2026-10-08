"""Read-only store access through obstore (the library titiler-eopf 0.12 uses).

`--store` takes `s3://bucket/key.zarr` (credentials and endpoint come from the usual
AWS_* environment variables), an `https://` gateway URL, or a local path. Every
request spends the run's budget. Writes are impossible: there is no write path here,
and zarr opens everything with `read_only=True`.

At the scratch stage, an unsigned reader (an http(s) store, or s3:// with
AWS_SKIP_SIGNATURE) counts a 403 as "no such key": a public bucket without list rights
answers 403 for a missing key (EODC's CI bucket does), and no credentials were sent that
could have been refused. Each such key is recorded for the report, because a key refused
for another reason (an ACL on the data objects) looks the same. A signed reader, and any
registered run, still raises on 403, so a real permission problem stays loud.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import obstore
from obstore.exceptions import PermissionDeniedError
from obstore.store import from_url
from zarr.storage import ObjectStore

from .budget import Budget


def normalize(url: str) -> str:
    if not urlparse(url).scheme:
        return Path(url).resolve().as_uri()
    return url.rstrip("/")


class StoreReader:
    def __init__(self, url: str, budget: Budget, *, forbidden_is_missing: bool = False):
        self.url = normalize(url)
        self.budget = budget
        self._stores: dict[str, object] = {}
        scheme = urlparse(self.url).scheme
        # object_store's own truthy values (y and Y included; verified against obstore 0.11)
        self.unsigned = scheme in ("http", "https") or (
            scheme == "s3" and os.environ.get("AWS_SKIP_SIGNATURE", "").lower() in ("1", "true", "yes", "y", "on"))
        self.absent = (FileNotFoundError, PermissionDeniedError) if forbidden_is_missing and self.unsigned else (FileNotFoundError,)
        self.forbidden: list[str] = []  # keys that answered 403 and were counted as missing

    def missing(self, key: str, exc: Exception) -> None:
        if isinstance(exc, PermissionDeniedError):
            self.forbidden.append(key)

    def access(self) -> str:
        """Where reads go and how they are signed, for `plan` and the report: an s3:// store
        follows AWS_ENDPOINT_URL[_S3], which a shell may still hold from another run."""
        scheme = urlparse(self.url).scheme
        if scheme == "s3":
            endpoint = next((os.environ[k] for k in ("AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL", "AWS_ENDPOINT") if os.environ.get(k)),
                            "the AWS default endpoint")
            return f"s3 via {endpoint}, {'unsigned' if self.unsigned else 'signed with the AWS_* credentials'}"
        return "local path" if scheme == "file" else f"{scheme}, unsigned"

    def _store(self, prefix: str = ""):
        root = f"{self.url}/{prefix}".rstrip("/") if prefix else self.url
        if root not in self._stores:
            # No retries: object_store retries 5xx/429 up to 10 times by default, which the
            # budget would never see. One logical read = one physical request.
            opts = {"allow_http": True} if root.startswith("http://") else None  # local test servers
            self._stores[root] = from_url(root, retry_config={"max_retries": 0}, client_options=opts)
        return self._stores[root]

    def get_json(self, path: str) -> dict | None:
        """GET `<path>` as JSON, or None if it doesn't exist."""
        self.budget.take()
        try:
            data = bytes(obstore.get(self._store(), path).bytes())
        except self.absent as exc:
            self.missing(path, exc)
            return None
        return json.loads(data)

    def node(self, group: str) -> dict | None:
        """The `zarr.json` of a group or array (`""` = the store root)."""
        return self.get_json(f"{group}/zarr.json" if group else "zarr.json")

    def head(self, path: str) -> dict | None:
        self.budget.take()
        try:
            return obstore.head(self._store(), path)
        except self.absent as exc:
            self.missing(path, exc)
            return None

    def head_size(self, path: str) -> int | None:
        meta = self.head(path)
        return int(meta["size"]) if meta else None

    def zarr_store(self, prefix: str = "", *, allow_list: bool = True) -> "BudgetedObjectStore":
        """A read-only zarr store rooted at `prefix` (e.g. an asset href's group).

        allow_list=False makes any listing raise. It mirrors what an HTTP reader hits when
        a group has no consolidated metadata: zarr falls back to listing, obstore's HTTP
        store sends PROPFIND, and the gateway answers 405 (data-pipeline#446).
        """
        return BudgetedObjectStore(self._store(prefix), read_only=True, budget=self.budget, reader=self, allow_list=allow_list, prefix=prefix)


class ListingNotAllowed(RuntimeError):
    pass


class BudgetedObjectStore(ObjectStore):
    def __init__(self, store, *, read_only: bool, budget: Budget, reader: StoreReader, allow_list: bool, prefix: str = ""):
        super().__init__(store, read_only=read_only)
        self._budget, self._reader, self._allow_list, self._prefix = budget, reader, allow_list, prefix

    def with_read_only(self, read_only: bool = False):
        return BudgetedObjectStore(self.store, read_only=True, budget=self._budget, reader=self._reader,
                                   allow_list=self._allow_list, prefix=self._prefix)

    async def get(self, key, prototype, byte_range=None):
        self._budget.take()
        try:
            return await super().get(key, prototype, byte_range)
        except self._reader.absent as exc:  # zarr's wrapper only maps FileNotFoundError to "missing"
            self._reader.missing(f"{self._prefix}/{key}" if self._prefix else key, exc)
            return None

    async def get_partial_values(self, prototype, key_ranges):
        key_ranges = list(key_ranges)
        self._budget.take(len(key_ranges))
        return await super().get_partial_values(prototype, key_ranges)

    def _refuse(self, what: str):
        if not self._allow_list:
            raise ListingNotAllowed(f"zarr tried to {what}: the group needs listing (no consolidated metadata?)")

    def list(self):
        self._refuse("list the store")
        return super().list()

    def list_prefix(self, prefix):
        self._refuse(f"list prefix {prefix!r}")
        return super().list_prefix(prefix)

    def list_dir(self, prefix):
        self._refuse(f"list directory {prefix!r}")
        return super().list_dir(prefix)
