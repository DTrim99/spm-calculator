# Calculator browser QA

`Calculator browser QA` runs Chromium on hosted Linux against the actual Next.js static export, both at `/` and at `/us/spm-calculator/`. It checks search by click and Enter, no-match behavior, one shared header and mobile navigation, annual SPM-area menus, published and modeled geography, 2025 component status, every forecast year through 2036, spending and household controls, and the canonical download and package identity. It also checks failed/stalled request recovery, unavailable-area transitions, Massachusetts/Sumter series breaks and actual rental-support/topcoding diagnostics. Inputs and successful responses come from the real served release; only the one-shot failure/stall is intercepted. No replacement calculation data or parallel threshold formulas are used.

The scientific calculation tests remain in Vitest/Python. Browser QA checks interactions, rendering, and exact served input/artifact identities; it does not certify a scientific release or publish a package.

Playwright is pinned to `@playwright/test` 1.63.0, verified against the [npm registry](https://registry.npmjs.org/@playwright%2ftest/1.63.0). Setup follows the [official CI guide](https://playwright.dev/docs/ci) and [configuration reference](https://playwright.dev/docs/test-configuration). Only Chromium is installed. Each job owns and closes its browser and static server; no browser runs on the local Mac. `bun run test:e2e --list` is available locally for test discovery.

## Verify existing deployments

Manually run the workflow on the reviewed calculator revision, supplying:

- `urls`: a JSON array of full public surface URLs, such as `["https://spm-calculator.vercel.app/","https://policyengine.org/us/spm-calculator/"]`.
- `content_sha256`: the reviewed canonical forecast content hash.
- `package_version`: the expected published package version.
- `package_status`: `published` for final acceptance; `local_preview` only when explicitly checking a development preview.

This mode performs read-only browser navigation and public data downloads. It never builds, deploys, authenticates, or changes the target. Protected previews will fail until a public test surface is available. The served scientific inputs must match the checkout, while publication metadata must match the explicit inputs. A preview pass cannot establish publication or production acceptance.

For another hosted Linux runner, use the same checkout, `bun install --frozen-lockfile`, and `bunx --no-install playwright install --with-deps chromium`, then set `SPM_E2E_BASE_URL`, `SPM_E2E_EXPECT_CONTENT_SHA256`, `SPM_E2E_EXPECT_PACKAGE_VERSION`, and `SPM_E2E_EXPECT_PACKAGE_STATUS` before `bun run test:e2e`.

Each job uploads the HTML report, JSON results, per-test served identity receipts, a canonical-download hash receipt, and failure screenshots/traces. Compare the receipts for standalone and subpath production; both must pass against the same reviewed content hash and published version. These checks complement downstream package/data acceptance; they do not establish PE or Microcosm rollout by themselves.
