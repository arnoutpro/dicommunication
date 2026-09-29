from __future__ import annotations

import os

import pytest

# Starlette's TestClient sends "Host: testserver". The app refuses host names it
# was not told about (DNS-rebinding protection, app/security.py), so allow this
# one the same way a deployment behind a reverse proxy would. Set before any
# app is created.
os.environ.setdefault("DICOMM_ALLOWED_HOSTS", "testserver")
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import RemoteNode
from app.store import ConfigStore


@pytest.fixture
def store(tmp_path) -> ConfigStore:
    return ConfigStore(tmp_path)


@pytest.fixture
def app(store: ConfigStore):
    return create_app(store)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def remote(store: ConfigStore) -> RemoteNode:
    node = RemoteNode(name="Local test SCP", ae_title="TEST_SCP", host="127.0.0.1", port=11112)
    store.add_remote(node)
    return node
