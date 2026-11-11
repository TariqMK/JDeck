![JDeck. JDownloader from any device on your network.](assets/banner.png)

A modern desktop app for JDownloader 2. Send links and watch your downloads from any computer on your home network. Phones and tablets can use it too, if you turn that on.

JDeck talks to JDownloader through its local API. It does not use MyJDownloader or any cloud service. No account. No tracking. Everything stays on your network.

**Website:** [tariqmk.github.io/JDeck](https://tariqmk.github.io/JDeck/)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/downloads-dark.png">
  <img src="assets/screenshots/downloads-light.png" alt="JDeck window: the Add links box and the download list with one running, one finished and one failed package" width="840">
</picture>

## Screenshots

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/add-links-dark.png">
  <img src="assets/screenshots/add-links-light.png" alt="JDeck with two pasted links, two packages waiting in the LinkGrabber and the download list" width="840">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/screenshots/settings-dark.png">
  <img src="assets/screenshots/settings-light.png" alt="JDeck Settings: the JDownloader address and the option to let other devices on the network use JDeck" width="840">
</picture>

## Features

- Paste links, or any text that contains links. JDeck finds the links and removes duplicates.
- Start the downloads immediately, or hold them in the LinkGrabber.
- Set a package name and a save folder (optional).
- See the LinkGrabber. Start or remove each package.
- See the download list with progress, size, speed and time left. The list refreshes every 1.5 seconds.
- Start, pause and stop all downloads.
- Optional: let phones, tablets and other computers use JDeck in a browser, with a password. Off by default.
- Light and dark mode.

## Download

Get the latest version from the [Releases page](../../releases/latest).

| Platform | File |
|---|---|
| Windows (x64) | `JDeck-vX.Y.Z-windows-x64.exe` |
| macOS (Apple Silicon) | `JDeck-vX.Y.Z-macos-arm64.zip` |
| Linux (x64) | `JDeck-vX.Y.Z-linux-x64.tar.gz` |

**Note:** The builds are not code-signed.
- Windows: SmartScreen can show a warning. Select **More info**, then **Run anyway**.
- macOS: The first time you open the app, macOS blocks it. Open **System Settings > Privacy & Security** and select **Open Anyway**.

**Note:** JDeck uses the web engine that is part of the system.
- Windows: Microsoft Edge WebView2. It is part of Windows 10 and Windows 11.
- macOS: WebKit. It is part of macOS.
- Linux: WebKitGTK. Install it if it is not there, for example `sudo apt install gir1.2-webkit2-4.1` on Ubuntu. Without it, JDeck opens in your web browser instead.

## Setup

### 1. Turn on the JDownloader local API

Do these steps on the computer or container that runs JDownloader 2.

1. In JDownloader, open **Settings > Advanced Settings**.
2. In the filter box, type `deprecated`.
3. Turn on **RemoteAPI: Deprecated Api Enabled**.
4. Turn off **RemoteAPI: Deprecated Api Localhost Only**.
5. Make sure **RemoteAPI: Deprecated Api Port** is `3128`.
6. Restart JDownloader.

**Note:** The name "Deprecated API" is historical. It is JDownloader's local API.

If JDownloader runs in Docker, publish port `3128` from the container.

**Caution:** The JDownloader local API has no password. Any device that can reach port 3128 can control JDownloader. Allow port 3128 only from computers you trust, for example with a firewall rule.

### 2. Start JDeck

1. Put the JDeck file in a folder you can write to, for example `Documents\JDeck`.
2. Start JDeck. The JDeck window opens.
3. Select **Settings**.
4. In **JDownloader address**, type the address of the JDownloader computer and port, for example `jd-host.local:3128`.
5. Select **Save**.

To use JDeck on another computer, copy JDeck to that computer and do the same steps. Each copy talks to JDownloader directly.

Closing the window closes JDeck.

### 3. Use JDeck on phones and tablets (optional)

Network sharing is off by default. When it is off, JDeck opens no network port, and only its window can use it.

1. In the JDeck window, select **Settings**.
2. Select **Let other devices on my network use JDeck**.
3. In **Password for other devices**, type a password.
4. Change **Port** only if `8080` is in use.
5. Select **Save**. The window header shows the address, for example `http://<this-computer>:8080`.
6. On the phone or tablet, open that address in a browser.
7. Type the password when the browser asks. Any user name works.

**Note:** Other devices can use JDeck only while the JDeck window is open. When sharing is on, JDeck asks before it closes.

**Note:** If the page does not open on another device, allow JDeck through the firewall of the computer that runs it.

Only the JDeck window can turn sharing on or off, or change the password and port.

### Run without a window (servers)

To run JDeck on a computer without a desktop, for example a home server, start it with `--headless`. JDeck then shares the page on your network only. The first time, it asks for a password in the terminal. Press **Ctrl+C** to stop it.

## Privacy

- No telemetry, analytics or tracking.
- No account and no cloud service. JDeck does not use MyJDownloader.
- JDeck makes these network connections only:
  - It connects to the JDownloader address you set, to send links and read the download list.
  - Only if you turn on network sharing: it serves the JDeck page on your network (port `8080` by default).
- The JDeck window talks to JDeck through `127.0.0.1` only. Other computers cannot reach it.
- Settings are stored in the `JDeck-data` folder next to the program. The password is stored as a salted hash, not as text.
- To move JDeck to another computer, copy the program and the `JDeck-data` folder.

**Note:** Network sharing uses plain HTTP. On your network, the password is encoded but not encrypted.

## Settings

| Setting | What it does |
|---|---|
| **JDownloader address** | The computer that runs JDownloader and its local API port. Default: `127.0.0.1:3128`. |
| **Let other devices on my network use JDeck** | Turns network sharing on or off. Default: off. |
| **Password for other devices** | The password other devices type to use JDeck. Required to turn on sharing. Leave empty to keep the current password. |
| **Port** | The network port for sharing. Default: `8080`. |

## Run From Source

Requires Python 3.10 or later.

1. Create a virtual environment and install the packages:
   ```bash
   python -m venv .venv
   .venv/Scripts/python -m pip install -r requirements.txt
   ```
   On macOS and Linux, use `.venv/bin/python`. On Linux, also install `pywebview[gtk]` and the WebKitGTK system packages.
2. Start JDeck:
   ```bash
   .venv/Scripts/python jdeck.py
   ```
   To run without a window, add `--headless`.

### Run the tests

```bash
python -m unittest discover -s tests
```

## Update the Screenshots

The screenshots and brand images are made by scripts. They use made-up sample data and a fake JDownloader. They do not read or change your `JDeck-data` folder.

1. Install the tools. They are only needed for these scripts:
   ```bash
   python -m pip install PySide6 Pillow
   ```
2. To retake the screenshots in `assets/screenshots/`, run:
   ```bash
   python assets/make_screenshots.py
   ```
3. To rebuild the icon files, the banner and the social card from `assets/icon/jdeck.svg`, run:
   ```bash
   python assets/make_assets.py
   ```
   This also refreshes the website's copies in `docs/` (`icon.svg`, `apple-touch-icon.png` and `social-card.png`).

## Website

The website is one page in `docs/`: `index.html`, `style.css` and `app.js`. It has no build step and loads nothing from other sites. The only outside request is to the GitHub API, to read the latest release.

### Preview the website

1. Start a local server:
   ```bash
   python -m http.server 8765 --bind 127.0.0.1 --directory docs
   ```
2. Open `http://127.0.0.1:8765/` in a browser.

**Note:** Opening `docs/index.html` as a file does not load the styles and scripts. Use the local server.

### Publish the website

Do these steps one time:

1. On GitHub, open **Settings > Pages**.
2. In **Source**, select **Deploy from a branch**.
3. Select the branch **main** and the folder **/docs**. Select **Save**.
4. On the repository page, select the gear next to **About**. Select **Use your GitHub Pages website**.

After that, each push to `main` updates the website.

The download buttons find files on the latest release by the end of their names (`windows-x64.exe`, `macos-arm64.zip`, `linux-x64.tar.gz`).

**Note:** The latest release does not include drafts or pre-releases. Until a public release exists, all download buttons open the Releases page.

## Creating a Release

Releases are built by GitHub Actions. The workflow builds Windows x64, macOS Apple Silicon and Linux x64, and attaches the three files to a GitHub release.

1. Push your changes to `main`.
2. On GitHub, open **Actions > Release**.
3. Select **Run workflow**.
4. Select the release type:
   - **draft**: creates a hidden release. Check it, then publish it on the Releases page.
   - **public**: publishes the release immediately.
5. Select the version bump: **patch** (1.2.3 to 1.2.4), **minor** (1.2.3 to 1.3.0) or **major** (1.2.3 to 2.0.0).
6. Select **Run workflow**.

The workflow:
- Runs the tests. If a test fails, nothing is built.
- Finds the highest existing `vX.Y.Z` tag or release (drafts included) and adds the bump.
- Writes that version into the build. The source code keeps the development version `0.0.0.dev0`.
- Creates the tag on the built commit only after all three builds succeed.
- Never commits or pushes to any branch.

No secrets are needed.

## Disclaimer

JDeck is an independent project. It is not affiliated with or endorsed by AppWork GmbH or JDownloader.
