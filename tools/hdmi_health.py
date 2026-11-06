"""Read only this project's X11 window and report whether its pixels advance."""
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import time
from hdmi import ROOT, TITLE, find_desktop


class XImage(c.Structure):
    _fields_ = [('width', c.c_int), ('height', c.c_int), ('xoffset', c.c_int), ('format', c.c_int),
                ('data', c.c_void_p), ('byte_order', c.c_int), ('bitmap_unit', c.c_int),
                ('bitmap_bit_order', c.c_int), ('bitmap_pad', c.c_int), ('depth', c.c_int),
                ('bytes_per_line', c.c_int), ('bits_per_pixel', c.c_int), ('red_mask', c.c_ulong),
                ('green_mask', c.c_ulong), ('blue_mask', c.c_ulong), ('obdata', c.c_void_p),
                ('functions', c.c_void_p * 6)]


def player_ticks(pid):
    fields = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
    return int(fields[11]) + int(fields[12])


def check(interval=2):
    state = json.loads((ROOT / 'data/hdmi-preview.json').read_text())
    pid = state['placement']['player_pid']
    command = Path('/proc/{}/cmdline'.format(pid)).read_bytes().split(b'\0')
    if TITLE.encode() not in command or Path(command[0].decode()).name != 'ffplay':
        raise RuntimeError('Preview process identity mismatch')
    _, environment = find_desktop()
    for key in ('DISPLAY', 'XAUTHORITY'):
        if key in environment:
            os.environ[key] = environment[key]
    x = c.CDLL('libX11.so.6')
    x.XOpenDisplay.argtypes = [c.c_char_p]
    x.XOpenDisplay.restype = c.c_void_p
    x.XCloseDisplay.argtypes = [c.c_void_p]
    x.XDefaultRootWindow.argtypes = [c.c_void_p]
    x.XDefaultRootWindow.restype = c.c_ulong
    x.XGetGeometry.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong), c.POINTER(c.c_int), c.POINTER(c.c_int),
                              c.POINTER(c.c_uint), c.POINTER(c.c_uint), c.POINTER(c.c_uint), c.POINTER(c.c_uint)]
    x.XTranslateCoordinates.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong, c.c_int, c.c_int,
                                       c.POINTER(c.c_int), c.POINTER(c.c_int), c.POINTER(c.c_ulong)]
    x.XGetImage.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int, c.c_uint, c.c_uint, c.c_ulong, c.c_int]
    x.XGetImage.restype = c.POINTER(XImage)
    x.XDestroyImage.argtypes = [c.POINTER(XImage)]
    display = x.XOpenDisplay(None)
    if not display:
        raise RuntimeError('X11 display is unavailable')
    try:
        window = state['placement']['window']
        root = c.c_ulong()
        px, py = c.c_int(), c.c_int()
        width, height, border, depth = c.c_uint(), c.c_uint(), c.c_uint(), c.c_uint()
        if not x.XGetGeometry(display, window, c.byref(root), c.byref(px), c.byref(py),
                              c.byref(width), c.byref(height), c.byref(border), c.byref(depth)):
            raise RuntimeError('Preview window is unavailable')
        x.XTranslateCoordinates(display, window, x.XDefaultRootWindow(display), 0, 0,
                                 c.byref(px), c.byref(py), c.byref(root))
        signatures = []
        before = player_ticks(pid)
        for index in range(2):
            image = x.XGetImage(display, window, 0, 0, width.value, height.value, c.c_ulong(-1).value, 2)
            if not image:
                raise RuntimeError('Cannot read preview window pixels')
            try:
                pixels = c.string_at(image.contents.data, image.contents.bytes_per_line * image.contents.height)
                signatures.append(hashlib.sha256(pixels).hexdigest())
            finally:
                x.XDestroyImage(image)
            if index == 0:
                time.sleep(interval)
        after = player_ticks(pid)
        return {'player_pid': pid, 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'window': window, 'geometry': [px.value, py.value, width.value, height.value],
                'cpu_ticks_advanced': after - before, 'pixel_signatures': signatures,
                'pixels_changed': signatures[0] != signatures[1]}
    finally:
        x.XCloseDisplay(display)


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))
