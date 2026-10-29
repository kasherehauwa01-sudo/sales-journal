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
