"""Record KEY_POWER events without grabbing devices or changing power policy."""
import argparse
import json
import os
from pathlib import Path
import selectors
import struct
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=7200)
    parser.add_argument('--output', type=Path, default=Path('data/power-events.jsonl'))
    args = parser.parse_args()
    if args.seconds < 1:
        parser.error('--seconds must be positive')
    devices = []
    bits = struct.calcsize('L') * 8
    for event in sorted(Path('/sys/class/input').glob('event*')):
        device = event / 'device'
        words = (device / 'capabilities/key').read_text().split()
        mask = sum(int(word, 16) << (index * bits) for index, word in enumerate(reversed(words)))
        if mask & (1 << 116):
            devices.append(('/dev/input/' + event.name, (device / 'name').read_text().strip()))
    if not devices:
        raise SystemExit('No KEY_POWER input device found')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    packet = struct.Struct('@llHHi')
    selector = selectors.DefaultSelector()
    descriptors = []
    try:
        for device, name in devices:
            descriptor = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
            descriptors.append(descriptor)
            selector.register(descriptor, selectors.EVENT_READ, (device, name))
        with args.output.open('w') as output:
            def record(row):
                output.write(json.dumps({'at': time.time(), **row}) + '\n')
                output.flush()
                os.fsync(output.fileno())
            record({'kind': 'start', 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    'devices': devices, 'seconds': args.seconds})
            deadline = time.monotonic() + args.seconds
            count = 0
            while time.monotonic() < deadline and count < 10000:
                for key, _ in selector.select(min(1, max(0, deadline - time.monotonic()))):
                    try:
                        data = os.read(key.fd, packet.size * 64)
                    except BlockingIOError:
                        continue
                    if not data:
                        raise RuntimeError('Power input device disconnected: ' + key.data[0])
                    for offset in range(0, len(data), packet.size):
                        sec, usec, event_type, code, value = packet.unpack_from(data, offset)
                        if event_type == 1 and code == 116:
                            record({'kind': 'key_power', 'device': key.data[0], 'name': key.data[1],
                                    'input_time': sec + usec / 1000000, 'value': value})
                            count += 1
            record({'kind': 'end', 'events': count, 'limited': count >= 10000})
    finally:
        selector.close()
        for descriptor in descriptors:
            os.close(descriptor)


if __name__ == '__main__':
    main()
