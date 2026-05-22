"""ABI Memory Plugin — Hermes plugin registration.

Registers ABIMemoryProvider as the external memory backend.
Activated via config.yaml: memory.provider: abi_memory
"""

from abi.memory.provider import ABIMemoryProvider


def register(ctx):
    """Register the ABI memory provider."""
    provider = ABIMemoryProvider()
    ctx.register_memory_provider(provider)
