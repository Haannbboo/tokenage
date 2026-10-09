"""The client-side model proxy: forwards to providers with the user's own keys
and reports metadata-only usage to the server. Never imports ``src``."""

from client.proxy.runtime import ProxyHandle, start_proxy

__all__ = ["ProxyHandle", "start_proxy"]
