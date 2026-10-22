"""Operator-only commands, explicit transactions; never schedules or enables itself."""
import argparse
import asyncio
import json
from pathlib import Path
from app.database import SessionLocal,engine
from app.services.analytics_automation import release_churn,manage_policy,review_identity

async def execute(args):
    payload={}
    if args.input:
        if Path(args.input).stat().st_size>1048576:raise ValueError('Input too large')
        payload=json.loads(Path(args.input).read_text())
    try:
        async with SessionLocal() as db:
            async with db.begin():
                if args.command in {'preview','release'}:
                    if not args.run_id or not args.input:raise ValueError('Run and independent manifest required')
                    result=await release_churn(db,args.run_id,payload,preview=args.command=='preview')
                elif args.command.startswith('policy-'):
                    result=await manage_policy(db,args.command.removeprefix('policy-'),payload,actor=args.actor,expected_revision=args.revision)
                else:result=await review_identity(db,payload,actor=args.actor,expected_revision=args.revision)
                print(json.dumps(result))
    finally:await engine.dispose()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['preview','release','policy-draft','policy-approve','policy-revoke','identity-review'])
    p.add_argument('--run-id');p.add_argument('--input',help='Private JSON manifest/policy/identity file')
    p.add_argument('--actor');p.add_argument('--revision',type=int)
    asyncio.run(execute(p.parse_args()))
