# Publishing

The npm package is published by CI, from a git tag.

```
git tag → .github/workflows/publish.yml → npm publish
```

## The 2026 rules, in one place

npm changed its authentication rules twice in 2026. Both matter here.

| Rule | What it means |
|---|---|
| **Trusted publishing (OIDC)** is the recommended way to publish from CI. The npm CLI exchanges the workflow's OIDC token for a short-lived registry token; no long-lived secret sits in the repository. | Needs npm CLI **11.5.1+**, Node **22.14.0+**, `id-token: write`, a **public** repo and a **public** package, and a GitHub-hosted runner. |
| **Provenance is automatic** under trusted publishing. You do not pass `--provenance`. | A public repo + public package is what makes the attestation possible. |
| **Staged publishing** (`npm stage publish` → approve with 2FA) puts a human in the loop. | Works for a *new* package too: npm publishes a `0.0.0-stage` placeholder so the package exists, then the approved version goes live. |
| **Bypass-2FA tokens lose direct publish in January 2027.** | Move automation to OIDC or to staged publishing. A token with direct-publish access is on its way out. |
| **A trusted publisher must publish once within 2 days** of being created, or it expires. | Create it when you are ready to release, not weeks in advance. |
| **Up to 10 trusted publishers per package**, and the fields of an existing one cannot be edited — only revoked and recreated. | Useful when a project publishes from several workflows or providers. |

Sources: [trusted publishing](https://docs.npmjs.com/trusted-publishers),
[staged publishing](https://docs.npmjs.com/staged-publishing),
[access tokens](https://docs.npmjs.com/about-access-tokens),
[`npm trust`](https://docs.npmjs.com/cli/v12/commands/npm-trust).

## Before the first release

npm's **trusted publishing** (OIDC) refuses to publish a package that does not
exist yet — npm states this in three places:

* the `npm trust` documentation: *"Package must exist: The package you're
  configuring must already exist on the npm registry."*
* the registry API spec for `POST /-/package/{package}/trust`:
  *"Requirements — Package MUST exist"*
* the OIDC exchange endpoint, which answers *"Package not found"*

So the **first** release needs either a token, or the staged-publishing
placeholder. Everything after that can run without a secret.

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

This token is for **one** publish. Delete it in step 5.

### 3. Publish

```bash
./scripts/publish-npm.sh          # tags and pushes; CI publishes
./scripts/publish-npm.sh --dry-run   # CI packs without publishing
```

The workflow's version gate refuses a tag that does not match the version in
`package.json`, so the tag and the package can never disagree.

### 4. Set up the trusted publisher (once)

Now that the package exists on the registry, npm will accept an OIDC publish
from this workflow. Either on npmjs.com — Packages → *hermes-continue-hook* →
Settings → **Trusted Publisher** → GitHub Actions — with:

| Field | Value |
|---|---|
| Organization or user | `ttxs69` |
| Repository | `hermes-continue-hook` |
| Workflow filename | `publish.yml` |
| Environment name | `npm` |
| Allowed actions | `npm publish` (and `npm stage publish`, which is always allowed) |

or from the CLI (npm 11.15.0+, 2FA enabled on the account):

```bash
npm trust github hermes-continue-hook \
  --file publish.yml \
  --repo ttxs69/hermes-continue-hook \
  --env npm \
  --allow-publish
```

Every field is matched **exactly** and case-sensitively against the workflow
that runs the publish. The `environment: npm` on the publish job is part of
that identity — if the two disagree, the publish is rejected with a 404 and
the reason is not printed anywhere useful.

**Do this and the first publish within 2 days** — a trusted publisher that has
not published once expires.

### 5. Remove the token

```bash
gh secret delete NPM_TOKEN
```

From here the workflow authenticates with OIDC alone. The `NODE_AUTH_TOKEN`
environment variable stays in the workflow (npm reads it, and it is empty);
the secret behind it is gone.

Then, for the strongest posture, set the package's *Publishing access* on
npmjs.com to **"Require two-factor authentication and disallow tokens"** and
give the trusted publisher *stage-only* permissions. Every release then goes
through `npm stage publish` and waits for a maintainer to approve it with 2FA.

**Publish against npmjs.org explicitly.** A machine-level `~/.npmrc` that
points at a registry mirror (mirrors, corporate proxies) would publish into
that mirror, and npm's OIDC exchange derives its audience from the registry
host — a mirror breaks it. The workflow passes
`--registry=https://registry.npmjs.org/` for that reason. The same applies to
`npm trust` below.

### Staged publishing (the stronger option)

`npm stage publish` uploads a version to a staging area instead of
publishing it. A maintainer then approves it with 2FA, and only then does it
go live.

```bash
npm stage publish              # in CI, or locally
npm stage list                 # what is pending
npm stage view <stage-id>      # inspect it
npm stage download <stage-id>  # get the tarball
npm stage approve <stage-id>   # 2FA — this is what publishes it
npm stage reject <stage-id>    # 2FA — discard it
```

Only `approve` and `reject` require 2FA; staging itself does not, which is
the point — the CI job never has to be a human.

For a **new** package, staging creates a `0.0.0-stage` placeholder so the
package exists on the registry. That is what makes step 4 possible at all.

To use it: give the trusted publisher *stage-only* permissions (allow
`npm stage publish`, not `npm publish`) and set the package's *Publishing
access* on npmjs.com to **"Require two-factor authentication and disallow
tokens"**. Every release then waits for a maintainer.

## Publishing access on the package

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

## Verifying and troubleshooting

```bash
npm view hermes-continue-hook dist.attestations   # provenance attached?
npm audit signatures                               # does it verify?
npm view hermes-continue-hook versions --json      # what is on the registry
```

| Symptom | Cause |
|---|---|
| `404` / "Package not found" on publish | the trusted publisher does not match the workflow: check repo, workflow **filename**, and environment name, character by character |
| `ENEEDAUTH` | `id-token: write` missing, or the runner is self-hosted (not supported) |
| `E_STAGE_REQUIRED` | the token is stage-only; use `npm stage publish` |
| provenance missing | private repo, private package, or the publish did not go through OIDC |
| `npm trust` says the package must exist | it does not — publish once with a token first, or stage it to create the `0.0.0-stage` placeholder |
| trusted publisher stopped working after a while | it expired: a configuration must complete its first publish within 2 days, and an unvalidated one cannot be edited — revoke it and create a new one |
