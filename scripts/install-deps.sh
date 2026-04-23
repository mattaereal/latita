#!/usr/bin/env python3
# Install system dependencies for latita
# Supports: apt (Debian/Ubuntu), dnf (Fedora), yum (RHEL/CentOS), pacman (Arch), zypper (openSUSE)

from __future__ import annotations

import os
import shutil
import subprocess
import sys

MISSING_CMDS = []
MISSING_GI = False

PACKAGE_MAP = {
    'apt': {
        'virt-install': 'virtinst',
        'qemu-img': 'qemu-utils',
        'virsh': 'libvirt-clients',
        'xorriso': 'xorriso',
        'gi': 'python3-gi',
    },
    'dnf': {
        'virt-install': 'virt-install',
        'qemu-img': 'qemu-img',
        'virsh': 'libvirt-client',
        'xorriso': 'xorriso',
        'gi': 'python3-gobject',
    },
    'yum': {
        'virt-install': 'virt-install',
        'qemu-img': 'qemu-img',
        'virsh': 'libvirt-client',
        'xorriso': 'xorriso',
        'gi': 'python3-gobject',
    },
    'pacman': {
        'virt-install': 'virt-install',
        'qemu-img': 'qemu',
        'virsh': 'libvirt',
        'xorriso': 'xorriso',
        'gi': 'python-gobject',
    },
    'zypper': {
        'virt-install': 'virt-install',
        'qemu-img': 'qemu-img',
        'virsh': 'libvirt-client',
        'xorriso': 'xorriso',
        'gi': 'python3-gobject',
    },
}

CMDS = ['virt-install', 'qemu-img', 'virsh', 'xorriso']


def check_cmds() -> None:
    global MISSING_CMDS, MISSING_GI
    print('\n=== Checking system dependencies ===\n')
    print('Commands:')
    for cmd in CMDS:
        if shutil.which(cmd):
            print(f'  {cmd}: found')
        else:
            MISSING_CMDS.append(cmd)
            print(f'  {cmd}: MISSING')
    print('\nPython gi module:')
    try:
        subprocess.run([sys.executable, '-c', 'import gi'], check=True, capture_output=True)
        print('  gi: found')
    except subprocess.CalledProcessError:
        MISSING_GI = True
        print('  gi: MISSING')


def detect_pkg_mgr() -> str:
    for mgr in ['apt-get', 'dnf', 'yum', 'pacman', 'zypper']:
        if shutil.which(mgr):
            actual = 'apt' if mgr == 'apt-get' else mgr
            print(f'\nDetected package manager: {actual}')
            return actual
    return 'unknown'


def install_via_pkg_mgr(pkg_mgr: str) -> bool:
    if pkg_mgr not in PACKAGE_MAP:
        return False

    packages = []
    for cmd in MISSING_CMDS:
        if cmd in PACKAGE_MAP[pkg_mgr]:
            packages.append(PACKAGE_MAP[pkg_mgr][cmd])

    if MISSING_GI and 'gi' in PACKAGE_MAP[pkg_mgr]:
        packages.append(PACKAGE_MAP[pkg_mgr]['gi'])

    if not packages:
        return True

    print(f'\nInstalling: {packages}\n')

    sudo_cmd = ['sudo']
    if pkg_mgr == 'apt':
        subprocess.run(sudo_cmd + ['apt-get', 'update', '-qq'], check=True)
        r = subprocess.run(sudo_cmd + ['apt-get', 'install', '-y', '-qq'] + packages,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'apt failed: {r.stderr}')
            return False
        return True
    elif pkg_mgr == 'dnf':
        r = subprocess.run(sudo_cmd + ['dnf', 'install', '-y', '-q'] + packages,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'dnf failed: {r.stderr}')
            return False
        return True
    elif pkg_mgr == 'yum':
        r = subprocess.run(sudo_cmd + ['yum', 'install', '-y', '-q'] + packages,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'yum failed: {r.stderr}')
            return False
        return True
    elif pkg_mgr == 'pacman':
        r = subprocess.run(sudo_cmd + ['pacman', '-Sy', '--noconfirm'] + packages,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'pacman failed: {r.stderr}')
            return False
        return True
    elif pkg_mgr == 'zypper':
        r = subprocess.run(sudo_cmd + ['zypper', 'install', '-y', '-q'] + packages,
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f'zypper failed: {r.stderr}')
            return False
        return True

    return False


