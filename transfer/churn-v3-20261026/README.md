# Temporary encrypted CHURN V3 transfer

AES-256-GCM. The key is deliberately absent from this repository. Obtain the 64-character hexadecimal key separately over an authenticated private channel. Install Python and the cryptography package on your own computer if needed (`py -m pip install cryptography`). Save the script below as decrypt.py next to the downloaded encrypted files and run `py decrypt.py`. Paste the key at the hidden prompt. Do not enter the key into command arguments, GitHub, or shell history.

```python
from pathlib import Path
from getpass import getpass
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

key = bytes.fromhex(getpass("AES-256 key (hex): ").strip())
assert len(key) == 32
expected = {
    "sales-journal-preliminary-20261026.tar.gz": "95937dbc78775182732900e4787206798f8a5d73decdffc66b1d6b9244c95f4f",
    "calltrack-preliminary-20261026.tar.gz": "de490ee7d98e14bb70b197a27fa91bb8314a25b90c8d0edfc0589d8ced24f80c",
}
for name, checksum in expected.items():
    payload = Path(name + ".aesgcm").read_bytes()
    magic = b"CHURNV3GCM1"
    assert payload.startswith(magic)
    header = payload[:len(magic)+12]
    nonce = header[len(magic):]
    data = AESGCM(key).decrypt(nonce, payload[len(header):], header)
    assert hashlib.sha256(data).hexdigest() == checksum
    with Path(name).open("xb") as output:
        output.write(data)
    print(name, "SHA256 OK", checksum)
```

Format: ASCII CHURNV3GCM1, 12-byte random nonce, ciphertext with 16-byte GCM authentication tag. Full header is authenticated as AAD. Each file has a distinct nonce. Authentication or checksum failure must stop recovery; do not use unauthenticated output. Originals are unchanged. No deployment or automation is performed by this transfer.
