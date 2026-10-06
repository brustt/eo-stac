# Introspection-based band alias resolution

Band Aliases (e.g., `"red"`) resolve to Asset Keys at runtime by checking which candidate keys actually exist in the Item's assets dict, rather than by maintaining a registry of known providers keyed on URL or collection ID.

The provider registry approach was rejected because: (1) the GDF returned by `search_items` already carries each Item's asset keys — the answer is in hand without any external lookup; (2) a URL/collection registry must be explicitly maintained for every new provider, while introspection works automatically for any provider whose keys appear in the Alias Table candidates; (3) mixing two providers in a single GDF (unusual but possible) works correctly per-item with introspection, whereas a single registry entry per source would not.

## Consequences

- The Alias Table must enumerate candidate keys for all known providers. Unknown providers work automatically only if their key appears in the candidate list or the caller supplies it via `extra_aliases`.
- Resolution is per-Item, not per-GDF. Items within the same search result could theoretically resolve the same Band Alias to different Asset Keys (e.g., mixing collections from one API call). The canonical key in `local_paths` is always the Band Alias, so downstream code is unaffected.
