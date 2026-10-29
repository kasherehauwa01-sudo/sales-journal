# Report frontend/backend compatibility

The owner's supplied runtime log shows:

- `GET /api/reports/brands` -> 404.
- `GET /api/reports/product-analytics/catalog-options/brand` -> 200.
- `POST /api/reports/product-sales/summary` -> 422.
- No product-sales HTTP 500 or exception traceback appears in this extract.

The UI was requesting a brand route absent from the running backend. Shared
brand filters now use the existing product-analytics catalogue options endpoint,
including server search, the same source setting, checkboxes, and retained
selections. `/reports/brands` remains an alias for callers with an older frontend;
it no longer restricts options to locally matched sales.

The summary rejection happens during request validation, before SQL execution.
A backend expecting `manager` / `buyer_type` as strings rejects the new arrays
with exactly HTTP 422; a regression reproduces this. The supplied access log does
not contain the 422 response detail, so the rejected fields must be confirmed by
the response or runtime schema. This is distinct from PostgreSQL's bind limit.
The current source accepts both strings and arrays and passes the retail endpoint
regression with 40,001 synthetic clients. It has not yet been verified in the
owner's running backend image.

The frontend explains a direct `string_type` validation rejection of those array
filters as a backend/frontend version mismatch. It does not flatten multiple
selections to one value, retry automatically, or expose validation input data.
Other validation failures retain HTTP 422 with a readable message; genuine HTTP
500 errors are not relabelled as successful results.

## Read-only runtime check

Run these on the VPS host, not inside psql. They neither recreate containers nor
run migrations, SQL, workers, report calculations or interservice requests.
Only image identifiers and two request schema properties are printed.

```bash
docker inspect --type container \
  --format 'image_ref={{.Config.Image}} image_id={{.Image}}' sales-journal-backend

docker exec -i sales-journal-backend python -B - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:8000/openapi.json', timeout=10) as response:
    spec = json.load(response)
path = '/api/reports/product-sales/summary'
schema = spec['paths'][path]['post']['requestBody']['content']['application/json']['schema']
if '$ref' in schema:
    current = spec
    for part in schema['$ref'].removeprefix('#/').split('/'):
        current = current[part]
    schema = current
for name in ('manager', 'buyer_type'):
    print(name, json.dumps(schema.get('properties', {}).get(name), ensure_ascii=False))
print('new_brand_route_present:', '/api/reports/brands' in spec['paths'])
PY
```

Both filter properties in a compatible backend include an array of strings
(alongside the legacy string / null alternatives). If only string/null is
advertised, the deployed image is incompatible with multi-select requests.

## Update prerequisite

Frontend assets and the backend image must be built from the same reviewed
revision containing the report changes. Rebuilding only the frontend leaves the
running backend unchanged. Inspect the actual backend image before planning the
update, and preserve its existing startup, automation-off flags and rollback
procedure. This report change adds no migration. Do not switch blindly to an old
image tag or use the container startup as an unreviewed migration mechanism.

No production update was performed during this fix.

## Empty and single product-sales selections

Product-sales requests now preserve the legacy wire shape when it is sufficient:
no selected values become JSON null, one value becomes a string (e.g. buyer_type
"Розница"), and two or more values remain an array. The UI state remains an array
and no selected values are discarded. The same serializer is used by report
requests, details and Excel from the successful snapshot. There is no automatic
retry or fallback that replaces multiple selections with just the first value.

This removes the string-only backend's validation rejection for empty/single
manager and buyer filters. Full multiple selections still require the compatible
backend schema described above. It does not modify a running server's SQL or
Docker image, and a production report calculation has not been run here.

Validation for this follow-up: 95 frontend tests and 25 targeted backend tests
passed, including null/string/array request shapes, the old nullable string
contract, and retail queries against the current backend endpoints using both
single and array selections. Frontend build and diff check passed.
