# Picomatch security backport

The JavaScript files and MIT license come from the `picomatch@4.0.7` npm release. The upstream tarball has integrity `sha512-qcJu88Q2IWqJsDD529JKMdwGm/dvInW4HvQnRwiH9JtihJvzGOscDtHE3x1pBKeUOTysQ8kVmLnJ2kJu7yhcGA==`. The package metadata identifies this repository's private backport as `4.0.7-dbaiops.1`.

The change in `lib/parse.js` handles GHSA-fc5q-39xc-vjpf by keeping generated wildcard or other unbounded repetition out of repeated extglob bodies. The parser treats those groups as literals, using the existing fallback for unsafe repeated extglobs. Normal globs and the existing safe flattening of some nested groups still work. The new check also applies when `maxExtglobRecursion` is disabled.

The root `file:` dependency and override make the frontend build and test toolchain resolve this source. The tests in `src/test/picomatch-security.test.js` verify the resolved path and matching behavior. The npm registry audit continues to run for the full dependency graph. It uses published advisory ranges and cannot assess the code in this local backport.

After upstream publishes a fixed version, replace the local reference with that version, update the lockfile and Dockerfile, and run a clean install, the security regression tests, the build, and the full audit. [Upstream release](https://github.com/micromatch/picomatch/releases/tag/4.0.7) · [Security advisory](https://github.com/advisories/GHSA-fc5q-39xc-vjpf)
