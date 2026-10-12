# Errors

When a source fails, `gleanyard collect` reports an `error_kind` so you can tell a changed
page from a network problem without reading messages.

| Kind | Meaning | Typical cause |
| --- | --- | --- |
| `structure_changed` | The response is not the shape the reader expects. | A feed changed its keys, an endpoint now returns HTML, a calendar is malformed. |
| `blocked` | The host refused access. | `robots.txt` disallows the URL, or HTTP 401, 403 or 451, or HTTP 429 that persists or asks for a wait over two minutes (a shorter Retry-After is waited out and retried). |
| `transport` | The request failed. | A network error or another HTTP error status. Server errors (500, 502, 503, 504) are retried first. |
| `pagination_changed` | Paging no longer behaves as configured, so records may be missing. | The paging parameter is ignored, pages repeat, or the source grew past `pagination.max_pages`. |
| `filter_ignored` | The source ignored a filter and returned out-of-bounds data. | A `filter` in the source file finds too many records outside the allowed values; the URL filter stopped working. |
| `bounds_exceeded` | A configured limit was reached. | More requests, bytes or occurrences than the source's limits allow, or a request to a host the source does not list. |
| `empty` | The run succeeded but produced no records. | A broken mapping or page, unless the source is `expect_empty: seasonal`. |
| `model` | The language model failed or returned unusable output. | `llm` mode with no provider key, the optional extra not installed, a provider outage, or a reply that is not `{"items": [...]}`. |
| `rights` | The source's rights do not allow collection. | Missing `private_store` or `normalize`, or `send_to_model` for `llm` mode. |
| `no_cache` | `--from-cache` found no stored run. | The source has never been collected, or its rights did not allow storage. |

Limits never truncate silently. A calendar with more occurrences than `max_occurrences`
fails with `bounds_exceeded` rather than returning part of the data.
