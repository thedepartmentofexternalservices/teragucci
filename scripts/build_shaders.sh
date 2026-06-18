#!/usr/bin/env bash
# Teraguchi Phase 2 D-02: bake GLSL -> .qsb via pyside6-qsb.
#
# Run after editing any of the GLSL sources in client/shaders/.
# CI verifies committed .qsb matches the source via a no-diff check
# (.github/workflows/ci.yml test-macos job).
#
# Requires PySide6 (which ships pyside6-qsb). On developer machines the
# project's venv at .venv/bin/pyside6-qsb is preferred; the script falls
# back to whatever pyside6-qsb is on PATH.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHADER_DIR="${REPO_ROOT}/client/shaders"

# Resolve pyside6-qsb. Prefer the project venv, then any ancestor .venv
# (handles parallel worktrees where .venv lives at the parent project
# root), then PATH (CI: pip-installed PySide6 puts the tool on PATH).
QSB=""
if [[ -x "${REPO_ROOT}/.venv/bin/pyside6-qsb" ]]; then
    QSB="${REPO_ROOT}/.venv/bin/pyside6-qsb"
else
    # Walk up to find a .venv (worktree-aware).
    SEARCH_DIR="${REPO_ROOT}"
    for _ in 1 2 3 4 5; do
        SEARCH_DIR="$(dirname "${SEARCH_DIR}")"
        if [[ -x "${SEARCH_DIR}/.venv/bin/pyside6-qsb" ]]; then
            QSB="${SEARCH_DIR}/.venv/bin/pyside6-qsb"
            break
        fi
        if [[ "${SEARCH_DIR}" == "/" ]]; then
            break
        fi
    done
fi
if [[ -z "${QSB}" ]] && command -v pyside6-qsb >/dev/null 2>&1; then
    QSB="$(command -v pyside6-qsb)"
fi
if [[ -z "${QSB}" ]]; then
    echo "ERROR: pyside6-qsb not found. Install PySide6 (pip install PySide6)." >&2
    exit 1
fi

cd "${SHADER_DIR}"

# --qt6 emits the GLSL/HLSL/MSL variants commonly bundled with QRhi
# pipelines so the shader can be loaded on Metal (macOS), GL (legacy
# fallback), and Vulkan/D3D when those backends ever come into play.
"${QSB}" --qt6 -o video_blit.vert.qsb video_blit.vert
"${QSB}" --qt6 -o video_blit.frag.qsb video_blit.frag

echo "Shaders baked to ${SHADER_DIR}"
