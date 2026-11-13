"""Authenticated application bridge to a deployment-owned local guard socket."""
import httpx

from .qbit import UpstreamError
from .store import Conflict


class OptimizerSettings:
    def __init__(self, path):
        self.path = path

    def request(self, method='GET', payload=None, actor=None):
        if not self.path:
            raise UpstreamError('Active guard settings are not connected')
        if method not in {'GET', 'PATCH'}:
            raise ValueError('Unsupported guard operation')
        if method == 'PATCH':
            if not isinstance(payload, dict) or set(payload) - {'revision', 'changes', 'reset'}:
                raise ValueError('Invalid guard settings update')
            payload = {'revision': payload.get('revision'), 'changes': payload.get('changes', {}),
                       'reset': payload.get('reset', []), 'actor': actor}
            if (type(payload['revision']) is not int or not isinstance(payload['changes'], dict)
                    or not isinstance(payload['reset'], list)):
                raise ValueError('A revision, changes object and reset list are required')
        try:
            with httpx.Client(transport=httpx.HTTPTransport(uds=self.path), timeout=5, trust_env=False) as client:
                response = client.request(method, 'http://optimizer/v1/settings', json=payload)
                if len(response.content) > 65536:
                    raise ValueError('Oversized guard response')
                body = response.json()
                if response.status_code == 409:
                    raise Conflict('Guard settings changed; reload before saving')
                if response.status_code == 400:
                    raise ValueError(body.get('error', 'Invalid guard settings'))
                if response.status_code != 200:
                    raise UpstreamError('Active guard settings are unavailable')
                if (not isinstance(body, dict) or type(body.get('revision')) is not int
                        or not isinstance(body.get('values'), dict) or not isinstance(body.get('fields'), list)):
                    raise UpstreamError('Active guard returned an invalid configuration')
                return body
        except (httpx.HTTPError, OSError):
            # No write retry: the owner may already have committed the request.
            raise UpstreamError('Could not confirm guard settings. Reload before retrying.') from None
