"""Write the API's OpenAPI document to docs/openapi.json.

    python tools/export_openapi.py      # or: make openapi

The document is committed, and tests/test_api_http.py (TestOpenAPI) fails when it
differs from what the code generates, so any change to the API's contract
shows up in review as a diff of this file.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "openapi.json"


def document():
    from fpl.api.app import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main():
    OUT.write_text(document())
    print(f"Wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
