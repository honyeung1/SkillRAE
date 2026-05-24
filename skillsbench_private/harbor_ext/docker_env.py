from pathlib import Path

from harbor.environments.docker.docker import DockerEnvironment

from skillsbench_private.docker_proxy import docker_global_proxy_enabled


class HostLiteLLMDockerEnvironment(DockerEnvironment):
    """Append a minimal compose overlay for host-side LiteLLM access."""

    _OVERLAY_COMPOSE_PATH = Path(__file__).with_name("host_litellm_overlay.compose.yaml")
    _PROXY_OVERLAY_COMPOSE_PATH = Path(__file__).with_name(
        "docker_proxy_overlay.compose.yaml"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def _docker_compose_paths(self) -> list[Path]:
        paths = list(super()._docker_compose_paths)
        paths.append(self._OVERLAY_COMPOSE_PATH)
        if docker_global_proxy_enabled():
            paths.append(self._PROXY_OVERLAY_COMPOSE_PATH)
        return paths
