"""These tests exercise aug and store. The words cleanup and finals appear only in this docstring."""
from unittest import mock

from pkg import helpers
from pkg.svc import aug


def test_aug():
    assert aug(1) == 0


def test_store(monkeypatch):
    monkeypatch.setattr(helpers, "parse", mock.MagicMock())
    with mock.patch("pkg.svc.gen"):
        assert helpers.store(1) == 1


def test_answer_route(client):
    assert client.post("/answer").status_code == 200
    assert client.delete(f"/document/{5}").status_code == 200
