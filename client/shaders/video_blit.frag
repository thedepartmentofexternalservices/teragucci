#version 440
// Teraguchi Phase 2 D-02: BT.709 video-range YUV(10-bit) -> RGB fragment shader.
// Source: ITU-R BT.709 / BT.1361; cross-verified against FFmpeg swscale BT.709.
//
// Anti-pattern guard: HDR matrices (BT dot 2020 / SMPTE 2084 / HLG) are
// explicitly NOT used here. HDR / tone-mapping is OUT OF SCOPE for
// Teraguchi v1 per CONTEXT.md D-06. If you find yourself adding wide-gamut
// HDR constants to this file, stop — that's a Phase 7 / v1.x decision and
// the planner needs to be looped in. The CI tests in
// tests/client/test_viewer_qrhi_video_layer.py and
// tests/smoke/test_ten_bit_pipeline.py grep this file's source for the
// HDR-matrix string and fail if it appears (D-02 anti-pattern rule from
// the RESEARCH document's "Anti-Patterns to Avoid" section).
//
// Input:
//   yTex  : QRhiTexture.R16  holding Y  plane (10-bit left-shifted into 16-bit container).
//   uvTex : QRhiTexture.RG16 holding UV plane (interleaved, half-res, 10-bit-in-16).
//
// P010 normalization note:
//   P010 stores 10-bit values in the TOP 10 bits of a 16-bit container
//   (i.e. shifted left by 6). Qt R16/RG16 samplers normalize the full
//   16-bit range to [0,1], so the natural P010 sample lands at the same
//   nominal [0,1] position as an 8-bit normalized sample would — which
//   is exactly what the BT.709 video-range constants expect. The bottom
//   2 bits of the 10-bit value live in bits 6-7 of the underlying 16-bit
//   value and survive the normalize-then-arithmetic round trip without
//   quantization (the precision is preserved by the fp32 intermediate).
//
// BT.709 video-range constants (verbatim from swscale):
//   Y range : [16/256, 235/256] = [0.0625, 0.9180]   centered at 16/256
//   UV center: 128/256 = 0.5
//   Y video-range scale  = 255/219 ~= 1.1643
//   UV video-range scale = 255/224 ~= 1.1384
//   R = Y + 1.5748*V
//   G = Y - 0.1873*U - 0.4681*V
//   B = Y + 1.8556*U

layout(binding = 1) uniform sampler2D yTex;
layout(binding = 2) uniform sampler2D uvTex;
layout(location = 0) in vec2 v_uv;
layout(location = 0) out vec4 fragColor;

void main() {
    // BT.709 video-range. Y scale = 255/219 ~= 1.1643; UV scale = 255/224 ~= 1.1384.
    float Y  = (texture(yTex,  v_uv).r  - 0.0625) * 1.1643;
    vec2  UV = (texture(uvTex, v_uv).rg - vec2(0.5, 0.5)) * 1.1384;
    float R = Y + 1.5748 * UV.y;
    float G = Y - 0.1873 * UV.x - 0.4681 * UV.y;
    float B = Y + 1.8556 * UV.x;
    fragColor = vec4(R, G, B, 1.0);
}
