# Errors

When a source fails, `gleanyard collect` reports an `error_kind` so you can tell a changed
page from a network problem without reading messages.

| Kind | Meaning | Typical cause |
| --- | --- | --- |
| `structure_changed` | The response is not the shape the reader expects. | A feed changed its keys, an endpoint now returns HTML, a calendar is malformed. |
| `blocked` | The host refused access. | `robots.txt` disallows the URL, or HTTP 401, 403, 429 or 451. |
| `transport` | The request failed. | A network error or another HTTP error status. Server errors (500, 502, 503, 504) are retried first. |
| `bounds_exceeded` | A configured limit was reached. | More requests, bytes or occurrences than the source's limits allow, or a request to a host the source does not list. |
| `empty` | The run succeeded but produced no records. | A broken mapping or page, unless the source is `expect_empty: seasonal`. |
| `model` | The language model failed or returned unusable output. | `llm` mode with no provider key, the optional extra not installed, a provider outage, or a reply that is not `{"items": [...]}`. |
| `rights` | The source's rights do not allow collection. | Missing `private_store` or `normalize`, or `send_to_model` for `llm` mode. |
| `no_cache` | `--from-cache` found no stored run. | The source has never been collected, or its rights did not allow storage. |

`pagination_changed` and `filter_ignored` are reserved for the paged and filtered readers
that come with the HTML extraction modes.

Limits never truncate silently. A calendar with more occurrences than `max_occurrences`
fails with `bounds_exceeded` rather than returning part of the data.
