# Build PyAV against system FFmpeg 7.1+ (Teraguchi VIDEO-12)

Phase 2 requires PyAV 17.x linked against system FFmpeg 7.1 or newer for full
VideoToolbox hwaccel on macOS, including P010 (10-bit HEVC Main10) surfaces.
PyPI wheels are built against an older bundled FFmpeg and do **NOT** expose
full VT hwaccel — you will silently lose 10-bit on the client.

This is one of the nine documented silent 10-bit downgrade points
(see `CLAUDE.md` "Known traps"). The decoder-side detection lives in
`client/video_decoder.py::decode_frame_planes` (D-01 cp.5 + cp.6) — when
the negotiated stream is HEVC Main10 and the first decoded `AVFrame.format.name`
is not in `{"p010le", "yuv420p10le"}`, the decoder raises and the structlog
event `decoder.hwaccel_software_fallback` fires. This doc is the dev-setup
half: install the right FFmpeg before that assertion ever has to fire.

## macOS (Homebrew)

```bash
brew install ffmpeg@7
# Tap the system FFmpeg libraries so PyAV finds them at install time:
export PKG_CONFIG_PATH="$(brew --prefix ffmpeg@7)/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
pip uninstall -y av
pip install --no-binary av 'av>=17.0.0,<18.0.0'
```

If you have only `ffmpeg` (current major) installed, that also works — as long
as it's >= 7.1. Confirm with `ffmpeg -version | head -1`.

Verify:

```bash
python -c "import av; print('FFmpeg:', av.library_versions)"
# Expect: libavcodec in the 61.x line (FFmpeg 7.x), VT codec accessible via
# av.codec.Codec('hevc_videotoolbox', 'r').
```

Also exercise the runtime decoder against a real Main10 negotiation:

```bash
python -c "
from client.video_decoder import VideoDecoder
d = VideoDecoder('h265')
d.set_negotiated_main10(True)
print('hw_backend:', d.hw_backend)
# On a correctly-built PyAV / FFmpeg you'll see 'videotoolbox' here.
# If you see 'software' you've hit the silent fallback — re-run pip install --no-binary av.
"
```

## Rocky Linux 9 (RPMFusion)

```bash
sudo dnf install rpmfusion-free-release rpmfusion-nonfree-release
sudo dnf install ffmpeg ffmpeg-devel pkgconfig
pip uninstall -y av
pip install --no-binary av 'av>=17.0.0,<18.0.0'
```

Verify with the same Python one-liners as above. On Rocky 9 with NVENC GPUs,
the decoder will report `hw_backend == "cuda"` (NVDEC) for HEVC Main10
playback paths — that's expected and is the production path for the Mac
client connected to a Rocky server (server encodes via `hevc_nvenc` Main10,
client decodes via `hevc_cuvid`).

## Why this matters

Phase 2 closes 9 silent 10-bit downgrade points (see `CLAUDE.md` "Known traps"
and `.planning/research/PITFALLS.md` Pitfall 2). If the client's PyAV is
linked against the bundled older FFmpeg, the VideoToolbox decode path silently
falls back to software on P010 input — the client's CPU spikes, frames drop,
and the "10-bit" claim in the health overlay becomes a lie.

The `tests/smoke/test_ten_bit_pipeline.py::test_checkpoint_6_decoder_hwaccel_is_videotoolbox`
assertion catches the source-level regression at CI time. The runtime
`decoder.hwaccel_software_fallback` structlog event catches the operational
regression on a live session. This doc is the dev-setup half — get FFmpeg
right at install time so neither gate ever has to fire in production.

## Troubleshooting

**Symptom:** `import av` works but `av.library_versions['libavcodec']` shows
60.x or lower.

**Cause:** PyAV linked against the bundled (or older) FFmpeg via the wheel —
`pip install av` without `--no-binary av` did this.

**Fix:** Run the install commands above with `--no-binary av` and confirm
the libavcodec major bumps to 61 or higher.

---

**Symptom:** `decode_frame_planes` raises `RuntimeError: Main10 negotiated
but decoder returned 'yuv420p'` on a Mac client.

**Cause:** VideoToolbox hwaccel silently fell back to software on the 10-bit
input because the linked FFmpeg is too old.

**Fix:** Re-install PyAV per above against system FFmpeg 7.1+. The decoder's
fail-loud behavior is intentional — it's the reason the silent downgrade trap
no longer ships in production.

---

**Symptom:** `pkg-config: command not found` during `pip install --no-binary av`.

**Cause:** macOS doesn't ship `pkg-config` by default and Homebrew didn't
install it as a transitive dep.

**Fix:** `brew install pkg-config` then re-run the install.

---

**Symptom:** Build fails with "FFmpeg libraries with version >= 4.0 are
required to build PyAV" even though `ffmpeg -version` shows 7.x.

**Cause:** `PKG_CONFIG_PATH` is not exported into the shell that's running
`pip`, so PyAV's setup.py finds the older bundled libs (or none).

**Fix:** Confirm `pkg-config --modversion libavcodec` returns 61.x in the
same shell, then re-run the install.

## References

- `client/video_decoder.py::decode_frame_planes` — decoder-side cp.5 + cp.6
  detection.
- `tests/smoke/test_ten_bit_pipeline.py` — 9-checkpoint pipeline harness.
- `tests/client/test_video_decoder.py` — live PyAV-mocked tests for the
  Main10-negotiation path.
- `.planning/research/PITFALLS.md` Pitfall 2: 10-Bit Pipeline Silently
  Degrades to 8-Bit.
- `CLAUDE.md` "Known traps" — full list of the 9 silent downgrade points.