def verify() -> bool:
    print('\n=== Verifying installation ===\n')
    all_ok = True
    for cmd in CMDS:
        if shutil.which(cmd):
            print(f'  {cmd}: ok')
        else:
            print(f'  {cmd}: FAIL')
            all_ok = False
    try:
        subprocess.run([sys.executable, '-c', 'import gi'], check=True, capture_output=True)
        print('  gi: ok')
    except subprocess.CalledProcessError:
        print('  gi: FAIL')
        all_ok = False
    return all_ok


def check_libvirtd_socket() -> bool:
    uid = os.getuid()
    socket_path = f'/run/user/{uid}/libvirt/virtqemud-sock'
    if os.path.exists(socket_path):
        return True
    r = subprocess.run(['systemctl', '--user', 'status', 'virtqemud.socket'],
                       capture_output=True, text=True)
    return r.returncode == 0


def setup_libvirtd_user_socket() -> None:
    print('\n=== Libvirt user socket setup ===\n')
    if check_libvirtd_socket():
        print('libvirtd user socket is already running.')
        return

    print('libvirtd user socket is NOT running.')
    print('To enable it manually, run:')
    print('  systemctl --user enable --now virtqemud.socket')
    print('  systemctl --user enable --now virtlogd.socket')
    print()
    try:
        resp = input('Enable now? (requires sudo for lingering) [y/N]: ').strip().lower()
    except EOFError:
        resp = 'n'

    if resp == 'y':
        print('\nEnabling lingering for your user (allows --user services without logon)...')
        user = os.environ.get('USER', '')
        if user:
            r = subprocess.run(['sudo', 'loginctl', 'enable-linger', user],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f'  sudo loginctl enable-linger failed: {r.stderr}')
                print('  Try manually: sudo loginctl enable-linger $USER')
        print('Starting libvirtd user services...')
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'virtqemud.socket'],
                       capture_output=True)
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'virtlogd.socket'],
                       capture_output=True)
        if check_libvirtd_socket():
            print('  libvirtd user socket is now running.')
        else:
            print('  Could not start libvirtd user socket. You may need to log out and back in.')
    else:
        print('Skipped. Run the systemctl commands above manually when ready.')


def main() -> int:
    check_cmds()

    if not MISSING_CMDS and not MISSING_GI:
        print('\nAll system dependencies are installed.')
        setup_libvirtd_user_socket()
        return 0

    pkg_mgr = detect_pkg_mgr()
    if pkg_mgr == 'unknown':
        print('\nCould not detect package manager. Please install the following manually:')
        if MISSING_CMDS:
            print(f'  Commands: {MISSING_CMDS}')
        if MISSING_GI:
            print('  Python gi module (python3-gi or python3-gobject)')
        return 1

    print(f'\nInstalling missing dependencies via {pkg_mgr}...')
    if not install_via_pkg_mgr(pkg_mgr):
        print('\nInstallation failed. Please install the following manually:')
        if MISSING_CMDS:
            pkg_names = [PACKAGE_MAP[pkg_mgr].get(c, c) for c in MISSING_CMDS]
            print(f'  Packages: {pkg_names}')
        if MISSING_GI:
            gi_pkg = PACKAGE_MAP[pkg_mgr].get('gi', 'python3-gi')
            print(f'  Python gi: {gi_pkg}')
        return 1

    if not verify():
        print('\nVerification failed. Some commands are still missing.')
        return 1

    print('\nAll system dependencies installed successfully.')
    setup_libvirtd_user_socket()
    return 0


if __name__ == '__main__':
    sys.exit(main())