# Evidence before the 2036 horizon extension

The 2036 extension rebuilds the CE and ACS components and reassembles the
canonical forecast. These files keep the bytes it replaces, with their
original meaning. Checks of the September 9, 2026 AST portability
adaptation read them; the extension does not repeat or relabel that
operation, and no script writes here.

| File | Replaced file | SHA-256 of the replaced bytes |
| --- | --- | --- |
| `acs_rolling_forecast.json.gz` | `data/current/acs_rolling_forecast.json`, the adapted ACS component | `47ed3e6a6924994079fa2f1f5698021b3fab795d78b36a331de5b3ba067d1a78` |
| `ce_rolling_forecast.json.gz` | `data/current/ce_rolling_forecast.json` | `26b863a3e5ea3e23fb4fcd547ccd497e25625d55542b8711a4825239aebb4e21` |
| `scientific_refresh_receipt.json` | `data/current/scientific_refresh_receipt.json`, the adaptation's verification receipt | `b0a27cd88e8161c50b17805ef43399ec948c89a0d5e4a2a8d9e562159ee33db1` |

The components are stored with lossless gzip compression (mtime=0); the
hashes above are of the decompressed bytes. The pre-extension forecast
remains available byte-for-byte at
`web/public/data/canonical/rolling-forecast-cc06784feb81f8c7935d4494cea0a9821a79af868ac50383da8be37c6dc14b99.json`.

`scripts/verify_horizon_extension.py` maps each replaced path to these
bytes (`retained_bytes`) and checks their hashes. It also compares the
current forecast with the retained one: every 2022–2035 value must be
unchanged. Its receipt, the current `data/current/scientific_refresh_receipt.json`,
links the archived receipt here as its predecessor.
