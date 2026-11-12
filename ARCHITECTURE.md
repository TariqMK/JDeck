# JDeck Architecture

This document describes how JDeck is split into two programs that share one codebase. It is the plan for the refactor from the single `jdeck.py` file.

## 1. Two editions

JDeck ships as two separate programs. Each one works alone. Neither needs the other.

| | JDeck Desktop | JDeck Server |
|---|---|---|
| Purpose | A desktop window for one PC | The JDeck web page for every device on the network |
| Runs on | Any PC on the network | The machine that runs JDownloader 2 |
| Connects to | JDownloader on the network, directly | JDownloader on the same machine |
| Default JDownloader address | none (set on first run) | `http://127.0.0.1:3128` |
| Interface | The JDeck page in its own window | The JDeck page in any browser on the network |
| Shell | Window (pywebview) | Tray icon (pystray), or headless |
| Listens on | `127.0.0.1`, random port, for its own window only | All interfaces, port `23128` (changeable) |
| Password | None. Nothing is open to the network | Required |
| Data folder | `JDeck-Desktop-data/` | `JDeck-Server-data/` |
| Runtime packages | pywebview | pystray, Pillow |

**Note:** Both editions can run on the same PC at the same time. They use different data folders and different ports.

## 2. Code layout

One repository. One shared core. One JDeck page. Two thin shells.

```
core/                    Shared. Never imports pywebview or pystray.
  __init__.py
  jd_api.py              JDownloader local API client, link extraction, address parsing
  settings.py            Settings file, password hashing, app and resource paths
  httpd.py               HTTP server, JSON API, Host check, CSRF header (not "http.py": that name hides the standard library module)
  auth.py                Sign-in, sessions, wrong-password delay (used by the server only)
  updates.py             Update check against GitHub releases
  edition.py             The Edition profile (section 3)
  runtime.py             Error pop-up, waiting for Ctrl+C or SIGTERM, the LAN address
desktop/
  __init__.py
  app.py                 Window, start-up, data folder migration
server/
  __init__.py
  app.py                 Edition profile, start-up, --set-password, headless mode
  handler.py             Sign-in, control page API and access rules on top of the core handler
  tray.py                Tray icon and menu
  autostart.py           Start at login (Windows shortcut, macOS LaunchAgent, Linux .desktop)
  qr.py                  Small QR code generator (SVG) for the network address
web/
  index.html             The JDeck page, for both editions
  server.html            The server control page (JDeck Server only)
  login.html             The sign-in page, and the first password on the server PC (JDeck Server only)
  common.css             Styles shared by the three pages
jdeck_desktop.py         Entry point: calls desktop.app.main()
jdeck_server.py          Entry point: calls server.app.main()
_version.py              One version for both editions
tests/
  test_core.py
  test_desktop.py
  test_server.py
```

Rules:

- Imports point down only. `desktop/` and `server/` import `core/`. `core/` never imports `desktop/` or `server/`.
- `core/` uses the Python standard library only.
- An edition never checks its own name. It reads the values in its `Edition` profile.
- `web/index.html` is never copied. Edition differences in the page come from the `features` object (section 3).

## 3. Edition profile

Each entry point creates one `Edition` object and passes it to the core.

```python
Edition(
    name="desktop",                 # "desktop" or "server"
    title="JDeck",                  # "JDeck Server"
    data_folder="JDeck-Desktop-data",
    defaults={"jd_url": ""},        # every settings key the edition keeps
                                    # server: jd_url "http://127.0.0.1:3128", port, password
    features={...},                 # sent to the page
)
```

`Settings` keeps only the keys in `defaults`. Other keys in `settings.json` are ignored.

The page reads `features` from `GET /api/state` and shows or hides the matching controls.

| Feature | Desktop | Server | Page control |
|---|---|---|---|
| `jdAddressEditable` | yes | no (set on the control page) | JDownloader address in Settings |
| `connectionHelp` | yes | no | Help for opening the JDownloader API to the network |
| `signOut` | no | yes | Sign out in the header |
| `controlPage` | no | yes | **Server settings** link in the header and in the JDownloader error banner, shown only when `isLocal` (the server PC) |

When JDownloader cannot be reached, the banner depends on `jdAddressEditable`:

- Desktop: the error and the address, with **Change address**. For "refused" and "unreachable", also **How to fix**, which opens Settings with the connection help open.
- Server: "JDeck Server cannot reach JDownloader on its computer." No address is shown, because other devices cannot change it.

Server settings (port, password, network address, start at login) are not in the JDeck page. They are on the control page (section 5.6).

## 4. JDeck Desktop

JDeck Desktop is the current app without network sharing.

