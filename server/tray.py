"""JDeck Server's tray icon. Click it to open the control page. Runs on the main thread
(macOS requires this); the HTTP server runs on background threads."""

import webbrowser

from core.settings import resource_path


def run_tray(controller):
    """Show the icon and block until Quit. Raises when no tray is available."""
    import pystray
    from PIL import Image

    def open_control():
        webbrowser.open(controller.local_url("/server"))

    def open_page():
        webbrowser.open(controller.local_url("/"))

    menu = pystray.Menu(
        pystray.MenuItem("Open JDeck Server", open_control, default=True),
        pystray.MenuItem("Open JDeck", open_page),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit JDeck Server", controller.request_stop),
    )
    icon = pystray.Icon("jdeck-server", Image.open(resource_path("assets", "icon", "jdeck.png")),
                        "JDeck Server", menu)
    controller.on_stop = icon.stop
    if controller.stopped.is_set():  # Stopped before the icon appeared.
        return
    icon.run()
