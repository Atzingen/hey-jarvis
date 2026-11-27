# Releasing Jarvis

How a version goes from `main` to the people who installed the plugin. Read it before pushing
to `stable`: **a push to `stable` is a release**, because that is what users get.

## How users receive code

| Channel | What it reads | Effect of a push |
|---|---|---|
| `omarchy plugin add` | a `git clone` of the repository's **default branch**, `stable` | new installs get the new code at once |
| `omarchy plugin update` | `git fetch origin HEAD` + `git merge --ff-only` | existing installs get the new panel at once; the voice service changes only after `install.sh --update` |
| Marketplace listing ([omarchyplugins.com](https://omarchyplugins.com/plugin.html?id=atzingen.jarvis)) | a **verified snapshot** pinned to one commit | nothing until the update is verified (below); meanwhile the page says *Update unverified* |
| Manual install | whatever the user checks out | — |

Consequences:

- `main` is the integration branch. `stable` only ever moves by **fast-forward** to a commit
  that already passed the checklist below.
- **Never rewrite `stable`** (no force-push, no reset): `omarchy plugin update` is
  fast-forward only and would fail for everyone. Roll back with a revert commit and a patch
  version.
- Tags do nothing for the marketplace; they mark what was released and feed the changelog
  links and GitHub Releases.

## Version numbers

The only version source is `version` in `manifest.json`, shown in the marketplace listing.

- **Patch** (`2.5.1`): fixes only, no new setting, no behaviour change a user would notice
  besides the fix.
- **Minor** (`2.6.0`): new features or settings, and behaviour changes that keep existing
  `config.toml` files working (old keys still read, safe defaults).
- **Major** (`3.0.0`): a change that breaks an existing configuration or removes a feature
  without a migration.

Every push to `stable` that changes behaviour gets a new number, even a one-line fix. Version
2.4.0 received three fixes after release without a bump, which left users and the listing
unable to tell the two apart.

## Changelog

`CHANGELOG.md` follows Keep a Changelog. Each change that reaches `main` adds a line under
**Unreleased** (Added / Changed / Fixed / Security / Removed), written for users: what they
notice, which setting, which command. On release day those lines move to a new
`## [X.Y.Z] — YYYY-MM-DD` section, **Unreleased** stays on top (empty), and the compare links
at the bottom get the new tag.

## Checklist before `stable`

Run from the release branch (`release/X.Y.Z`) or from `main` with the release merged.

### Automated

```bash
~/.local/share/jarvis/venv/bin/python -m unittest discover -s tests          # all OK
QT_QPA_PLATFORM=offscreen /usr/lib/qt6/bin/qmltestrunner -import app -input tests
omarchy plugin validate "$PWD"                                             # exit 0
bash -n install.sh scripts/install-router-local.sh bin/jarvis bin/dev-layout bin/voice-launcher
~/.local/share/jarvis/venv/bin/python -m py_compile bin/*.py scripts/*.py
find . -path ./.git -prune -o -type l -print                               # must print nothing
python3 -m json.tool manifest.json >/dev/null
```

- [ ] All of the above pass, with no known failure carried over.
- [ ] `manifest.json` version bumped; `CHANGELOG.md` section dated.
- [ ] `README.md` and `README.pt-BR.md` describe the same features; every new setting is in
      `docs/reference.en.md`.
- [ ] `SECURITY.md` is literally true for this commit: new endpoints, new files written,
      new CLI flags, anything that reaches the model's prompt. The marketplace reviewer checks
      its claims against the code (`grep -- --dangerously` included).
- [ ] New downloads are pinned (version or revision), checksummed and size-capped; new
      Python dependencies are in the hash-locked `requirements*.lock`.
- [ ] The marketplace's own validator accepts the commit. `omarchy plugin validate` does not
      check every marketplace rule (the manifest `description` is limited to 500 characters,
      covered by `tests/test_manifest.py`); after pushing, run the marketplace's
      `scripts/build-catalog.mjs` inspection locally if the issue bot reports
      `update-compatibility-invalid`, since the bot does not say why.

### On a real machine

Install the release on the maintainer's desktop first (`bash install.sh --update` from the
release checkout) and use it for at least a day. Then check, in both languages where it applies:

- [ ] `systemctl --user is-active voice-launcher.service` is `active`; `jarvis log` has no
      traceback after start.
- [ ] Wake word → question → spoken answer → follow-up without the wake word → “end”.
- [ ] Barge-in: talking over an answer stops it.
- [ ] Codex: a question that needs a command, a long task that opens the terminal, a
      follow-up in the same session.
- [ ] Claude: the same three; the window must say native session, not compatibility.
- [ ] `system_access = "ask"`: the consent window shows the exact command; `n` denies, timeout
      denies. `full` and `off` behave as documented.
- [ ] Dictation: `Ctrl+Shift+K` toggle, `Ctrl+Shift+L` hold, `Esc` during polish,
      `dictation_live` into an editor, focus change stops insertion.
- [ ] Each STT backend in use (`local`, `nemotron`, `openai`) plus the fallback to Whisper
      (stop the backend mid-session).
- [ ] Bar widget: tooltip, panel, switches, settings screen saves and restarts.
- [ ] Picoh (if connected): phases, eyes dim, mouth follows the voice.
- [ ] Record the CLI versions tested (`claude --version`, `codex --version`) in the README's
      *Terminal conversations* section.

For changes to `install.sh`, also test a fresh install on a clean user account:
`omarchy plugin add …`, **Install** from the panel, first conversation, then
`install.sh --uninstall --purge`.

## Publishing

```bash
git switch main && git merge --no-ff release/X.Y.Z
git push origin main
git switch stable && git merge --ff-only main && git push origin stable
git tag -a vX.Y.Z -m "Jarvis X.Y.Z" stable && git push origin vX.Y.Z
awk '/^## \[X.Y.Z\]/{f=1; next} /^## \[/{f=0} f' CHANGELOG.md > /tmp/jarvis-notes.md
gh release create vX.Y.Z --title "Jarvis X.Y.Z" --notes-file /tmp/jarvis-notes.md
git rev-parse stable     # the 40-character SHA for the marketplace
```

Then ask the marketplace to verify the new commit: open the
[plugin verification form](https://github.com/omacom/omarchy-plugin-marketplace/issues/new?template=verify-plugin.yml),
choose **Verify and publish a newer upstream commit**, plugin ID `atzingen.jarvis`, repository
`https://github.com/Atzingen/hey-jarvis`, target commit = the SHA above.

- Bots run compatibility validation and the security baseline on that exact commit. A
  `review-required` result waits for a maintainer, who may ask for changes.
- **Answer every review round within 7 days**; an unanswered request is closed.
- After a fix: bump the patch version, push `main` and `stable`, and **edit the issue** with
  the new SHA (editing is what re-runs the bots).
- Done when the listing shows *Snapshot verified* for the new commit
  (`verificationCoverage: snapshot-verified` in the catalog).

## After publishing

- Delete merged feature branches and their worktrees.
- If something breaks for users: revert on `main`, release a patch version through the same
  steps. Never force-push `stable`.
