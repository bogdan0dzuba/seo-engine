"""GSC service-account JWT; stdlib plus macOS OpenSSL executable, key in RAM only."""
import base64
import json
import os
import subprocess
import time
from urllib.parse import quote, urlencode
from engine.collect.http import keychain, request, CollectionError
from engine.config import write_json
from engine.collect.endpoint import Endpoints


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def authorize(item):
    raw = keychain(item)
    try:
        info = json.loads(raw if raw.startswith('{') else bytes.fromhex(raw))
        now = int(time.time())
        claims = {'iss': info['client_email'], 'scope': 'https://www.googleapis.com/auth/webmasters.readonly',
                  'aud': 'https://oauth2.googleapis.com/token', 'iat': now, 'exp': now+3600}
        message = (b64(b'{"alg":"RS256","typ":"JWT"}')+'.'+b64(json.dumps(claims).encode())).encode()
        rd, wr = os.pipe()
        try:
            os.write(wr, info['private_key'].encode())
            os.close(wr)
            wr = None
            sig = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', '/dev/fd/'+str(rd)], input=message,
                                 pass_fds=(rd,), capture_output=True, check=True, timeout=15).stdout
        finally:
            os.close(rd)
            if wr is not None:
                os.close(wr)
        return request('https://oauth2.googleapis.com/token', body=urlencode({'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': message.decode()+'.'+b64(sig)}), form=True)['access_token']
    except CollectionError:
        raise
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        raise CollectionError('Service account authorization failed') from None


def collect(cfg, folder, start, end):
    endpoints = Endpoints(folder, 'google')
    token = endpoints.attempt('google/authorization', lambda: authorize(cfg['keychain']['google']))
    if token is None:
        return endpoints.errors
    base = 'https://www.googleapis.com/webmasters/v3/sites/'+quote(cfg['gsc_property'], safe='')
    for dimensions in (['date'], ['page'], ['query'], ['page', 'query'], ['date', 'page', 'query']):
        name = 'gsc_'+'_'.join(dimensions)
        def analytics():
            rows, offset = [], 0
            try:
                while True:
                    p = request(base+'/searchAnalytics/query', token, {'startDate': start, 'endDate': end,
                                'dimensions': dimensions, 'rowLimit': 25000, 'startRow': offset, 'dataState': 'final'}, scheme='Bearer')
                    chunk = p.get('rows', [])
                    rows.extend(chunk)
                    if len(chunk) < 25000:
                        return dict(p, rows=rows, _window=[start, end], _dimensions=dimensions, _complete=True)
                    offset += 25000
            finally:
                write_json(folder/(name+'.json'), {'rows': rows, '_window': [start, end], '_dimensions': dimensions, '_complete': False})
        endpoints.save(name, 'searchAnalytics/query/'+','.join(dimensions), analytics)
    endpoints.save('gsc_sitemaps', '/sitemaps', lambda: request(base+'/sitemaps', token, scheme='Bearer'))
    return endpoints.errors
