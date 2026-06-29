# Update Source Plugins

`check-updates` discovers package-specific upstream sources through plugins in
`overlay_tools.core.update_sources`.

## Contract

Each plugin implements:

- `name: str`
- `match(context: PackageSourceContext) -> SourceMatch | None`
- `latest_release(match: SourceMatch) -> SourceRelease | None`

`latest_release()` should return `None` for non-fatal HTTP/JSON/parsing failures.
By default, a matched plugin that returns `None` reports `manual-check` with the
plugin's `source_url`; this prevents stale metadata GitHub IDs from masking a
custom-source failure. Manual-only plugins that should preserve GitHub release
fallback can set `SourceMatch(fallback_to_github=True)`.

## Adding a source

1. Create `src/overlay_tools/core/update_sources/<vendor>.py`.
2. Add tests in `tests/test_update_sources.py` for:
   - host match
   - package-name fallback match if needed
   - false-positive avoidance
   - parser success
   - parser malformed/empty/null payload behavior
3. Register the plugin in `registry.DEFAULT_UPDATE_SOURCES`.
4. Do not add vendor-specific branches to `cli/check_updates.py`.

## HTTP policy

Use `httpx`, not `requests`.

Custom source HTTP failures should be non-fatal. Use `follow_redirects=True` for
latest-release endpoints that may return 30x responses; this mirrors
`overlay_tools.core.github.GitHubClient` behavior.

```python
try:
    response = httpx.get(url, timeout=10, follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
except (httpx.HTTPError, ValueError):
    return None
```