Behaviour:

- Closing the window quits JDeck Desktop.
- The local server answers only on `127.0.0.1` with a random port. It accepts only the Host `127.0.0.1:<port>` or `localhost:<port>`.
- If the window cannot open (for example Linux without WebKitGTK), JDeck Desktop opens the page in the default browser.
- The update check stays manual. It compares the version with the latest release tag. Both editions share one version, so no file-name check is needed.

Removed from the current app:

- Network sharing, its password, its port and the shared-address pill.
- The close confirmation (`CLOSE_WARNING`).
- `--headless`. This moves to JDeck Server.

First run:

1. If no JDownloader address is saved, JDeck Desktop opens Settings.
2. The user types the address, for example `jd-host.local` or `jd-host.local:3128`.
3. The user selects **Test connection**.
4. JDeck Desktop shows one of three results: connected, no answer, or answer refused.

Connection help (shown when the test fails):

1. On the JDownloader PC, open Settings > Advanced Settings.
2. Type `deprecated` in the filter.
3. Turn on **RemoteAPI: Deprecated Api Enabled**.
4. Turn off **RemoteAPI: Deprecated Api Localhost Only**.
5. Restart JDownloader.

**Caution:** The JDownloader local API has no password. After step 4, every device on the network can control JDownloader. Do this only on a trusted network. To keep the API closed, use JDeck Server instead.

Data folder migration:

1. At start, if `JDeck-Desktop-data/` does not exist and `JDeck-data/` exists beside the program, JDeck Desktop copies the JDownloader address from `JDeck-data/settings.json`.
2. JDeck Desktop does not copy the sharing settings or the password.
3. JDeck Desktop does not change or delete `JDeck-data/`.

## 5. JDeck Server

JDeck Server runs in the background on the JDownloader machine. It serves the JDeck page to the network.

### 5.1 Listening

- One HTTP server on all interfaces, port `23128` by default.
- Port `23128` is below the temporary-port ranges of Linux (32768 and up) and Windows and macOS (49152 and up). It is not a JDownloader port (3128, 9666).
- If the port is in use, JDeck Server shows the error in a pop-up and quits.

### 5.2 JDownloader connection

JDeck Server and JDownloader run on the same machine. JDeck Server connects to `http://127.0.0.1:3128`.

1. In JDownloader, open Settings > Advanced Settings.
2. Type `deprecated` in the filter.
3. Turn on **RemoteAPI: Deprecated Api Enabled**.
4. Keep **RemoteAPI: Deprecated Api Localhost Only** turned on.
5. Restart JDownloader.

**Note:** With step 4, the JDownloader API stays closed to the network. Network devices reach JDownloader only through JDeck Server and its password.

### 5.3 Access

There are two kinds of request:

- **Server PC:** the request comes from `127.0.0.1` with the Host `127.0.0.1:<port>` or `localhost:<port>`.
- **Network device:** every other request.

| Action | Server PC | Network device |
|---|---|---|
| Use the JDeck page (add links, control downloads) | sign-in required | sign-in required |
| Open the control page (section 5.6) | yes | no (404) |
| Set the first password | yes | no |
| Quit JDeck Server from a page | yes | no |

- Until a password exists, network devices see a page that says "Set a password on the server PC first".
- Every POST request still needs the header `X-JDeck: 1`.

**Caution:** Do not put JDeck Server behind a reverse proxy on the same machine. The proxy sends every request from `127.0.0.1`, so every device counts as the server PC.

### 5.4 Sign-in

JDeck Server uses a sign-in page with sessions. It does not use the browser's Basic auth pop-up.

- `GET /login` shows the sign-in page. Every other page and API call without a valid session goes to `/login` (page) or returns 401 (API).
- `POST /api/login` checks the password against the PBKDF2-SHA256 hash. On success it sets the session cookie.
- Session cookie: `jdeck_session`, a random 32-byte token. Flags: `HttpOnly`, `SameSite=Strict`, `Path=/`. No `Secure` flag, because the connection is plain HTTP.
- Sessions are kept in memory only. A restart signs out every device.
- A session ends after 30 days without use.
- **Sign out** in the page header ends the session on that device.
- Changing the password ends every session.
- Wrong passwords: after 5 failures from one address, that address waits 30 s before the next try.

**Caution:** Traffic is plain HTTP. A device on the same network can read it. Use JDeck Server only on a trusted home network.

### 5.5 Tray icon

- Click the tray icon to open the control page (section 5.6) in the default browser.
- The tray menu has three items:

