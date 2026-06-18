#!/usr/bin/env python3
"""Test Ctrl+V paste via XTest on an Xvfb display."""
import subprocess, time, os

# Start Xvfb
xvfb = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1024x768x24'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
env = {**os.environ, 'DISPLAY': ':99'}

# Set clipboard
p = subprocess.Popen(['xclip', '-selection', 'clipboard', '-i'], stdin=subprocess.PIPE, env=env)
p.communicate(b'PASTE_TEST_OK')
r = subprocess.run(['xclip', '-selection', 'clipboard', '-o'], capture_output=True, text=True, env=env)
print(f'Clipboard: {r.stdout!r}')

from Xlib import X, display as xdisplay
from Xlib.ext import xtest as xt
from Xlib.XK import string_to_keysym

dpy = xdisplay.Display(':99')
has_xtest = dpy.has_extension('XTEST')
print(f'XTest available: {has_xtest}')

# Start xterm that writes stdin to a file
xterm = subprocess.Popen(
    ['xterm', '-e', 'cat > /tmp/tg_paste_result.txt'],
    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2)

kc_ctrl = dpy.keysym_to_keycode(string_to_keysym('Control_L'))
kc_v = dpy.keysym_to_keycode(string_to_keysym('v'))
kc_shift = dpy.keysym_to_keycode(string_to_keysym('Shift_L'))
kc_ret = dpy.keysym_to_keycode(string_to_keysym('Return'))
kc_d = dpy.keysym_to_keycode(string_to_keysym('d'))
print(f'Keycodes: ctrl={kc_ctrl} v={kc_v} shift={kc_shift}')

# xterm paste = Ctrl+Shift+V (NOT Ctrl+V!)
print('Injecting Ctrl+Shift+V...')
xt.fake_input(dpy, X.KeyPress, detail=kc_ctrl)
xt.fake_input(dpy, X.KeyPress, detail=kc_shift)
dpy.sync()
time.sleep(0.03)
xt.fake_input(dpy, X.KeyPress, detail=kc_v)
dpy.sync()
time.sleep(0.03)
xt.fake_input(dpy, X.KeyRelease, detail=kc_v)
xt.fake_input(dpy, X.KeyRelease, detail=kc_shift)
xt.fake_input(dpy, X.KeyRelease, detail=kc_ctrl)
dpy.sync()
time.sleep(0.5)

# Enter + Ctrl+D to finish cat
xt.fake_input(dpy, X.KeyPress, detail=kc_ret)
xt.fake_input(dpy, X.KeyRelease, detail=kc_ret)
dpy.sync()
time.sleep(0.2)
xt.fake_input(dpy, X.KeyPress, detail=kc_ctrl)
dpy.sync()
time.sleep(0.03)
xt.fake_input(dpy, X.KeyPress, detail=kc_d)
dpy.sync()
time.sleep(0.03)
xt.fake_input(dpy, X.KeyRelease, detail=kc_d)
xt.fake_input(dpy, X.KeyRelease, detail=kc_ctrl)
dpy.sync()
time.sleep(1)
dpy.close()

try:
    with open('/tmp/tg_paste_result.txt') as f:
        content = f.read().strip()
    print(f'Pasted text: {content!r}')
    if 'PASTE_TEST_OK' in content:
        print('SUCCESS: XTest paste works')
    else:
        print(f'UNEXPECTED: got {content!r}')
except Exception as e:
    print(f'Could not read result: {e}')

xterm.terminate()
xvfb.terminate()
if os.path.exists('/tmp/tg_paste_result.txt'):
    os.unlink('/tmp/tg_paste_result.txt')
