I couldn't retrieve the policy or any of the documents, so I haven't classified or corrected anything.

**Why it failed:** `get_policy`, `get_document` and `health_check` aren't available to me as direct tools in this session. The only route to the Undrift MCP is the `undrift_mcp_3` sub-agent. My instructions say I must call these tools myself, so that each document's `version` and content are in my own context. They also say I must not delegate retrieval to a sub-agent. I followed that rule and did not delegate.

Without the retrieved text I can't quote evidence, and I won't guess at policy values, versions or document contents. Per my rules, each affected item is `UNKNOWN`.

**Results**
- **Policy `shiftly-returns`:** `UNKNOWN`. It wasn't retrieved, so the version and the voluntary return period are unknown.
- **doc-001:** `UNKNOWN`. It wasn't retrieved, so there is no evidence to quote.
- **doc-002:** `UNKNOWN`. It wasn't retrieved, so there is no evidence to quote.
- **doc-003:** `UNKNOWN`. It wasn't retrieved, so there is no evidence to quote.
- **doc-004:** `UNKNOWN`. It wasn't retrieved, so there is no evidence to quote.

**Options to proceed**
1. Ask a workspace admin to check that the Undrift MCP integration is enabled and **connected** in the Integrations panel, and to run the **Test** button. It also needs to be exposed to me directly, not only through a sub-agent. Then re-run this request.
2. If you accept that the sub-agent will do the retrieval, tell me so explicitly. I would then have it return the raw tool outputs, including `version`. This departs from my standard procedure, so please confirm first.

```json
{
  "policy": {
    "id": "shiftly-returns",
    "version": null,
    "voluntary_return_days": null
  },
  "documents": {
    "doc-001": {
      "category": "UNKNOWN",
      "document_version": null,
      "evidence": null
    },
    "doc-002": {
      "category": "UNKNOWN",
      "document_version": null,
      "evidence": null
    },
    "doc-003": {
      "category": "UNKNOWN",
      "document_version": null,
      "evidence": null
    },
    "doc-004": {
      "category": "UNKNOWN",
      "document_version": null,
      "evidence": null
    }
  },
  "changes_made": false
}
```
