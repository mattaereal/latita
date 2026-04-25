#!/usr/bin/env python3
"""Real end-to-end TUI smoke test via pexpect.

This launches latita menu in a pseudo-tty and drives it with keystrokes
to catch rendering, focus, and lifecycle bugs that unit tests miss.
"""

import pexpect
import sys

TIMEOUT = 5


def run_smoke_test():
    child = pexpect.spawn("uv run latita menu", timeout=TIMEOUT, encoding="utf-8")

    # 1. Wait for main dashboard
    try:
        child.expect("VMs")
        print("✓ Dashboard rendered")
    except pexpect.TIMEOUT:
        print("✗ Dashboard did not render")
        print(child.before)
        return 1

    # 2. Check status bar is short (should not contain old long status)
    before = child.before + child.after
    if "↑↓ navigate | tab switch | q quit | c create" in before:
        print("✗ Status bar still has old long format")
        return 1
    print("✓ Status bar is concise")

    # 3. Tab to actions pane
    child.send("\t")
    child.expect(["Actions", pexpect.TIMEOUT])
    if "Actions" not in (child.before + child.after):
        print("✗ Actions pane not visible after tab")
        return 1
    print("✓ Tab switches to actions pane")

    # 4. Open templates screen
    child.send("t")
    try:
        child.expect("Templates")
        print("✓ Templates screen opened")
    except pexpect.TIMEOUT:
        print("✗ Templates screen did not open")
        print(child.before)
        return 1

    # 5. Close templates screen (q is bound to pop_screen)
    child.send("q")
    try:
        child.expect("VMs")
        print("✓ Templates screen closed, back to dashboard")
    except pexpect.TIMEOUT:
        print("✗ Templates screen did not close")
        print(child.before)
        return 1

    # 6. Open capsules screen
    child.send("p")
    try:
        child.expect("Capsules")
        print("✓ Capsules screen opened")
    except pexpect.TIMEOUT:
        print("✗ Capsules screen did not open")
        return 1

    # 7. Close capsules screen (q is bound to pop_screen)
    child.send("q")
    try:
        child.expect("VMs")
        print("✓ Capsules screen closed, back to dashboard")
    except pexpect.TIMEOUT:
        print("✗ Capsules screen did not close")
        print(child.before)
        return 1

    # 8. Quit
    child.send("q")
    try:
        child.expect(pexpect.EOF)
        print("✓ Exited cleanly")
    except pexpect.TIMEOUT:
        print("✗ Did not exit cleanly")
        print(child.before)
        return 1

    return 0


if __name__ == "__main__":
    rc = run_smoke_test()
    sys.exit(rc)
