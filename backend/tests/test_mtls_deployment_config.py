from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_backend_group_is_configurable_and_ca_is_not_mounted():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "${SALES_BACKEND_GID:" in compose
    assert "group_add:" in compose
    assert "SALES_BACKEND_GID=" in env_example
    assert "/etc/nginx/sales-mtls:/" not in compose


def test_helper_sandbox_keeps_strict_protection_and_runtime_directory():
    unit = (ROOT / "deploy/mtls-helper/sales-mtls-helper.service").read_text(encoding="utf-8")

    assert "ProtectSystem=strict" in unit
    assert "RuntimeDirectoryPreserve=yes" in unit
    assert "/run/nginx.pid" in unit
    assert "/var/log/nginx" in unit
