"""Isolate endpoint errors without persisting exception text or credentials."""
from engine.config import write_json


class Endpoints:
    def __init__(self, folder, source):
        self.folder, self.source, self.errors = folder, source, []

    def attempt(self, endpoint, action):
        try:
            return action()
        except Exception as error:
            self.errors.append({'endpoint': endpoint, 'error_class': type(error).__name__})
            return None
        finally:
            write_json(self.folder/(self.source+'_manifest.json'), {'errors': self.errors})

    def save(self, name, endpoint, action):
        def fetch():
            payload = action()
            write_json(self.folder/(name+'.json'), payload)
            return payload
        return self.attempt(endpoint, fetch)
