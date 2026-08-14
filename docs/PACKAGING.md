# Packaging & Distributing Alpha Research as a Desktop Product

This guide explains how to package **Alpha Research** as a single, standalone desktop application (`.exe` on Windows, binary on macOS and Linux) that can be distributed to end users without requiring Python, terminal setup, or command-line commands.

---

## 1. How It Works

When bundled into a standalone executable:
1. **Self-Contained Python & Server**: PyInstaller packages Python 3.11, FastAPI, Uvicorn, SQLAlchemy, and Alembic into a single executable binary.
2. **Embedded Frontend & Assets**: The single-page HTML frontend (`index.html`), operator knowledge bases (`operators/`), field catalogs (`fields/`), and Alembic database migrations (`migrations/`) are bundled directly inside the executable.
3. **Automatic Data Initialization**: On first run, the executable creates a persistent user data directory (`~/.alpha_research/` or `%APPDATA%\alpha_research`), runs database schema migrations automatically, and seeds default operators and lookups.
4. **Auto Browser Launch**: The application finds an available local port (e.g. `http://127.0.0.1:8000`), starts the FastAPI web server, and opens the user's default web browser to the Alpha Research interface.

---

## 2. Prerequisites for Building

Ensure you have installed the project's virtual environment and dependencies:

```bash
cd backend
uv venv --python 3.11 .venv
VIRTUAL_ENV=.venv uv pip install -e ".[dev]"
```

Verify `pyinstaller` is installed:

```bash
.venv/bin/pyinstaller --version
```

---

## 3. Building the Standalone Desktop Executable

Run the automated build script:

```bash
cd backend
.venv/bin/python -m scripts.build_desktop
```

This generates the standalone binary in the `backend/dist/` directory:
- **macOS / Linux**: `backend/dist/alpha-research-desktop`
- **Windows**: `backend/dist/alpha-research-desktop.exe`

---

## 4. Running the Standalone Executable

You can execute the binary directly from terminal or double-click it in your file manager / Finder:

```bash
# macOS / Linux
./dist/alpha-research-desktop

# Windows (Command Prompt / PowerShell)
.\dist\alpha-research-desktop.exe
```

When launched:
- Terminal output logs database initialization & server status.
- The web browser opens automatically to `http://127.0.0.1:8000`.
- User data (database `wq.db` and `.env` settings) are saved in `~/.alpha_research/`.

---

## 5. Cross-Platform Building & Distribution

> [!NOTE]
> PyInstaller creates binaries for the OS it is run on. To build native executables for Windows, macOS, or Linux, run the build command on that platform (or inside a GitHub Actions CI matrix).

### Building macOS `.dmg` or `.app` Bundle
To create a double-clickable `.app` or installer `.dmg` on macOS:
1. Run `python -m scripts.build_desktop` on a macOS host.
2. Optionally wrap `dist/alpha-research-desktop` using `create-dmg`:
   ```bash
   brew install create-dmg
   create-dmg 'Alpha Research.dmg' 'dist/alpha-research-desktop'
   ```

### Building Windows `.exe` Installer
To create an installer on Windows:
1. Run `python -m scripts.build_desktop` on a Windows host.
2. Optionally create a standard Windows installer using [Inno Setup](https://jrsoftware.org/isinfo.php) or [NSIS](https://nsis.sourceforge.io/).

---

## 6. Verification & Automated CI Packaging

Add a GitHub Actions workflow step (`.github/workflows/package.yml`) to automatically build and upload binary releases on every git release tag:

```yaml
name: Build Desktop Releases
on:
  push:
    tags: ['v*']

jobs:
  build:
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: |
          cd backend
          uv venv
          uv pip install -e ".[dev]"
          python -m scripts.build_desktop
```