| Item | Action |
|---|---|
| Open JDeck Server | Opens the control page. This is the default (click) action |
| Open JDeck | Opens the JDeck page on the server PC |
| Quit JDeck Server | Stops the server and quits (section 5.7) |

**Note:** On macOS, a click on a menu-bar icon always opens the menu. **Open JDeck Server** is the first item.

### 5.6 Control page

The control page is `http://127.0.0.1:<port>/server`. It answers only on the server PC. It is a separate small page, `web/server.html`. It shares its styles with the JDeck page through `web/common.css`.

The control page shows:

| Item | Content |
|---|---|
| Status | Running, port, number of signed-in devices |
| JDownloader | Address and connected / not reachable. **Test connection** button |
| Network address | `http://<LAN IP>:<port>/`, a **Copy** button and a QR code for phones |
| Password | Set or change. Changing it signs out every device |
| Port | Change. JDeck Server restarts its listener on the new port |
| Start at login | On or off (section 5.8) |
| Version | Version number and **Check for updates** |
| Quit JDeck Server | Stops the server and quits (section 5.7) |

The QR code is made by JDeck Server (`server/qr.py`, standard library only) and served as an SVG image. Nothing loads from the internet.

First run:

1. JDeck Server starts and shows the tray icon.
2. If no password exists, JDeck Server opens the control page in the default browser.
3. The control page sends the browser to `/login`. On the server PC, `/login` asks for the first password. The user types it two times.
4. JDeck Server signs the server PC in and shows the control page.
5. Network devices can now sign in.

Headless first run (no desktop session):

1. Run `jdeck-server --set-password` in a terminal.
2. Type the password two times.
3. Run `jdeck-server --headless`.

### 5.7 Quit

**Quit JDeck Server** does these steps in order:

1. Stop accepting new connections.
2. Let open requests finish, for up to 3 s.
3. Save the settings.
4. Remove the tray icon.
5. Exit with code 0.

The same steps run on Ctrl+C, SIGTERM and macOS app quit.

Other ways to quit, for systems where the tray icon is not visible:

- **Quit JDeck Server** on the control page.
- `jdeck-server --stop` stops the running copy. At start, JDeck Server writes a random token to `JDeck-Server-data/control.token`. `--stop` sends it back on 127.0.0.1. The file is removed at quit.

### 5.8 Start at login

- Windows: a shortcut in the user's Startup folder.
- macOS: a LaunchAgent in `~/Library/LaunchAgents/`.
- Linux: a `.desktop` file in `~/.config/autostart/`.

**Note:** This is the one file JDeck Server writes outside its data folder. It is created only when the user turns the option on, and removed when the user turns it off.

### 5.9 Single instance

- At start, JDeck Server tries to bind the port. The port is exclusive: on Windows the server uses `SO_EXCLUSIVEADDRUSE`, because with `SO_REUSEADDR` a second copy could bind the same port without an error.
- If a JDeck Server already answers on that port, the new copy opens the control page and exits.
- If another program has the port, JDeck Server shows the "port in use" error.

### 5.10 Headless

`jdeck-server --headless` runs without a tray icon. It logs to the terminal and stops on Ctrl+C or SIGTERM. Use it on Linux servers, NAS devices and other machines without a desktop.

If the tray cannot start, JDeck Server falls back to headless and logs the reason.

## 6. Platforms

Both editions build for Windows x64, macOS Apple Silicon (arm64) and Linux x64.

| Topic | Windows | macOS | Linux |
|---|---|---|---|
| Desktop window | WebView2 | WebKit | WebKitGTK |
| Server tray | pystray (win32) | pystray (AppKit), main thread | pystray (xorg) |
| Server in Dock | n/a | hidden (`LSUIElement`) | n/a |
| Package | one `.exe` | `.app` in a `.zip` | one file in a `.tar.gz` |

**Note:** GNOME shows no tray icons without the AppIndicator extension. On GNOME, use `--stop` or the page to quit, or use `--headless`.

**Note:** On macOS the tray must run on the main thread. The HTTP server runs on a background thread.

## 7. Releases

One workflow builds both editions on every release. One version. One tag. One GitHub release.

Build matrix: 2 editions x 3 platforms = 6 jobs.

| Edition | Windows | macOS | Linux |
|---|---|---|---|
| Desktop | `JDeck-Desktop-vX.Y.Z-windows-x64.exe` | `JDeck-Desktop-vX.Y.Z-macos-arm64.zip` | `JDeck-Desktop-vX.Y.Z-linux-x64.tar.gz` |
| Server | `JDeck-Server-vX.Y.Z-windows-x64.exe` | `JDeck-Server-vX.Y.Z-macos-arm64.zip` | `JDeck-Server-vX.Y.Z-linux-x64.tar.gz` |

