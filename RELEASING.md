# Releasing a new YellowLink version

You don't need to build anything on your own PC. GitHub builds `YellowLink.exe`
for you every time you push a version tag.

## 1. Bump the version number (two places)

1. **Client:** open `client/yellowlink/__init__.py` and change
   ```python
   __version__ = "0.1.0"
   ```
   to the new version, e.g. `"0.1.1"`.
2. **Server:** on the machine running the relay server, open its `config.json`
   and set `"latest_client"` to the same version:
   ```json
   "latest_client": "0.1.1",
   "download_url": "https://github.com/<owner>/<repo>/releases/latest"
   ```
   Restart the server afterwards (`docker compose restart` or
   `sudo systemctl restart yellowlink`). Do this **after** step 4 has finished,
   so nobody gets told to update before the download exists.

Use normal version numbers: `MAJOR.MINOR.PATCH` (0.1.0 -> 0.1.1 for fixes,
0.2.0 for new features).

## 2. Commit

```bash
git add -A
git commit -m "Release 0.1.1"
git push
```

## 3. Tag and push the tag

```bash
git tag v0.1.1
git push --tags
```

The tag **must** be `v` + exactly the number in `__version__`. If they don't
match, the build stops with a red error telling you so. Fix `__version__`,
commit, delete the bad tag (`git tag -d v0.1.1 && git push --delete origin v0.1.1`)
and tag again.

## 4. Wait for the build

Open the repository on GitHub -> **Actions** tab -> "Release". It takes about
5-10 minutes. Green check = done. Red X = click it to read the error.

When it's green, the **Releases** page (right side of the repo's main page)
has a new release `YellowLink 0.1.1` with `YellowLink.exe` attached.

You can also run the build without making a release: Actions -> Release ->
"Run workflow". The exe then appears under "Artifacts" at the bottom of the
run page (handy for testing before you tag).

## 5. Friends download it

Friends download `YellowLink.exe` from the Releases page and replace their old
one. Their settings, ROM path and save files are stored separately
(`%APPDATA%\YellowLink\`), so replacing the exe loses nothing.

Direct link to always the newest exe:
`https://github.com/<owner>/<repo>/releases/latest/download/YellowLink.exe`

Windows SmartScreen may warn "Windows protected your PC" because the exe is
not code-signed: click **More info -> Run anyway**.

## How the in-app update notice works

When a client connects, the server's `welcome` message includes
`latest_client` and `download_url` from the server config. The client compares
`latest_client` with its own `__version__`; if the server's number is higher,
it shows "Update 0.1.1 available - press U." in the chat, and pressing **U**
opens `download_url` in the browser. That is why the
server's `latest_client` is bumped last: it is the switch that tells everyone
to update.

## Private repository?

If the GitHub repository is **private**, only people with access can download
releases. Either:

- add each friend as a collaborator (repo -> Settings -> Collaborators ->
  Add people; they need a free GitHub account), or
- make the repository public (Settings -> General -> Danger Zone -> Change
  visibility). This is fine: the repo contains no ROM and no Nintendo assets.
  `.gitignore` blocks `*.gb`, `*.gbc`, save files and `config.json` (which
  holds the room password) from ever being committed. Never commit those.
