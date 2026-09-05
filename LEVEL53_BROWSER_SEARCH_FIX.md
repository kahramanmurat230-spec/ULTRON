# Browser natural-language search fix

Runtime validation found that a request containing a search phrase plus follow-up reading instructions could be serialized verbatim into the browser URL query. The fix must extract the intended search phrase before URL construction and keep follow-up observation/read steps separate. Existing approval, safety, and verification boundaries remain authoritative.
