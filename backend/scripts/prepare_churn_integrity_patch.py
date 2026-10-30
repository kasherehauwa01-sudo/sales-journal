#!/usr/bin/env python3
"""Prepare a patch from local COPIES; never write the live integrity manifest."""
import argparse
from copy import deepcopy
import difflib
import hashlib
import json
import os
from pathlib import Path
import re

OLD = 'f04e422ee2881f4cca6fccb2e8818f631b5eeae6f227c8cc01d7ab48dbe56bbc'
NEW = '8f6382d514f0ad4d7e22bacf9b92ce24b5222c16e003065f4cfa10c567f58f7d'
MAX_BYTES = 131072


def read_copy(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError('Unsupported local copy')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            data = source.read(MAX_BYTES+1)
        if len(data) > MAX_BYTES:
            raise ValueError('Local copy too large')
        return data
    finally:
        os.close(fd)


def prepare(manifest_bytes, php_bytes, reviewer):
    if not re.fullmatch(r'[a-zA-Z0-9_.@ -]{1,80}', reviewer):
        raise ValueError('Explicit reviewer identifier required')
    if hashlib.sha256(php_bytes).hexdigest() != NEW:
        raise ValueError('PHP copy differs from the specifically audited GitHub version')
    text = manifest_bytes.decode('utf-8')
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError('Unsupported manifest schema; audit its real format first')
    matches = []
    def walk(value, path=()):
        if not isinstance(value, dict) or not value:
            raise ValueError('Only explicit nested file-to-SHA256 maps are supported')
        for key, leaf in value.items():
            if not isinstance(key,str) or any(ord(c)<32 for c in key):
                raise ValueError('Invalid manifest key')
            if isinstance(leaf,dict):
                walk(leaf,path+(key,))
            elif isinstance(leaf,str) and re.fullmatch('[a-f0-9]{64}',leaf):
                if key.replace('\\','/').endswith('api/manager_tasks_service.php') and leaf==OLD:
                    matches.append(path+(key,))
            else:
                raise ValueError('Unsupported manifest schema; refusing to expose unrelated configuration')
    walk(parsed)
    if len(matches)!=1 or text.count(OLD)!=1:
        raise ValueError('Exactly one audited old manager_tasks_service.php checksum required')
    changed=text.replace(OLD,NEW,1)
    expected=deepcopy(parsed);node=expected
    for key in matches[0][:-1]:node=node[key]
    node[matches[0][-1]]=NEW
    if json.loads(changed)!=expected:
        raise ValueError('Unexpected manifest change')
    patch=''.join(difflib.unified_diff(text.splitlines(keepends=True),changed.splitlines(keepends=True),
                                     fromfile='runtime-code-sha256.json',tofile='runtime-code-sha256.json'))
    if not text.endswith('\n'):
        # Keep the source representation exact; require normal newline text for patch.
        raise ValueError('Manifest copy must end with a newline; review format before preparing patch')
    return '# Reviewed-by: '+reviewer+'\n# Input SHA256: '+hashlib.sha256(manifest_bytes).hexdigest()+'\n'+patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-copy',type=Path,required=True)
    parser.add_argument('--php-copy',type=Path,required=True)
    parser.add_argument('--reviewer',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.output.resolve() in {args.manifest_copy.resolve(),args.php_copy.resolve()}:
            raise ValueError('Output must be a separate new patch file')
        result=prepare(read_copy(args.manifest_copy),read_copy(args.php_copy),args.reviewer)
        descriptor=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as output:output.write(result)
    except (OSError,ValueError,TypeError):
        parser.exit(1,'Patch not prepared: copies, schema, reviewed hash or output preconditions failed\n')
    print('Review patch prepared. No manifest, policy, budget, service or task was changed.')


if __name__=='__main__':main()
