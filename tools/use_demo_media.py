"""On the board, switch the eight sources to a verified demo media pack."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


def request(base, method='GET', body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    req = urllib.request.Request(base.rstrip('/') + '/api/config', data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def apply(base, folder):
    folder = folder.resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    rows = sorted(manifest['videos'], key=lambda row: row['channel'])
    if [row['channel'] for row in rows] != list(range(8)):
        raise ValueError('The media pack must contain channels 0 through 7 exactly once')
    paths = []
    for row in rows:
        path = (folder / 'videos' / row['file']).resolve()
        if path.parent != folder / 'videos':
            raise ValueError('Invalid media filename')
        if sha256(path) != row['sha256']:
            raise ValueError('Media SHA-256 mismatch: ' + row['file'])
        paths.append(path)
    current = request(base)
    updated = json.loads(json.dumps(current))
    channels = {row['id']: row for row in updated['channels']}
    if sorted(channels) != list(range(8)):
        raise ValueError('Terminal configuration must contain eight channel slots')
    for row, path in zip(rows, paths):
        channels[row['channel']].update(name=row['name'], source=str(path), enabled=True, loop=True)
    backups = ROOT / 'data' / 'config-backups'
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = backups / ('before-demo-{}-{}.json'.format(stamp, uuid.uuid4().hex[:8]))
    with backup.open('x', encoding='utf-8') as output:
        json.dump(current, output, indent=2)
        output.write('\n')
    result = request(base, 'PUT', updated)
    if result != updated:
        raise RuntimeError('Applied configuration differs; inspect /api/config and backup ' + str(backup))
    return {'pack': manifest['pack'], 'backup': str(backup), 'channels': [
        {'id': row['id'], 'name': row['name'], 'source': row['source']} for row in result['channels']]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8090')
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--media-dir', type=Path)
    actions.add_argument('--restore', type=Path, help='Restore a previous configuration backup through the API')
    args = parser.parse_args()
    if args.restore:
        result = request(args.url, 'PUT', json.loads(args.restore.read_text(encoding='utf-8')))
        print(json.dumps({'restored': str(args.restore), 'channels': len(result['channels'])}))
    else:
        print(json.dumps(apply(args.url, args.media_dir), indent=2))


if __name__ == '__main__':
    main()
