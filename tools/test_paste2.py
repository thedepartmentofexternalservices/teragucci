#!/usr/bin/env python3
"""Test XTest key injection — types text, then tests Ctrl+V in a GUI app."""
import subprocess, time, os

xvfb = subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1024x768x24'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1)
env = {**os.environ, 'DISPLAY': ':99'}

# Set clipboard
p = subprocess.Popen(['xclip', '-selection', 'clipboard', '-i'], stdin=subprocess.PIPE, env=env)
p.communicate(b'PASTED')

from Xlib import X, display as xdisplay
from Xlib.ext import xtest as xt
from Xlib.XK import string_to_keysym

dpy = xdisplay.Display(':99')

# Start xedit (simple X11 text editor that supports Ctrl+V)
# Try xedit, then xterm as fallback
for app in ['xedit', 'gedit', 'xterm']:
    if subprocess.run(['which', app], capture_output=True).returncode == 0:
        print(f'Using {app}')
        editor = subprocess.Popen([app], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        break
else:
    print('No text editor found')
    xvfb.terminate()
    exit(1)

time.sleep(2)

def keycode(name):
    return dpy.keysym_to_keycode(string_to_keysym(name))

def press_key(kc):
    xt.fake_input(dpy, X.KeyPress, detail=kc)
    dpy.sync()
    time.sleep(0.02)

def release_key(kc):
    xt.fake_input(dpy, X.KeyRelease, detail=kc)
    dpy.sync()
    time.sleep(0.02)

def tap_key(name):
    kc = keycode(name)
    press_key(kc)
    release_key(kc)

# Type "TYPED:" to prove basic key injection works
for ch in 'typed':
    tap_key(ch)
tap_key('colon')

# Now test Ctrl+V
kc_ctrl = keycode('Control_L')
kc_v = keycode('v')
print(f'Injecting Ctrl+V (ctrl={kc_ctrl}, v={kc_v})')
press_key(kc_ctrl)
press_key(kc_v)
release_key(kc_v)
release_key(kc_ctrl)
time.sleep(0.5)

# Check X selection (what's in the active window's text)
# Use xclip to get PRIMARY selection (what's selected)
# Actually we can't easily check the editor content. Let's check xprop.
# Better: use xdotool to select all and copy, then check clipboard

# Type a marker after the paste
for ch in ':end':
    tap_key(ch)

# Select all (Ctrl+A), Copy (Ctrl+C), then read clipboard
kc_a = keycode('a')
kc_c = keycode('c')
time.sleep(0.3)
press_key(kc_ctrl)
press_key(kc_a)
release_key(kc_a)
release_key(kc_ctrl)
time.sleep(0.2)
press_key(kc_ctrl)
press_key(kc_c)
release_key(kc_c)
release_key(kc_ctrl)
time.sleep(0.5)

dpy.close()

# Read what was copied back to clipboard
r = subprocess.run(['xclip', '-selection', 'clipboard', '-o'], capture_output=True, text=True, env=env)
print(f'Editor content (via clipboard): {r.stdout!r}')

if 'PASTED' in r.stdout:
    print('SUCCESS: Ctrl+V paste worked!')
elif 'typed' in r.stdout:
    print('PARTIAL: Typing works but Ctrl+V did NOT paste')
else:
    print(f'UNKNOWN: clipboard says {r.stdout!r}')

editor.terminate()
xvfb.terminate()
