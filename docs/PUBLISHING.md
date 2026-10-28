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

So the **first** release needs an automation token. Everything after that can
run without a secret.

### 1. Create the npm account and a token

Only the account owner can do this — it is an identity action.

1. Sign up: <https://www.npmjs.com/signup>
2. Create an **automation token** (or a granular token with *Packages: read &
   write* and *publish: yes*):
   <https://www.npmjs.com/settings/ttxs69/access-tokens>

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

### 4. Switch to trusted publishing (optional, and better)

After the package exists, configure the trusted publisher once and remove the
long-lived secret:

```bash
npm trust hermes-continue-hook \
  --repo ttxs69/hermes-continue-hook \
  --file publish.yml \
  --env npm \
  --allow-publish
```

From then on the workflow exchanges the job's OIDC token for a short-lived
registry token — no secret in the repository, and every publish carries a
provenance statement that points back at this commit.

## Provenance

`npm publish --provenance` needs:

* npm CLI ≥ 9.5.0 (the workflow installs the latest),
* a cloud-hosted runner (GitHub Actions qualifies),
* `id-token: write` in the workflow permissions (set),
* a **public** `repository` field in `package.json` that matches the publish
  location exactly (set — the repository is public).

## What the workflow checks

| Step | What it proves |
|---|---|
| version-gate | the pushed tag equals the version in `package.json` |
| `npm view … versions` | what is already published |
| `npm pack --dry-run` | the tarball contents are the ones we expect |
| `npm publish --provenance` | the release, with a provenance statement |

The CI workflow (`ci.yml`) additionally asserts the tarball carries the
runtime and contains no build artefacts.