Workflow rules:

- Tests run once, before any build.
- The release is created only after all 6 builds pass and all 6 files are present.
- Each build runs `--self-check` for its edition. The Linux Server self-check runs under `xvfb-run`, because the tray library needs a display to load.
- Only Desktop builds install GTK and WebKitGTK on Linux.
- Each edition installs only its own packages: `requirements-desktop.txt` (pywebview) or `requirements-server.txt` (pystray, Pillow).
- Server builds name the pystray tray backend for PyInstaller (`--hidden-import pystray._win32`, `._darwin` or `._xorg`), because pystray picks it at run time.
- Program names: `JDeck` (Desktop) and `JDeck-Server` (Server). macOS shows "JDeck" and "JDeck Server" (`CFBundleDisplayName`).
- macOS Server: the workflow sets `LSUIElement` in `Info.plist` with `plutil` (menu-bar only, no Dock icon). (`*.spec` files are git-ignored, so the workflow does not use a spec file.)
- macOS: editing `Info.plist` breaks PyInstaller's ad-hoc signature. The workflow signs the `.app` again (`codesign --force --deep --sign -`) and verifies it, because Apple Silicon needs a valid signature to start the app.
- macOS bundle IDs: `io.github.jdeck.desktop` and `io.github.jdeck.server`.
- Release notes start with a "Which download do I need?" section and a table of the 6 files. GitHub's generated notes follow.

## 8. Files outside the code that change

| File | Change |
|---|---|
| `docs/app.js` | Download links per edition (section 8.1) |
| `docs/index.html` | Edition switch in the download section (section 8.1). The phone-sharing panel describes JDeck Server |
| `assets/make_screenshots.py` | Copies the new layout instead of `jdeck.py` |
| `assets/make_assets.py` | Not done yet: a server tray icon (small, monochrome on macOS). The tray uses the app icon for now. |
| `README.md` | "Which edition do I need?", setup for each edition, the port, the API caution |
| `.gitignore` | Add `JDeck-Desktop-data/` and `JDeck-Server-data/`. Keep `JDeck-data/` |
| `requirements.txt` | Split into `requirements-desktop.txt` and `requirements-server.txt` (done in step 6) |

### 8.1 Website downloads

The visitor sees 3 download links at a time, not 6.

- The download section has one switch above the 3 platform cards: **Desktop | Server**. **Desktop** is selected by default.
- Each mode has one line under the switch:
  - Desktop: "For the PC you use. Connects to JDownloader on your network."
  - Server: "For the JDownloader machine. Use JDeck from any browser at home."
- The 3 platform cards stay as they are. The switch changes the file each card links to, and its size.
- The card for the visitor's OS stays highlighted.
- The hero button downloads JDeck Desktop for the visitor's OS. A small link under it says "Need the server version?" and selects **Server** in the download section.
- A link with `#download-server` opens the page with **Server** selected.

File matching in `docs/app.js`:

- Each edition has one pattern per platform, for example `^JDeck-Desktop-v.*-windows-x64\.exe$`.
- Old releases (v1.0.0) have no edition in the file name. If the latest release has no `JDeck-Desktop-` files, **Desktop** uses the old names `^JDeck-v.*`.
- If the latest release has no `JDeck-Server-` files, the switch is hidden.
- If the GitHub request fails, every link goes to the Releases page, as now.

**Note:** Because of the fallbacks, the website change can be published before the first two-edition release.

## 9. Tests

- `test_core.py`: link extraction, address parsing, JDownloader calls with a fake JDownloader, settings, password hashing, update check, Host check, CSRF header.
- `test_desktop.py`: no network listener, first-run state, data folder migration.
- `test_server.py`: sign-in required, session cookie flags, session expiry, sign out, password change ends sessions, wrong-password delay, control page only from the server PC, first password only from the server PC, single instance, clean shutdown, port in use.
- The 35 current tests move into these files. None is deleted without a reason in the commit message.

## 10. Refactor order

Each step ends with all tests passing.

1. Move shared code from `jdeck.py` into `core/`. No behaviour change.
2. Add `desktop/` with the current window code. Remove network sharing. Add the first-run Settings and **Test connection**.
3. Add `server/` with headless mode, access rules, sign-in and sessions.
4. Add the control page, the tray, quit, single instance and start at login.
5. Add the `features` object to `/api/state` and the matching changes in `web/index.html`.
6. Change the release workflow to the 6-build matrix.
7. Update the website (section 8.1), screenshots, README and context file.

## 11. Later, not part of this refactor

- Desktop-only features, for example download-finished notifications.
- HTTPS for JDeck Server.
