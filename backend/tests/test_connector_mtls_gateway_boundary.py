from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_public_gateway_does_not_proxy_mtls_ingress_routes() -> None:
    source = (ROOT / "gateway" / "Caddyfile").read_text(encoding="utf-8")

    connector_ingress = "/api/v1/connector-control/ingress/*"
    policy_ingress = "/api/v1/policy-control/ingress/*"
    blocked = source.index("@connectorIngress")
    general_api = source.index("@api path")

    assert connector_ingress in source
    assert policy_ingress in source
    assert "handle @connectorIngress" in source
    assert "respond 404" in source[blocked:general_api]
    assert blocked < general_api
