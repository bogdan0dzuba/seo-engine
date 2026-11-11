"""Read-only API transport: errors contain no response bodies or credentials."""
import json
import subprocess
import urllib.request
import urllib.error


class CollectionError(RuntimeError):
    pass


def keychain(item):
    try:
        r = subprocess.run(['security', 'find-generic-password', '-a', item['account'], '-s', item['service'], '-w'],
                           capture_output=True, check=True, timeout=15)
        return r.stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        raise CollectionError('Keychain unavailable') from None


def request(url, token=None, body=None, scheme='OAuth', form=False):
    headers = {'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json'}
    if token:
        headers['Authorization'] = scheme+' '+token
    data = body.encode() if form else json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        raise CollectionError('API HTTP '+str(code)) from None
    except (OSError, ValueError):
        raise CollectionError('Network or response unavailable') from None
