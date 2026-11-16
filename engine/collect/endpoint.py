"""Isolate endpoint errors without persisting exception text or credentials."""
import re
from engine.config import write_json

AUTH_STATUS = {401, 403}


class Endpoints:
    def __init__(self, folder, source):
        self.folder, self.source, self.errors = folder, source, []
        self.ok, self.attempted, self.auth_failed, self.http = 0, 0, False, {}

    def manifest(self):
        # stale=True: at least one endpoint failed, so the snapshot is incomplete.
        return {'errors': self.errors, 'stale': bool(self.errors), 'attempted': self.attempted, 'ok': self.ok,
                'auth_failed': self.auth_failed, 'http_status': self.http}

    def attempt(self, endpoint, action):
        self.attempted += 1
        try:
            result = action()
            self.ok += 1
            return result
        except Exception as error:
            self.errors.append({'endpoint': endpoint, 'error_class': type(error).__name__})
            match = re.fullmatch(r'API HTTP (\d{3})', str(error))  # CollectionError text carries only the status code.
            if match:
                self.http[endpoint] = int(match.group(1))
            if (match and int(match.group(1)) in AUTH_STATUS) or str(error) == 'Keychain unavailable':
                self.auth_failed = True
            return None
        finally:
            write_json(self.folder/(self.source+'_manifest.json'), self.manifest())

    def save(self, name, endpoint, action):
        def fetch():
            payload = action()
            write_json(self.folder/(name+'.json'), payload)
            return payload
        return self.attempt(endpoint, fetch)
