# Publishing

The npm package is published by CI, from a git tag.

```
git tag → .github/workflows/publish.yml → npm publish --provenance
```

## Before the first release

npm's **trusted publishing** (OIDC) refuses to publish a package that does not
exist yet — npm states this in three places:

* the `npm trust` documentation: *"Package must exist: The package you're
  configuring must already exist on the npm registry."*
* the registry API spec for `POST /-/package/{package}/trust`:
  *"Requirements — Package MUST exist"*
* the OIDC exchange endpoint, which answers *"Package not found"*

So the **first** release needs a token. Everything after that can run without
a secret.

### 1. Create the npm account and a token

Only the account owner can do this — it is an identity action.

1. Sign up: <https://www.npmjs.com/signup>
2. Turn on **2FA** for the account (auth-and-writes). Configuring trusted
   publishing requires an interactive 2FA challenge.
3. Create a token: <https://www.npmjs.com/settings/ttxs69/access-tokens>

> npm removed legacy automation tokens in November 2025 — what the site offers
> now is a **Granular Access Token** (GAT).

| Field | Value | Why |
|---|---|---|
| Packages and scopes → Permissions | **Read and write (publish and stage)** | a stage-only token is rejected with `E_STAGE_REQUIRED` |
| Select Packages | **All Packages** | the package does not exist yet, so it cannot be picked by name |
| Bypass two-factor authentication | **on** | a non-interactive publish needs it |
| Expiration | as short as possible | revoke it right after the first release |

### 2. Store it in the repository

```bash
gh secret set NPM_TOKEN
```

### 3. Publish

```bash
./scripts/publish-npm.sh          # tags and pushes; CI publishes
./scripts/publish-npm.sh --dry-run   # CI packs without publishing
```

The workflow's version gate refuses a tag that does not match the version in
`package.json`, so the tag and the package can never disagree.

**Publish against npmjs.org explicitly.** A machine-level `~/.npmrc` that
points at a registry mirror (mirrors, corporate proxies) would publish into
that mirror, and npm's OIDC exchange derives its audience from the registry
host — a mirror breaks it. The workflow passes
`--registry=https://registry.npmjs.org/` for that reason. The same applies to
`npm trust` below.

### 4. Switch to trusted publishing (optional, and better)

After the package exists, configure the trusted publisher once and remove the
long-lived secret:

```bash
npm trust github hermes-continue-hook \
  --repo ttxs69/hermes-continue-hook \
  --file publish.yml \
  --env npm \
  --allow-publish \
  --registry=https://registry.npmjs.org/
```

(`npm trust` needs **npm ≥ 11.15.0** and Node ≥ 22.14.0; a GAT with
*bypass 2FA* may **not** configure a trusted publisher — that call requires an
interactive 2FA challenge, which the CLI prompts for and the website asks
for.)

The equivalent web flow: npmjs.com → your package → **Settings** →
**Trusted publishing** → *Select your publisher* → GitHub Actions, with

| Field | Value |
|---|---|
| Organization or user | `ttxs69` |
| Repository | `hermes-continue-hook` |
| Workflow filename | `publish.yml` (the file name only) |
| Environment name | `npm` (the environment this repository already has) |
| Allowed actions | `npm publish` |

Two things npm does not tell you on the form: the configuration **expires if
no successful publish happens within two days**, and npm does **not** validate
what you type — the fields are case-sensitive and must match exactly, or the
publish fails with `ENEEDAUTH`.

From then on the workflow exchanges the job's OIDC token for a short-lived
registry token — no secret in the repository — and npm attaches the
provenance statement automatically (no `--provenance` flag needed).

### Publishing access on the package

After the first release, the package's *Publishing access* setting can be
tightened to **"Require two-factor authentication and disallow tokens"** —
that does not affect trusted publishing.

## Provenance

* With **trusted publishing**, npm generates the provenance statement
  automatically — the flag is not needed.
* With a **token**, ask for it explicitly: `npm publish --provenance`, which
  needs npm CLI ≥ 9.5.0, a cloud-hosted runner, `id-token: write`, and a
  **public** `repository` field in `package.json` that matches the publish
  location exactly. Private repositories never get provenance.

## What the workflow checks

| Step | What it proves |
|---|---|
| version-gate | the pushed tag equals the version in `package.json` |
| `npm view … versions` | what is already published |
| `npm pack --dry-run` | the tarball contents are the ones we expect |
| `npm publish --provenance` | the release, with a provenance statement |

The CI workflow (`ci.yml`) additionally asserts the tarball carries the
runtime and contains no build artefacts.
