"""Run only as a standalone script inside a disposable user/network namespace."""
import os
from pathlib import Path
import socket
import subprocess
import sys


def main():
    assert os.environ.get('OMK_TEST_PARENT_NETNS')
    assert os.readlink('/proc/self/ns/net') != os.environ['OMK_TEST_PARENT_NETNS']
    rules = Path(__file__).resolve().parents[2] / 'systemd/omk-ble-api.nft'

    def run(*args):
        return subprocess.run(args, check=True, capture_output=True, text=True)

    run('ip', 'link', 'set', 'lo', 'up')
    sockets = []
    for port in (8787, 8000):
        server = socket.socket()
        server.bind(('0.0.0.0', port))
        server.listen(32)
        sockets.append(server)
    for _ in range(2):
        run('nft', '-f', str(rules))
    with socket.create_connection(('127.0.0.1', 8787), timeout=1):
        pass
    for interface, subnet, allowed in [('wlan0', '192.0.2', False), ('br-test', '198.51.100', True)]:
        child = subprocess.Popen(['unshare', '--net', 'sleep', '30'])
        try:
            import time
            for _ in range(100):
                if os.readlink(f'/proc/{child.pid}/ns/net') != os.readlink('/proc/self/ns/net'):
                    break
                time.sleep(.01)
            else:
                raise AssertionError('client namespace not ready')
            run('ip', 'link', 'add', interface, 'type', 'veth', 'peer', 'name', 'client')
            run('ip', 'link', 'set', 'client', 'netns', str(child.pid))
            run('ip', 'address', 'add', f'{subnet}.1/24', 'dev', interface)
            run('ip', 'link', 'set', interface, 'up')
            prefix = ['nsenter', '-t', str(child.pid), '-n']
            run(*prefix, 'ip', 'address', 'add', f'{subnet}.2/24', 'dev', 'client')
            run(*prefix, 'ip', 'link', 'set', 'client', 'up')
            for port in (8787, 8000):
                code = 'import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=.5).close()'
                result = subprocess.run([*prefix, sys.executable, '-c', code, f'{subnet}.1', str(port)], capture_output=True)
                assert (result.returncode == 0) == (allowed or port == 8000), (interface, port, result.stderr)
        finally:
            child.terminate()
            child.wait()
    for server in sockets:
        server.close()
    print('PASS: BLE API permits loopback/Docker, blocks external clients, preserves Dashboard port, and reloads safely.')


if __name__ == '__main__':
    main()
