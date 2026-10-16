from app.services.discovery.adapters.base import DiscoveryAdapter
from app.services.discovery.adapters.direct import DirectFileDiscoveryAdapter
from app.services.discovery.adapters.lux import LuxDiscoveryAdapter
from app.services.discovery.adapters.manifest import ManifestDiscoveryAdapter
from app.services.discovery.adapters.ytdlp import YtDlpDiscoveryAdapter

__all__ = [
    "DiscoveryAdapter",
    "DirectFileDiscoveryAdapter",
    "LuxDiscoveryAdapter",
    "ManifestDiscoveryAdapter",
    "YtDlpDiscoveryAdapter",
]
