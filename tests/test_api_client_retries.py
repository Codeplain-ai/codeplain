"""Which failed requests the API client retries."""

from types import SimpleNamespace

import pytest
import requests

import codeplain_REST_api
from codeplain_REST_api import CodeplainAPI


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error", response=self)


def make_api(monkeypatch, statuses):
    api = CodeplainAPI("key", SimpleNamespace(debug=lambda *a, **k: None, error=lambda *a, **k: None))
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return FakeResponse(statuses[min(len(calls), len(statuses)) - 1])

    api.session = SimpleNamespace(post=post)
    monkeypatch.setattr(codeplain_REST_api.time, "sleep", lambda seconds: None)
    return api, calls


def test_a_not_found_answer_is_not_retried_and_raised_as_it_is(monkeypatch):
    api, calls = make_api(monkeypatch, [404])

    with pytest.raises(requests.exceptions.HTTPError) as raised:
        api.post_request("http://api/agent/continue", {}, {}, None, num_retries=8)

    assert raised.value.response.status_code == 404 and len(calls) == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limits_and_server_errors_are_retried(monkeypatch, status):
    api, calls = make_api(monkeypatch, [status, 200])

    assert api.post_request("http://api/x", {}, {}, None, num_retries=2) == {}
    assert len(calls) == 2
