"""Download and cache rules (D-15, D-16) with a fake HTTP layer. No network access."""

from __future__ import annotations

import pytest
import requests

import starter_cli.landing
from starter_cli.landing import DownloadError, EntitySpec, download

SPEC = EntitySpec(
    source="acme",
    entity="widgets",
    url="https://example.invalid/widgets.csv",
    file_name="widgets.csv",
    columns=("code", "name"),
    header_prefix=b'"Code"',
)
GOOD_BODY = b'"Code","Name"\n"A1","Alpha"\n'


class FakeResponse:
    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self.content = content


class FakeGet:
    """Stands in for requests.get and records every call."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[str] = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.response


def install(monkeypatch, fake) -> None:
    monkeypatch.setattr(starter_cli.landing.requests, "get", fake)


def cache_files(raw_root):
    return sorted(path.name for path in raw_root.rglob("*") if path.is_file())


def test_http_403_fails_after_one_request_with_a_helpful_message(monkeypatch, tmp_path):
    fake = FakeGet(FakeResponse(403, b"<html>Forbidden</html>"))
    install(monkeypatch, fake)
    with pytest.raises(DownloadError) as excinfo:
        download(SPEC, tmp_path)
    message = str(excinfo.value)
    assert SPEC.url in message
    assert "blocked or unavailable" in message
    assert "README.md#troubleshooting" in message
    assert len(fake.calls) == 1
    assert cache_files(tmp_path) == []


def test_http_404_fails_after_one_request_and_caches_nothing(monkeypatch, tmp_path):
    fake = FakeGet(FakeResponse(404, b"not found"))
    install(monkeypatch, fake)
    with pytest.raises(DownloadError, match="blocked or unavailable"):
        download(SPEC, tmp_path)
    assert len(fake.calls) == 1
    assert cache_files(tmp_path) == []


def test_html_200_error_page_is_not_cached(monkeypatch, tmp_path):
    fake = FakeGet(FakeResponse(200, b"<!DOCTYPE html><html>Access denied</html>"))
    install(monkeypatch, fake)
    with pytest.raises(DownloadError, match="README.md#troubleshooting"):
        download(SPEC, tmp_path)
    assert len(fake.calls) == 1
    assert cache_files(tmp_path) == []


def test_network_error_fails_after_one_request_and_caches_nothing(monkeypatch, tmp_path):
    fake = FakeGet(error=requests.ConnectionError("no route"))
    install(monkeypatch, fake)
    with pytest.raises(DownloadError) as excinfo:
        download(SPEC, tmp_path)
    assert SPEC.url in str(excinfo.value)
    assert "blocked or unavailable" in str(excinfo.value)
    assert len(fake.calls) == 1
    assert cache_files(tmp_path) == []


def test_good_response_is_cached_byte_for_byte_with_no_part_file(monkeypatch, tmp_path):
    fake = FakeGet(FakeResponse(200, GOOD_BODY))
    install(monkeypatch, fake)
    path = download(SPEC, tmp_path)
    assert path == tmp_path / "acme" / "widgets.csv"
    assert path.read_bytes() == GOOD_BODY
    assert cache_files(tmp_path) == ["widgets.csv"]


def test_a_cached_file_is_reused_without_any_network_call(monkeypatch, tmp_path):
    cached = tmp_path / "acme" / "widgets.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(GOOD_BODY)

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("the network was used although the file is cached")

    install(monkeypatch, must_not_be_called)
    assert download(SPEC, tmp_path) == cached


def test_refresh_downloads_exactly_once_and_replaces_the_file(monkeypatch, tmp_path):
    cached = tmp_path / "acme" / "widgets.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'"Code","Name"\n"OLD","old"\n')
    fake = FakeGet(FakeResponse(200, GOOD_BODY))
    install(monkeypatch, fake)
    path = download(SPEC, tmp_path, refresh=True)
    assert len(fake.calls) == 1
    assert path.read_bytes() == GOOD_BODY
    assert cache_files(tmp_path) == ["widgets.csv"]


def test_a_failed_refresh_keeps_the_previous_cached_file(monkeypatch, tmp_path):
    cached = tmp_path / "acme" / "widgets.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(GOOD_BODY)
    fake = FakeGet(FakeResponse(403, b"nope"))
    install(monkeypatch, fake)
    with pytest.raises(DownloadError):
        download(SPEC, tmp_path, refresh=True)
    assert cached.read_bytes() == GOOD_BODY
    assert cache_files(tmp_path) == ["widgets.csv"]
