import shutil
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.models import StoredObject


class ObjectNotFound(Exception):
    pass


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def put_file(self, key: str, path: Path, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def download(self, key: str, path: Path) -> None: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def get_bytes(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc

    def download(self, key: str, path: Path) -> None:
        path.write_bytes(self.get_bytes(key))


class S3Storage:
    def __init__(self, bucket: str, endpoint_url: str | None, region: str):
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def ensure_bucket(self) -> None:
        # api and worker both call this at startup, so losing the creation race is expected.
        try:
            self.client.create_bucket(Bucket=self.bucket)
        except (
            self.client.exceptions.BucketAlreadyOwnedByYou,
            # Returned by some S3-compatible servers for a bucket this caller already created.
            self.client.exceptions.BucketAlreadyExists,
        ):
            pass

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey as exc:
            raise ObjectNotFound(key) from exc

    def download(self, key: str, path: Path) -> None:
        self.client.download_file(self.bucket, key, str(path))


class PostgresStorage:
    """Objects as rows in stored_objects. Used where no object store is available (the
    free-tier cloud deployment); writes are idempotent upserts, like S3 puts."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        values = {"content_type": content_type, "size_bytes": len(data), "data": data}
        with self.session_factory() as session:
            session.execute(
                pg_insert(StoredObject)
                .values(key=key, **values)
                .on_conflict_do_update(index_elements=["key"], set_=values)
            )
            session.commit()

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        self.put_bytes(key, path.read_bytes(), content_type)

    def get_bytes(self, key: str) -> bytes:
        with self.session_factory() as session:
            data = session.scalar(select(StoredObject.data).where(StoredObject.key == key))
        if data is None:
            raise ObjectNotFound(key)
        return data

    def download(self, key: str, path: Path) -> None:
        path.write_bytes(self.get_bytes(key))


def make_storage(
    settings: Settings,
    session_factory: sessionmaker[Session] | None = None,
    create_bucket: bool = False,
) -> Storage:
    if settings.storage_backend == "postgres":
        return PostgresStorage(session_factory)
    if settings.storage_backend == "s3":
        storage = S3Storage(settings.s3_bucket, settings.s3_endpoint_url, settings.s3_region)
        if create_bucket and settings.s3_create_bucket:
            storage.ensure_bucket()
        return storage
    return LocalStorage(settings.storage_local_root)
