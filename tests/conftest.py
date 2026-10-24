import pytest

from switchboard.principal import Principal
from switchboard.runtime import build_runtime
from switchboard.settings import ALL_SCOPES, Settings


@pytest.fixture
def runtime(tmp_path):
    settings = Settings(
        env="demo",
        audit_path=str(tmp_path / "audit.jsonl"),
        policy_path=str(tmp_path / "policy.json"),
        force_mock=True,
        rate_limit=1000,
    )
    return build_runtime(settings)


def principal(tenant_id: str = "halden-bikes", scopes: list[str] | None = None) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        scopes=frozenset(scopes if scopes is not None else ALL_SCOPES),
        subject="elena.voss@fernwood.studio",
        client_id="test",
    )
