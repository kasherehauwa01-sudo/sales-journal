"""Run inside the backend container after deployment; never prints the token."""
import argparse
import json
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('name', help='Exact client name from Calltrack')
    args = parser.parse_args()
    if not settings.calltrack_integration_token:
        raise SystemExit('CALLTRACK_INTEGRATION_TOKEN is not configured')
    request = Request('http://127.0.0.1:8000/api/integrations/calltrack/client-insights',
                      data=json.dumps({'name': args.name, 'as_of': datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()}).encode(),
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + settings.calltrack_integration_token})
    try:
        with urlopen(request, timeout=90) as response:
            data = json.load(response)
    except HTTPError as error:
        raise SystemExit(f'Insights API HTTP {error.code}; check backend version and logs') from None
    for field in ('unique_products', 'top_categories', 'sale_quantities', 'rfm'):
        if field not in data:
            raise SystemExit(f'Missing response field: {field}')
    print('Insights API: OK')
    print('Unique products:', data['unique_products'])
    print('Categories:', len(data['top_categories']))
    print('RFM:', (data['rfm'] or {}).get('segment', 'No eligible purchases'))


if __name__ == '__main__':
    main()
