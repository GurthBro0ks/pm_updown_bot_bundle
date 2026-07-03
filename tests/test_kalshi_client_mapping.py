"""Credential mapping tests for Kalshi clients."""


def test_order_client_prefers_canonical_rotated_fields(monkeypatch, tmp_path):
    from utils import kalshi_orders

    canonical_key_file = tmp_path / "canonical.pem"
    stale_trading_key_file = tmp_path / "stale-trading.pem"
    legacy_key_file = tmp_path / "legacy.pem"
    canonical_key_file.write_text("placeholder")
    stale_trading_key_file.write_text("placeholder")
    legacy_key_file.write_text("placeholder")

    loaded_paths = []

    def fake_load_pem_private_key(data, password=None):
        loaded_paths.append(data)
        return object()

    monkeypatch.setattr(kalshi_orders.serialization, "load_pem_private_key", fake_load_pem_private_key)
    monkeypatch.setenv("KALSHI_API_KEY_ID", "canonical-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PATH", str(canonical_key_file))
    monkeypatch.setenv("KALSHI_TRADING_KEY", "stale-trading-key")
    monkeypatch.setenv("KALSHI_TRADING_SECRET_FILE", str(stale_trading_key_file))
    monkeypatch.setenv("KALSHI_KEY", "legacy-key")
    monkeypatch.setenv("KALSHI_SECRET_FILE", str(legacy_key_file))

    client = kalshi_orders.KalshiOrderClient(log_path=str(tmp_path / "orders.log"))

    assert client.api_key == "canonical-key"
    assert loaded_paths == [b"placeholder"]


def test_order_client_accepts_key_id_file_aliases(monkeypatch, tmp_path):
    from utils import kalshi_orders

    alias_key_file = tmp_path / "alias.pem"
    alias_key_file.write_text("placeholder")

    monkeypatch.setattr(
        kalshi_orders.serialization,
        "load_pem_private_key",
        lambda data, password=None: object(),
    )
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("KALSHI_KEY_ID", "alias-key")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_FILE", str(alias_key_file))
    monkeypatch.setenv("KALSHI_KEY", "legacy-key")

    client = kalshi_orders.KalshiOrderClient(log_path=str(tmp_path / "orders.log"))

    assert client.api_key == "alias-key"


def test_shadow_resolver_has_no_hardcoded_kalshi_key_default():
    text = open("scripts/shadow_resolver.py", encoding="utf-8").read()

    assert 'os.getenv("KALSHI_KEY", ' not in text
    assert 'os.getenv("KALSHI_SECRET_FILE", ' not in text
