#version 440
// Teraguchi Phase 2 D-02: full-screen quad vertex shader.
// Source-of-truth: tracked here; baked to .qsb via scripts/build_shaders.sh.
//
// Per RESEARCH.md "QRhiWidget Metal BT.709 shader pair (D-02)" — the
// full-screen quad takes clip-space positions in the bottom 2 components
// of `position` and emits a 0..1 UV. The actual UV layout for the quad
// vertices (Y-flip required to match Qt's top-left origin against
// Metal's bottom-left NDC) is encoded by the host code in viewer.py
// when it builds QUAD_VERTICES — this shader stays geometry-agnostic.

layout(location = 0) in vec4 position;
layout(location = 0) out vec2 v_uv;
void main() {
    v_uv = position.xy * 0.5 + 0.5;
    gl_Position = position;
}
