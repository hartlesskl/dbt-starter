"""Download, retry, integrity and cache rules (D-13, D-14) with a fake HTTP layer.

No network access and no real waiting: `sleep` is a recorder and `jitter` is injectable.
"""

from __future__ import annotations

import json

import pytest
import requests

import starter_cli.landing
from starter_cli.landing import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    DownloadError,
    EntitySpec,
    download,
)

CSV_SPEC = EntitySpec(
    source="acme",
    entity="widgets",
    url="https://example.invalid/widgets.csv",
    file_name="widgets.csv",
    columns=("code", "name"),
    header_prefix=b'"Code"',
)
PARQUET_SPEC = EntitySpec(
    source="acme",
    entity="parts",
    url="https://example.invalid/parts_{month}.parquet",
    file_name="parts_{month}.parquet",
    columns=("code",),
    header_prefix=b"PAR1",
    file_format="parquet",
    default_months=("2024-01",),
)
MONTH = "2024-01"
PARQUET_URL = PARQUET_SPEC.url_for(MONTH)
CSV_BODY = b'"Code","Name"\n"A1","Alpha"\n'
PARQUET_BODY = b"PAR1" + b"x" * 40 + b"PAR1"


class FakeResponse:
    """A streamed response. Chunks are 4 bytes so a short body still has several chunks."""

    def __init__(self, status_code, body=b"", headers=None, fail_after_chunks=None):
        self.status_code = status_code
        self.body = body
        self.headers = {} if headers is None else dict(headers)
        self.fail_after_chunks = fail_after_chunks

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        for index in range(0, len(self.body), 4):
            if self.fail_after_chunks is not None and index // 4 >= self.fail_after_chunks:
                raise requests.ConnectionError("connection reset mid-stream")
            yield self.body[index : index + 4]
        if self.fail_after_chunks is not None and len(self.body) <= 4 * self.fail_after_chunks:
            raise requests.ConnectionError("connection reset at the end")

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def good(body=PARQUET_BODY, **headers):
    """A 200 response whose Content-Length matches its body unless a header overrides it."""
    merged = {"Content-Length": str(len(body)), **headers}
    return FakeResponse(200, body, merged)


