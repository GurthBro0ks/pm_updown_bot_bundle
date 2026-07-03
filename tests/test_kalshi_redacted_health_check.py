from types import SimpleNamespace

import pytest
import requests

from scripts import kalshi_redacted_health_check as health


class FakeClient:
    base_url = "https://kalshi.example"

    def _normalize_api_path(self, path):
        if not path.startswith("/"):
            path = f"/{path}"
        if path.startswith("/trade-api/v2"):
            return path
        return f"/trade-api/v2{path}"

    def _get_auth_headers(self, method, path):
        return {
            "KALSHI-ACCESS-KEY": "full-key-id-must-not-print",
            "KALSHI-ACCESS-SIGNATURE": "signature-must-not-print",
            "KALSHI-ACCESS-TIMESTAMP": "timestamp-must-not-print",
        }


def _run_with_statuses(statuses):
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, headers, params, timeout))
        status = statuses[len(calls) - 1]
        if status == "timeout":
            raise requests.Timeout("network timeout with sensitive detail")
        return SimpleNamespace(status_code=status, text="body-must-not-print")

    results, status_class = health.run_checks(
        client_factory=FakeClient,
        request_get=fake_get,
    )
    return results, status_class, health.format_results(results, status_class), calls


def test_all_2xx_readonly_statuses_return_pass():
    results, status_class, output, calls = _run_with_statuses([200, 204, 200])

    assert [result.status for result in results] == ["PASS", "PASS", "PASS"]
    assert status_class == "2xx"
    assert "KALSHI_AUTH=PASS" in output
    assert "PORTFOLIO_READONLY=PASS" in output
    assert "OPEN_ORDERS_READONLY=PASS" in output
    assert "HTTP_STATUS_CLASS=2xx" in output
    assert "VALUES_PRINTED=no" in output
    assert len(calls) == 3


def test_auth_401_fails_without_leaking_response_or_headers():
    _, status_class, output, _ = _run_with_statuses([401, 200, 200])

    assert "KALSHI_AUTH=FAIL" in output
    assert "HTTP_STATUS_CLASS=4xx" in output
    assert status_class == "4xx"
    assert "body-must-not-print" not in output
    assert "full-key-id-must-not-print" not in output
    assert "signature-must-not-print" not in output
    assert "timestamp-must-not-print" not in output


def test_404_and_timeout_warn_without_leaking_values():
    _, status_class, output, _ = _run_with_statuses([200, 404, "timeout"])

    assert "KALSHI_AUTH=PASS" in output
    assert "PORTFOLIO_READONLY=WARN" in output
    assert "OPEN_ORDERS_READONLY=WARN" in output
    assert status_class == "4xx"
    assert "network timeout" not in output
    assert "body-must-not-print" not in output


def test_client_init_failure_prints_only_status_lines():
    def broken_client():
        raise RuntimeError("secret/path/should/not/print")

    results, status_class = health.run_checks(client_factory=broken_client)
    output = health.format_results(results, status_class)

    assert output == (
        "KALSHI_AUTH=FAIL\n"
        "PORTFOLIO_READONLY=WARN\n"
        "OPEN_ORDERS_READONLY=WARN\n"
        "HTTP_STATUS_CLASS=unknown\n"
        "VALUES_PRINTED=no\n"
    )
    assert "secret/path" not in output


def test_output_contract_contains_no_key_paths_env_values_or_headers():
    _, _, output, _ = _run_with_statuses([200, 200, 200])

    forbidden_fragments = [
        "full-key-id-must-not-print",
        "signature-must-not-print",
        "KALSHI-ACCESS-KEY",
        "KALSHI_PRIVATE_KEY_PATH",
        "/tmp/private.pem",
        "Authorization",
        "Bearer",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in output


def test_5xx_status_class_is_preserved():
    _, status_class, output, _ = _run_with_statuses([200, 500, 200])

    assert status_class == "5xx"
    assert "PORTFOLIO_READONLY=WARN" in output
    assert "HTTP_STATUS_CLASS=5xx" in output


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failure_statuses_are_fail(status_code):
    _, _, output, _ = _run_with_statuses([status_code, 200, 200])

    assert "KALSHI_AUTH=FAIL" in output
