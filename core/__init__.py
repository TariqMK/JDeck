"""JDeck core: the code shared by JDeck Desktop and JDeck Server.
Uses the Python standard library only. Never imports a window or tray toolkit."""

from _version import __version__  # noqa: F401  (re-exported for the other modules)

APP_NAME = "JDeck"