class FakeGet:
    """Stands in for requests.get: one queued outcome per request, and every call recorded."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert self.outcomes, f"unexpected request number {len(self.calls)} to {url}"
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def urls(self):
        return [url for url, _kwargs in self.calls]


def install(monkeypatch, fake) -> None:
    monkeypatch.setattr(starter_cli.landing.requests, "get", fake)


def never_called(*_args, **_kwargs):
    raise AssertionError("the network was used although the file is cached")


class Sleeps(list):
    """Records requested waits instead of sleeping."""

    def __call__(self, seconds):
        self.append(seconds)


def run(monkeypatch, tmp_path, outcomes, spec=PARQUET_SPEC, month=MONTH, **kwargs):
    fake = FakeGet(outcomes)
    install(monkeypatch, fake)
    sleeps = Sleeps()
    kwargs.setdefault("sleep", sleeps)
    path = download(spec, tmp_path, month=month if spec.is_monthly else None, **kwargs)
    return path, fake, sleeps


def run_failing(monkeypatch, tmp_path, outcomes, spec=PARQUET_SPEC, month=MONTH, **kwargs):
    fake = FakeGet(outcomes)
    install(monkeypatch, fake)
    sleeps = Sleeps()
    kwargs.setdefault("sleep", sleeps)
    with pytest.raises(DownloadError) as excinfo:
        download(spec, tmp_path, month=month if spec.is_monthly else None, **kwargs)
    return excinfo.value, fake, sleeps


def cache_files(raw_root):
    return sorted(str(path.relative_to(raw_root)) for path in raw_root.rglob("*") if path.is_file())


def seed_cache(raw_root, body=PARQUET_BODY, meta=None):
    """Put a file (and optionally its sidecar) in the cache and return the file path."""
    path = raw_root / "acme" / PARQUET_SPEC.file_name_for(MONTH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if meta is not None:
        path.with_name(path.name + ".meta.json").write_text(json.dumps(meta))
    return path


def test_a_good_parquet_body_is_cached_after_one_request_with_a_sidecar(monkeypatch, tmp_path):
    response = good(**{"Last-Modified": "Thu, 21 Mar 2024 15:35:44 GMT", "ETag": '"abc"'})
    path, fake, sleeps = run(monkeypatch, tmp_path, [response])
    assert path == tmp_path / "acme" / "parts_2024-01.parquet"
    assert path.read_bytes() == PARQUET_BODY
    assert fake.urls == [PARQUET_URL]
    assert sleeps == []
    assert cache_files(tmp_path) == [
        "acme/parts_2024-01.parquet",
        "acme/parts_2024-01.parquet.meta.json",
    ]
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    assert set(meta) == {"url", "size", "last_modified", "etag", "downloaded_at"}
    assert meta["url"] == PARQUET_URL
    assert meta["size"] == len(PARQUET_BODY)
    assert meta["last_modified"] == "Thu, 21 Mar 2024 15:35:44 GMT"
    assert meta["etag"] == '"abc"'
    assert meta["downloaded_at"]


def test_a_request_streams_with_a_timeout_pair_and_a_user_agent(monkeypatch, tmp_path):
    _path, fake, _sleeps = run(monkeypatch, tmp_path, [good()])
    _url, kwargs = fake.calls[0]
    assert kwargs["stream"] is True
    assert isinstance(kwargs["timeout"], tuple) and len(kwargs["timeout"]) == 2
    assert kwargs["headers"]["User-Agent"]


def test_four_403s_fail_after_exactly_four_requests_with_backed_off_waits(monkeypatch, tmp_path):
    outcomes = [FakeResponse(403, b"<html>Forbidden</html>") for _ in range(MAX_ATTEMPTS)]
    error, fake, sleeps = run_failing(monkeypatch, tmp_path, outcomes)
    assert len(fake.calls) == 4
    assert len(sleeps) == 3
    for wait, base in zip(sleeps, BACKOFF_SECONDS, strict=True):
        assert base <= wait <= base * 1.25
    message = str(error)
    assert PARQUET_URL in message
    assert "--from-dir" in message
    assert "rate limit" in message
    assert "README.md#troubleshooting" in message
    assert cache_files(tmp_path) == []


def test_the_waits_follow_the_injected_jitter(monkeypatch, tmp_path):
    outcomes = [FakeResponse(503) for _ in range(MAX_ATTEMPTS)]
    _error, _fake, sleeps = run_failing(monkeypatch, tmp_path, outcomes, jitter=lambda: 1.0)
    assert sleeps == [2.5, 5.0, 10.0]


def test_a_403_then_a_good_body_succeeds_after_one_wait(monkeypatch, tmp_path):
    path, fake, sleeps = run(monkeypatch, tmp_path, [FakeResponse(403, b"no"), good()])
    assert path.read_bytes() == PARQUET_BODY
    assert len(fake.calls) == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    "first",
    [
        FakeResponse(404, b"not found"),
        FakeResponse(429, b"slow down"),
        FakeResponse(500, b"oops"),
        FakeResponse(502, b"bad gateway"),
        requests.ConnectionError("no route"),
        requests.Timeout("too slow"),
    ],
    ids=["404", "429", "500", "502", "connection-error", "timeout"],
)
def test_each_kind_of_failed_attempt_is_retried(monkeypatch, tmp_path, first):
    path, fake, sleeps = run(monkeypatch, tmp_path, [first, good()])
    assert path.is_file()
    assert len(fake.calls) == 2
    assert len(sleeps) == 1


def test_retry_after_sets_the_wait_exactly(monkeypatch, tmp_path):
    throttled = FakeResponse(429, b"", {"Retry-After": "30"})
    _path, _fake, sleeps = run(monkeypatch, tmp_path, [throttled, good()])
    assert sleeps == [30]


def test_retry_after_is_capped_at_sixty_seconds(monkeypatch, tmp_path):
    throttled = FakeResponse(429, b"", {"Retry-After": "600"})
    _path, _fake, sleeps = run(monkeypatch, tmp_path, [throttled, good()])
    assert sleeps == [MAX_RETRY_AFTER_SECONDS] == [60.0]


def test_a_retry_after_that_is_not_a_number_falls_back_to_the_backoff(monkeypatch, tmp_path):
    throttled = FakeResponse(503, b"", {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    _path, _fake, sleeps = run(monkeypatch, tmp_path, [throttled, good()], jitter=lambda: 0.0)
    assert sleeps == [2.0]


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (good(PARQUET_BODY, **{"Content-Length": str(len(PARQUET_BODY) + 10)}), "short body"),
        (FakeResponse(200, b"<!DOCTYPE html><html>Access denied</html>"), "html page"),
        (good(b"PAR1" + b"x" * 40 + b"PAR2"), "bad tail"),
        (good(b"PAR1"), "too short to hold both magics"),
    ],
    ids=["short-body", "html-200", "bad-tail", "tiny"],
)
def test_a_wrong_body_is_retried_then_fails_with_nothing_cached(monkeypatch, tmp_path, bad, why):
    outcomes = [FakeResponse(bad.status_code, bad.body, bad.headers) for _ in range(MAX_ATTEMPTS)]
    error, fake, sleeps = run_failing(monkeypatch, tmp_path, outcomes)
    assert len(fake.calls) == MAX_ATTEMPTS, why
    assert len(sleeps) == MAX_ATTEMPTS - 1
    assert PARQUET_URL in str(error)
    assert cache_files(tmp_path) == []


def test_a_stream_that_breaks_after_the_first_chunk_leaves_no_part_and_no_file(
    monkeypatch, tmp_path
):
    outcomes = [
        FakeResponse(200, PARQUET_BODY, {}, fail_after_chunks=1) for _ in range(MAX_ATTEMPTS)
    ]
    error, fake, _sleeps = run_failing(monkeypatch, tmp_path, outcomes)
    assert len(fake.calls) == MAX_ATTEMPTS
    assert PARQUET_URL in str(error)
    assert cache_files(tmp_path) == []


def test_a_stream_that_breaks_once_is_retried_and_the_second_try_is_cached(monkeypatch, tmp_path):
    broken = FakeResponse(200, PARQUET_BODY, {}, fail_after_chunks=2)
    path, _fake, _sleeps = run(monkeypatch, tmp_path, [broken, good()])
    assert path.read_bytes() == PARQUET_BODY
    assert cache_files(tmp_path) == [
        "acme/parts_2024-01.parquet",
        "acme/parts_2024-01.parquet.meta.json",
    ]


def test_a_compressed_response_is_not_held_to_content_length(monkeypatch, tmp_path):
    # `requests` decodes the body, so its length differs from the Content-Length header.
    response = good(PARQUET_BODY, **{"Content-Length": "7", "Content-Encoding": "gzip"})
    path, fake, _sleeps = run(monkeypatch, tmp_path, [response])
    assert path.read_bytes() == PARQUET_BODY
    assert len(fake.calls) == 1


def test_a_cached_file_is_reused_with_zero_requests(monkeypatch, tmp_path):
    cached = seed_cache(tmp_path, meta={"size": len(PARQUET_BODY)})
    install(monkeypatch, never_called)
    assert download(PARQUET_SPEC, tmp_path, month=MONTH, sleep=Sleeps()) == cached


def test_a_cached_file_with_no_sidecar_is_reused_after_the_magic_check(monkeypatch, tmp_path):
    cached = seed_cache(tmp_path)
    install(monkeypatch, never_called)
    assert download(PARQUET_SPEC, tmp_path, month=MONTH, sleep=Sleeps()) == cached


def test_a_cached_file_with_a_cut_tail_is_rejected_with_a_hint(monkeypatch, tmp_path):
    cached = seed_cache(tmp_path, body=PARQUET_BODY[:-6])
    install(monkeypatch, never_called)
    with pytest.raises(DownloadError) as excinfo:
        download(PARQUET_SPEC, tmp_path, month=MONTH, sleep=Sleeps())
    assert "--refresh" in str(excinfo.value)
    assert "delete" in str(excinfo.value).lower()
    assert cached.name in str(excinfo.value)
    assert cached.read_bytes() == PARQUET_BODY[:-6]


def test_a_cached_file_whose_size_differs_from_its_sidecar_is_rejected(monkeypatch, tmp_path):
    seed_cache(tmp_path, meta={"size": len(PARQUET_BODY) + 1})
    install(monkeypatch, never_called)
    with pytest.raises(DownloadError, match="--refresh"):
        download(PARQUET_SPEC, tmp_path, month=MONTH, sleep=Sleeps())


@pytest.mark.parametrize("body", [b"", b"<html>not a csv</html>"])
def test_a_cached_csv_without_the_expected_header_is_rejected_with_a_hint(
    monkeypatch, tmp_path, body
):
    cached = tmp_path / "acme" / "widgets.csv"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(body)
    install(monkeypatch, never_called)
    with pytest.raises(DownloadError, match="--refresh"):
        download(CSV_SPEC, tmp_path)
    assert cached.read_bytes() == body


def test_refresh_downloads_again_and_reports_old_and_new_size_and_last_modified(
    monkeypatch, tmp_path, capsys
):
    old_body = b"PAR1" + b"o" * 10 + b"PAR1"
    cached = seed_cache(
        tmp_path,
        body=old_body,
        meta={"size": len(old_body), "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT"},
    )
    response = good(**{"Last-Modified": "Thu, 21 Mar 2024 15:35:44 GMT"})
    path, fake, _sleeps = run(monkeypatch, tmp_path, [response], refresh=True)
    assert path == cached
    assert len(fake.calls) == 1
    assert path.read_bytes() == PARQUET_BODY
    out = capsys.readouterr().out
    assert "parts_2024-01.parquet" in out
    assert str(len(old_body)) in out
    assert str(len(PARQUET_BODY)) in out
    assert "Mon, 01 Jan 2024 00:00:00 GMT" in out
    assert "Thu, 21 Mar 2024 15:35:44 GMT" in out
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text())
    assert meta["size"] == len(PARQUET_BODY)


def test_a_failed_refresh_keeps_the_previous_file_and_sidecar_byte_for_byte(monkeypatch, tmp_path):
    cached = seed_cache(
        tmp_path, meta={"size": len(PARQUET_BODY), "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT"}
    )
    sidecar = cached.with_name(cached.name + ".meta.json")
    file_before, sidecar_before = cached.read_bytes(), sidecar.read_bytes()
    outcomes = [FakeResponse(403, b"nope") for _ in range(MAX_ATTEMPTS)]
    run_failing(monkeypatch, tmp_path, outcomes, refresh=True)
    assert cached.read_bytes() == file_before
    assert sidecar.read_bytes() == sidecar_before
    assert cache_files(tmp_path) == [
        "acme/parts_2024-01.parquet",
        "acme/parts_2024-01.parquet.meta.json",
    ]


def test_a_refresh_that_breaks_mid_stream_keeps_the_previous_file_and_sidecar(
    monkeypatch, tmp_path
):
    cached = seed_cache(tmp_path, meta={"size": len(PARQUET_BODY)})
    sidecar = cached.with_name(cached.name + ".meta.json")
    file_before, sidecar_before = cached.read_bytes(), sidecar.read_bytes()
    outcomes = [
        FakeResponse(200, PARQUET_BODY, {}, fail_after_chunks=3) for _ in range(MAX_ATTEMPTS)
    ]
    run_failing(monkeypatch, tmp_path, outcomes, refresh=True)
    assert cached.read_bytes() == file_before
    assert sidecar.read_bytes() == sidecar_before
    assert len(cache_files(tmp_path)) == 2


def test_a_csv_download_checks_its_header_and_gets_the_same_retries(monkeypatch, tmp_path):
    html = FakeResponse(200, b"<html>Access denied</html>")
    path, fake, sleeps = run(monkeypatch, tmp_path, [html, good(CSV_BODY)], spec=CSV_SPEC)
    assert path == tmp_path / "acme" / "widgets.csv"
    assert path.read_bytes() == CSV_BODY
    assert len(fake.calls) == 2
    assert len(sleeps) == 1
    assert fake.urls == [CSV_SPEC.url, CSV_SPEC.url]


def test_a_csv_that_never_gets_its_header_fails_with_nothing_cached(monkeypatch, tmp_path):
    outcomes = [FakeResponse(200, b"<html>no</html>") for _ in range(MAX_ATTEMPTS)]
    error, fake, _sleeps = run_failing(monkeypatch, tmp_path, outcomes, spec=CSV_SPEC)
    assert len(fake.calls) == MAX_ATTEMPTS
    assert CSV_SPEC.url in str(error)
    assert cache_files(tmp_path) == []


def test_the_only_exception_a_failed_download_raises_is_download_error(monkeypatch, tmp_path):
    outcomes = [requests.exceptions.ChunkedEncodingError("bad chunk") for _ in range(MAX_ATTEMPTS)]
    error, _fake, _sleeps = run_failing(monkeypatch, tmp_path, outcomes)
    assert isinstance(error, DownloadError)
