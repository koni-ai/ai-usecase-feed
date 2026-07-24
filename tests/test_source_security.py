from __future__ import annotations

import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

import source_manager


def resolver(host: str, port: int, **_kwargs):
    address = "10.0.0.7" if host == "internal.example" else "93.184.216.34"
    return [(2, 1, 6, "", (address, port))]


class FakeResponse:
    def __init__(self, url: str, payload: bytes, content_type: str = "text/plain"):
        self.url = url
        self.payload = payload
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.payload

    def geturl(self) -> str:
        return self.url


class SequenceOpener:
    def __init__(self, values: list[object]):
        self.values = list(values)
        self.requests = []

    def open(self, request, timeout: float):
        self.requests.append((request.full_url, timeout))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def redirect_error(source: str, target: str) -> HTTPError:
    headers = Message()
    headers["Location"] = target
    return HTTPError(source, 302, "Found", headers, None)


class SafeFetcherTests(unittest.TestCase):
    def test_redirect_target_is_revalidated_before_second_request(self) -> None:
        first = "https://public.example/start"
        opener = SequenceOpener(
            [redirect_error(first, "https://internal.example/private")]
        )
        with patch("source_manager.build_opener", return_value=opener):
            fetch = source_manager.make_safe_fetcher(resolver=resolver)
        with self.assertRaises(source_manager.UnsafeURLError):
            fetch(first, "text/html", 100)
        self.assertEqual(1, len(opener.requests))

    def test_public_redirect_is_bounded_and_succeeds(self) -> None:
        first = "https://public.example/start"
        final = "https://other.example/feed"
        opener = SequenceOpener(
            [
                redirect_error(first, final),
                FakeResponse(final, b"<rss/>", "application/rss+xml"),
            ]
        )
        with patch("source_manager.build_opener", return_value=opener):
            fetch = source_manager.make_safe_fetcher(resolver=resolver)
        payload, final_url, _ = fetch(first, "application/xml", 100)
        self.assertEqual(b"<rss/>", payload)
        self.assertEqual(final, final_url)
        self.assertEqual(2, len(opener.requests))

    def test_stream_read_rejects_response_over_byte_limit(self) -> None:
        url = "https://public.example/feed"
        opener = SequenceOpener([FakeResponse(url, b"x" * 11)])
        with patch("source_manager.build_opener", return_value=opener):
            fetch = source_manager.make_safe_fetcher(resolver=resolver)
        with self.assertRaises(source_manager.FetchError):
            fetch(url, "application/xml", 10)


if __name__ == "__main__":
    unittest.main()
