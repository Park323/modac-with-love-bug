"""MODAC — a framework-agnostic image-to-action system for FPS-style games.

Two decoupled halves talk over a small HTTP/WebSocket API:

    [game adapter]                         [policy server]
     grab frame   ──── image bytes ────▶    image -> Action model
     apply action ◀──── Action JSON ─────    (this package serves it)

Swap the adapter to point the same policy at a different game.
"""

__version__ = "0.1.0"
