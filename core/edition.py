"""The Edition profile: the values that make JDeck Desktop and JDeck Server different.
Each entry point creates one Edition and passes it to the core. The core reads these
values; it never checks which edition it is running in."""

from dataclasses import dataclass, field


@dataclass(frozen=True, eq=False)
class Edition:
    name: str            # "desktop" or "server"
    title: str           # Shown in the window title, tray and pages
    data_folder: str     # Folder beside the program that holds settings.json
    defaults: dict       # Every settings key this edition keeps, with its default value
    features: dict = field(default_factory=dict)  # Sent to the page in /api/state
