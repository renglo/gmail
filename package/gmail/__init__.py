"""Gmail agent inbox channel extension for Renglo."""

__version__ = "1.0.0"
__all__ = ["get_handler", "list_handlers", "HANDLERS"]


def _get_initialize_extension():
    from gmail.handlers.initialize_extension import InitializeExtension

    return InitializeExtension


HANDLERS = {
    "initialize_extension": _get_initialize_extension,
}


def get_handler(handler_name: str):
    """Get an instantiated handler by name."""
    if handler_name not in HANDLERS:
        available = ", ".join(HANDLERS.keys())
        raise KeyError(
            f"Handler '{handler_name}' not found. Available handlers: {available}"
        )

    return HANDLERS[handler_name]()


def list_handlers():
    """List all available handler names."""
    return list(HANDLERS.keys())
