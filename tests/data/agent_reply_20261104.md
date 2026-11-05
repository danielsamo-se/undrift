**Policy:** `shiftly-returns`, version 2. It sets a 30-day voluntary return period for purchases made on or after 2026-11-04. Earlier purchases keep the period promised at the time of purchase. The statutory 14-day EU withdrawal right is unchanged.

**doc-001 (Returns FAQ, website-faq): OUTDATED**
- Evidence: "All customers can return items within 60 days."
- The policy gives 30 days (`voluntary_return_days`) for purchases from 2026-11-04 (`effective_from`). This is a general statement that contradicts that for new purchases.
- Recommended correction: state 30 days for purchases on or after 2026-11-04. Add that earlier purchases keep the period promised at purchase. Drop the "All customers" wording.

**doc-002 (Right of withdrawal, legal-terms): VALID_DISTINCT**
- Evidence: "Customers have a statutory 14-day withdrawal right."
- This is the statutory EU right, which the policy says it does not change. It is separate from the voluntary return period. Leave it as it is.

**doc-003 (Order #1001 note, support-crm): EXISTING_COMMITMENT**
- Evidence: "Order #1001 was placed before the policy change and retains its promised 60-day return period."
- The policy says purchases made before 2026-11-04 "keep the voluntary return period that was promised at the time of purchase." The 60-day promise therefore stays valid.

**doc-004 (Shipping, website-shipping): UNRELATED**
- Evidence: "Free shipping on orders above 50 EUR."
- Shipping fees are not covered by the returns policy.

I changed nothing. All tool calls succeeded.

```json
{
  "policy": {
    "id": "shiftly-returns",
    "version": 2,
    "voluntary_return_days": 30
  },
  "documents": {
    "doc-001": {
      "category": "OUTDATED",
      "evidence": "All customers can return items within 60 days."
    },
    "doc-002": {
      "category": "VALID_DISTINCT",
      "evidence": "Customers have a statutory 14-day withdrawal right."
    },
    "doc-003": {
      "category": "EXISTING_COMMITMENT",
      "evidence": "Order #1001 was placed before the policy change and retains its promised 60-day return period."
    },
    "doc-004": {
      "category": "UNRELATED",
      "evidence": "Free shipping on orders above 50 EUR."
    }
  },
  "changes_made": false
}
```
