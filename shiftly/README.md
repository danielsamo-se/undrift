# Shiftly

A fictional, responsive storefront for the Undrift consistency-management demo.
There is no real checkout, customer data or payment processing.

Requires Node 22 or later; there are no external build dependencies.

```sh
npm test
npm run build
npm run preview
```

Preview: http://127.0.0.1:8200/ and http://127.0.0.1:8200/returns/.

On Vercel select framework **Other**, root `.`, build command `npm run build`,
output `dist`, production branch `main`. Connect the GitHub integration so a
human-merged PR triggers a production deployment and GitHub deployment statuses.
Keep deployment protection off for this public fictional storefront.

`content/returns.txt` is the exact repository-backed policy text. The initial
60-day policy is intentionally outdated. Undrift's approved correction replaces
only this file through a human-reviewed GitHub PR. Preserve exact whitespace;
the initial file has no trailing newline. Do not edit policy text in HTML templates.

The build renders that file in `#returns-policy` on the Returns page, serves its
original bytes at `/content/returns.txt`, and records `VERCEL_GIT_COMMIT_SHA` at
`/build.json`. Undrift verifies all three against the reviewed correction.
Ensure Vercel's system environment variables are available to the build.

GitHub credentials, Cominty credentials and Undrift databases must never be
copied into this repository or Vercel project. Publishing is performed by the
local Undrift review API after separate human authorization. No auto-merge.
