"""Authenticated smoke test without printing the token or client name."""
import argparse
import json
import urllib.error
import urllib.request
from app.config import settings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--url",default="http://localhost:8000/api/integrations/calltrack/client-insights")
    args=parser.parse_args()
    if not settings.calltrack_integration_token:
        raise SystemExit("CALLTRACK_INTEGRATION_TOKEN is not configured")
    request=urllib.request.Request(args.url,data=json.dumps({"name":args.name}).encode(),
        headers={"Content-Type":"application/json","Authorization":"Bearer "+settings.calltrack_integration_token})
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            result=json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"client-insights HTTP {exc.code}") from None
    print(json.dumps({"status":result["status"],"snapshot":result["snapshot"],
                      "rfm_code":(result.get("rfm") or {}).get("rfm_code"),
                      "unique_products":result["unique_products"]},ensure_ascii=False))


if __name__=="__main__": main()
