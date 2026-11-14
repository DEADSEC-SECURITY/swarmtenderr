"""Explicit, durable, temporary metadata requests independent of optimizer mode."""
import hashlib
import time

from .qbit import UpstreamError
from .store import Conflict


STOPPED = {"stoppedDL", "pausedDL", "stoppedUP", "pausedUP"}


def forced(row):
    return bool(row.get('force_start') or row.get('state') in {'forcedDL', 'forcedUP', 'forcedMetaDL'})


def has_metadata(row):
    return max(row.get('size', 0), row.get('total_size', 0), row.get('amount_left', 0)) > 0


def binding(client):
    return hashlib.sha256((client.url.rstrip('/') + '\n' + client.username).encode()).hexdigest()


class MetadataRequests:
    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    def active(self):
        return any(record.get('active') for record in self.store.metadata_requests().values())

    def claim(self):
        if not self.store.controller_lease(self.owner):
            raise Conflict("Another instance is monitoring torrent controls; use that instance or wait for it to release ownership")

    def save(self, record):
        self.store.save_metadata_request(record)

    def finish(self, record, result, message):
        record.update(active=False, phase=result, message=message, finished_at=time.time())
        self.save(record)
        self.store.finish_action(record['id'], 'confirmed' if result == 'acquired' else result)
        if record.get('cancel_id'):
            self.store.finish_action(record['cancel_id'], 'confirmed')

    def start(self, client, identity, audit, duration):
        self.claim()
        if self.store.metadata_requests().get(identity, {}).get('active'):
            raise Conflict("A metadata attempt is already active for this torrent")
        row = client.torrent(identity)
        if has_metadata(row) or row.get('progress', 0) >= 1:
            raise ValueError("This torrent already has metadata")
        if forced(row):
            raise ValueError("This torrent is already force-started; return it to the queue before requesting a temporary metadata attempt")
        if row.get('state') in {'error', 'missingFiles', 'checkingDL', 'checkingUP', 'moving', 'unknown'}:
            raise ValueError("Resolve the torrent's current client state before requesting metadata")
        now = time.time()
        record = dict(id=audit, torrent=identity, client=binding(client), active=True,
                      phase='starting', started_at=now, deadline=now + duration,
                      was_stopped=row.get('state') in STOPPED, stage='start',
                      force_confirmed=False,
                      message='Requesting an immediate metadata attempt')
        self.store.save_metadata_request(record, create=True)
        armed = False
        try:
            client.action(identity, 'force_start', manual=True,
                          eligible=lambda current: not has_metadata(current) and not forced(current))
            armed = True
            # Some qBittorrent 5 releases set the force flag on a stopped
            # magnet without starting it. Resume only that observed state.
            if client.torrent(identity).get('state') in STOPPED:
                self.claim()
                client.action(identity, 'resume', manual=True, eligible=forced)
                if not forced(client.torrent(identity)):
                    self.claim()
                    client.action(identity, 'force_start', manual=True,
                                  eligible=lambda current: not forced(current))
        except ValueError:
            if armed:
                record.update(phase='attention', message='Client state changed during startup. Inspect the torrent or cancel this metadata attempt.')
                self.save(record)
            else:
                self.finish(record, 'rejected', 'The torrent changed before the metadata attempt could start')
            raise
        except UpstreamError:
            record.update(phase='attention', message='Start result unconfirmed. Waiting for a client observation; the start request will not be replayed.')
            self.save(record)
            raise
        record.update(phase='fetching', message='Bypassing the queue to request metadata from peers')
        self.save(record)
        try:
            self.claim()
            client.action(identity, 'reannounce', manual=True)
        except (ValueError, UpstreamError):
            record['message'] = 'Metadata attempt started; tracker reannounce could not be confirmed'
            self.save(record)

    def release(self, client, record, result):
        """Write intent before HTTP. A later observation confirms each cleanup step."""
        self.claim()
        record.update(phase='restoring', stage='unforce', result=result,
                      mutation_at=time.time(),
                      message='Returning the torrent to its previous queue control')
        self.save(record)
        try:
            client.action(record['torrent'], 'unforce', manual=True, eligible=forced)
        except ValueError:
            # Nothing to undo when the user has already removed the force flag.
            self.finish(record, 'interrupted', 'The client state changed; manual control was preserved')
        except UpstreamError:
            record.update(phase='attention', message='Return to queue is unconfirmed. Inspect the torrent or explicitly retry Cancel metadata attempt.')
            self.save(record)
            raise

    def cancel(self, client, identity, audit):
        self.claim()
        record = self.store.metadata_requests().get(identity)
        if not record or not record.get('active'):
            raise ValueError("There is no active metadata attempt for this torrent")
        if record['client'] != binding(client):
            raise ValueError("Reconnect the original qBittorrent client before cancelling its metadata attempt")
        if record['phase'] == 'restoring':
            raise ValueError("This metadata attempt is already returning to the queue")
        record['cancel_id'] = audit
        row = client.torrent(identity)
        if not forced(row):
            if record['was_stopped'] and record.get('stage') in {'unforce', 'pause'} and row.get('state') not in STOPPED:
                record.update(phase='restoring', stage='pause', result='cancelled', mutation_at=time.time(),
                              message='Restoring the torrent’s previous stopped state')
                self.save(record)
                try:
                    client.action(identity, 'pause', manual=True, eligible=lambda current: not forced(current))
                except UpstreamError:
                    record.update(phase='attention', message='Restoring the stopped state is unconfirmed. Inspect the torrent or retry Cancel metadata attempt.')
                    self.save(record)
                    raise
                return
            self.finish(record, 'cancelled', 'Metadata attempt cancelled; current manual client state was preserved')
            return
        self.release(client, record, 'cancelled')

    def manual_override(self, identity, action, client=None):
        if action not in {'pause', 'resume', 'force_start', 'unforce'}:
            return
        record = self.store.metadata_requests().get(identity)
        if record and record.get('active'):
            self.claim()
            if action in {'pause', 'resume'}:
                if client is None or record['client'] != binding(client):
                    raise ValueError('Reconnect the original client before taking over its metadata attempt')
                record.update(phase='restoring', stage='unforce', result='interrupted',
                              was_stopped=action == 'pause', force_confirmed=False, mutation_at=time.time(),
                              message='Releasing temporary force start for your manual ' + action + ' request')
                self.save(record)
                if forced(client.torrent(identity)):
                    try:
                        client.action(identity, 'unforce', manual=True, eligible=forced)
                    except UpstreamError:
                        record.update(phase='attention', message='Queue restoration is unconfirmed. Inspect the torrent before retrying your manual action.')
                        self.save(record)
                        raise
                if action == 'pause':
                    # The caller issues this explicit stop once. Polling may
                    # confirm it, but must not replay a lost stop response.
                    record['stage'] = 'pause'
                    self.save(record)
            else:
                self.finish(record, 'interrupted', 'Metadata attempt superseded by an explicit manual ' + action.replace('_', ' ') + ' request')

    def poll(self, client, rows):
        records = [record for record in self.store.metadata_requests().values() if record.get('active')]
        if not records or not self.store.controller_lease(self.owner):
            return False
        indexed, changed = {row['hash']: row for row in rows}, False
        for record in records:
            if record['client'] != binding(client):
                if not record.get('connection_changed'):
                    record.update(phase='attention', connection_changed=True, message='Client connection changed. Reconnect the original client to restore this attempt.')
                    self.save(record)
                    self.store.finish_action(record['id'], 'uncertain')
                continue
            row = indexed.get(record['torrent'])
            if not row:
                self.finish(record, 'removed', 'The torrent was removed from the client')
                continue
            record.pop('connection_changed', None)
            if record.get('force_confirmed') and row.get('state') in STOPPED and not record['was_stopped']:
                record['was_stopped'] = True
                self.save(record)
            if record['phase'] in {'restoring', 'attention'} and record.get('stage') in {'unforce', 'pause'}:
                if forced(row):
                    if record['phase'] == 'restoring' and time.time() - record.get('mutation_at', 0) > 30:
                        record.update(phase='attention', message='Return to queue has not been confirmed. Inspect the torrent or retry Cancel metadata attempt.')
                        self.save(record)
                    continue  # A lost response never causes an automatic replay.
                if record['was_stopped'] and row.get('state') not in STOPPED:
                    if record['stage'] == 'pause':
                        if record['phase'] == 'restoring' and time.time() - record.get('mutation_at', 0) > 30:
                            record.update(phase='attention', message='The previous stopped state has not been confirmed. Inspect the torrent or retry Cancel metadata attempt.')
                            self.save(record)
                        continue
                    record.update(phase='restoring', stage='pause', message='Restoring the torrent’s previous stopped state')
                    record['mutation_at'] = time.time()
                    self.save(record)
                    try:
                        self.claim()
                        client.action(record['torrent'], 'pause', manual=True, eligible=lambda current: not forced(current))
                        changed = True
                    except ValueError:
                        self.finish(record, 'interrupted', 'Client state changed; manual control was preserved')
                    except UpstreamError:
                        record.update(phase='attention', message='Restoring the stopped state is unconfirmed. Inspect the torrent or retry Cancel metadata attempt.')
                        self.save(record)
                    continue
                result = record.get('result', 'cancelled')
                messages = {'acquired': 'Metadata acquired; temporary force start released',
                            'timed_out': 'Metadata did not arrive before the deadline; temporary force start released',
                            'cancelled': 'Metadata attempt cancelled; temporary force start released',
                            'interrupted': 'Manual client control preserved; temporary force start released'}
                self.finish(record, result, messages[result])
                continue
            if not forced(row):
                if not record.get('force_confirmed') and time.time() - record['started_at'] < 30 and time.time() < record['deadline']:
                    continue
                self.finish(record, 'interrupted', 'No temporary force start is active; current client state was preserved')
                continue
            if row.get('state') in STOPPED and not has_metadata(row) and time.time() < record['deadline']:
                if not record.get('force_confirmed') and time.time() - record['started_at'] < 10:
                    continue
                if record.get('force_confirmed'):
                    record['was_stopped'] = True
                    try:
                        self.release(client, record, 'interrupted')
                        changed = True
                    except UpstreamError:
                        pass
                elif record['phase'] != 'attention':
                    record.update(phase='attention', message='The client is still stopped. Inspect it or cancel the metadata attempt; startup will not be replayed.')
                    self.save(record)
                continue
            if not record.get('force_confirmed'):
                record['force_confirmed'] = True
                self.save(record)
            if has_metadata(row) or time.time() >= record['deadline']:
                try:
                    self.release(client, record, 'acquired' if has_metadata(row) else 'timed_out')
                    changed = True
                except UpstreamError:
                    pass
            elif record['phase'] != 'fetching':
                record.update(phase='fetching', message='Temporary force start confirmed; requesting metadata from peers')
                self.save(record)
                self.store.finish_action(record['id'], 'accepted')
        return changed

    def public(self, client):
        return {identity: {key: record.get(key) for key in
                ('active', 'phase', 'started_at', 'deadline', 'finished_at', 'message')}
                for identity, record in self.store.metadata_requests().items()
                if record['client'] == binding(client)}
