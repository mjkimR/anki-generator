from typing import Dict
from .base import BaseTTSProvider
from .azure import AzureTTSProvider
from .aivis import AivisTTSProvider
from .edge import EdgeTTSProvider


class TTSProviderFactory:
    """Factory for managing and instantiating TTS providers."""

    def __init__(self):
        self._providers: Dict[str, BaseTTSProvider] = {
            "azure": AzureTTSProvider(),
            "aivis": AivisTTSProvider(),
            "edge": EdgeTTSProvider(),
        }

    def get_provider(self, provider_name: str) -> BaseTTSProvider:
        name = provider_name.strip().lower() if provider_name else ""
        if name not in self._providers:
            supported = ", ".join(sorted(self._providers.keys()))
            raise ValueError(f"Unsupported TTS_PROVIDER '{provider_name}'. Choose one of: {supported}.")
        return self._providers[name]

    def list_supported_providers(self) -> tuple:
        return tuple(sorted(self._providers.keys()))

    def get_render_versions(self) -> dict:
        return {name: provider.render_version for name, provider in self._providers.items()}


_factory_instance = TTSProviderFactory()


def get_provider(provider_name: str) -> BaseTTSProvider:
    return _factory_instance.get_provider(provider_name)


def list_supported_providers() -> tuple:
    return _factory_instance.list_supported_providers()


def get_render_versions() -> dict:
    return _factory_instance.get_render_versions()
