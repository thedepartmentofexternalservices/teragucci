/*
 * nvfbc_capture — teraguchi NvFBC capture helper.
 *
 * Spawned by screen_capture.py as a long-lived subprocess when
 * libnvidia-fbc.so.1 is available. Uses NVIDIA's NvFBC library to
 * read the X screen framebuffer directly from the compositor's output
 * (bypassing XShmGetImage, which races with Mutter's DRI path and
 * produces torn frames during video playback), then streams raw BGRA
 * frames out on stdout for the Python server to forward to the
 * encoder.
 *
 * Wire format (stdout, little-endian, one header+payload per frame):
 *
 *     offset  size  field
 *     ------  ----  -----
 *        0     4    magic      'T','G','F','R'
 *        4     4    width      uint32 (pixels)
 *        8     4    height     uint32 (pixels)
 *       12     4    byte_size  uint32 (bytes of payload that follow)
 *      16    N    bgra       raw BGRA8888, row-major, top-down
 *
 * All diagnostic output goes to stderr. Teraguchi captures the helper's
 * stderr and forwards it to its own logger.
 *
 * Shutdown: SIGTERM and SIGINT destroy the capture session and exit
 * cleanly. A broken stdout pipe (SIGPIPE) is treated as "parent is
 * gone" and also triggers shutdown.
 *
 * Build: see Makefile. Link only against libdl and libc — libnvidia-fbc
 * is dlopen'd at runtime so the binary still launches (and exits with
 * a clean error) on machines without an NVIDIA driver.
 */

#define _POSIX_C_SOURCE 200809L

#include <dlfcn.h>
#include <errno.h>
#include <getopt.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "NvFBC.h"

#define NVFBC_LIB "libnvidia-fbc.so.1"
#define FRAME_MAGIC "TGFR"

static volatile sig_atomic_t g_stop = 0;

static NVFBC_API_FUNCTION_LIST g_fn;
static NVFBC_SESSION_HANDLE    g_session        = 0;
static int                     g_handle_alive   = 0;
static int                     g_session_alive  = 0;
static void                   *g_lib            = NULL;

static void on_signal(int sig) {
    (void)sig;
    g_stop = 1;
}

static void die_v(const char *fmt, va_list ap) {
    fprintf(stderr, "[nvfbc] ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    fflush(stderr);
}

static void log_line(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    die_v(fmt, ap);
    va_end(ap);
}

static void log_nvfbc_err(const char *where) {
    const char *msg = "(no session handle)";
    if (g_handle_alive && g_fn.nvFBCGetLastErrorStr) {
        const char *s = g_fn.nvFBCGetLastErrorStr(g_session);
        if (s && *s) msg = s;
    }
    log_line("%s: %s", where, msg);
}

/* write() exactly n bytes or return -1. Treats SIGPIPE / EPIPE as
 * "parent closed stdout" and sets g_stop so the main loop exits. */
static int write_all(int fd, const void *buf, size_t n) {
    const uint8_t *p = (const uint8_t *)buf;
    while (n > 0) {
        ssize_t w = write(fd, p, n);
        if (w < 0) {
            if (errno == EINTR) continue;
            if (errno == EPIPE) { g_stop = 1; return -1; }
            log_line("write failed: %s", strerror(errno));
            return -1;
        }
        p += w;
        n -= (size_t)w;
    }
    return 0;
}

static void cleanup(void) {
    if (g_session_alive && g_fn.nvFBCDestroyCaptureSession) {
        NVFBC_DESTROY_CAPTURE_SESSION_PARAMS p;
        memset(&p, 0, sizeof(p));
        p.dwVersion = NVFBC_DESTROY_CAPTURE_SESSION_PARAMS_VER;
        g_fn.nvFBCDestroyCaptureSession(g_session, &p);
        g_session_alive = 0;
    }
    if (g_handle_alive && g_fn.nvFBCDestroyHandle) {
        NVFBC_DESTROY_HANDLE_PARAMS p;
        memset(&p, 0, sizeof(p));
        p.dwVersion = NVFBC_DESTROY_HANDLE_PARAMS_VER;
        g_fn.nvFBCDestroyHandle(g_session, &p);
        g_handle_alive = 0;
    }
    if (g_lib) {
        dlclose(g_lib);
        g_lib = NULL;
    }
}

static void usage(const char *prog) {
    fprintf(stderr,
        "usage: %s [--display :N] [--fps N] [--with-cursor 0|1] [--push 0|1] "
        "[--direct-capture 0|1] [--want-10bit 0|1]\n"
        "\n"
        "Streams raw BGRA (8-bit) or YUV420P10LE (Phase 2 VIDEO-09 10-bit)\n"
        "frames captured via NvFBC to stdout. See source for wire format.\n",
        prog);
}

int main(int argc, char **argv) {
    const char *display         = NULL;   /* use env $DISPLAY by default */
    int         fps             = 60;
    int         with_cursor     = 0;      /* off = client draws its own */
    int         push_model      = 1;
    int         direct_capture  = 0;      /* opt-in: known to be unstable */
    int         grab_timeout_ms = 1000;
    /* Phase 2 VIDEO-09 (10-bit Linux capture). When true AND the
     * installed NvFBC SDK exposes NVFBC_BUFFER_FORMAT_YUV420P10LE,
     * the capture session is set up to deliver 10-bit YUV instead of
     * 8-bit BGRA. The Python side requests this when ServerColorCaps
     * negotiates main10. SDK older than ~12.x without the enum value
     * silently degrades to BGRA + emits a warning to stderr (caught
     * by the parent, surfaces as the 'degraded' badge). */
    int         want_10bit      = 0;

    static const struct option opts[] = {
        {"display",        required_argument, 0, 'd'},
        {"fps",            required_argument, 0, 'f'},
        {"with-cursor",    required_argument, 0, 'c'},
        {"push",           required_argument, 0, 'p'},
        {"direct-capture", required_argument, 0, 'D'},
        {"want-10bit",     required_argument, 0, 't'},
        {"help",           no_argument,       0, 'h'},
        {0,0,0,0},
    };

    int opt;
    while ((opt = getopt_long(argc, argv, "d:f:c:p:D:t:h", opts, NULL)) != -1) {
        switch (opt) {
            case 'd': display        = optarg; break;
            case 'f': fps            = atoi(optarg); break;
            case 'c': with_cursor    = atoi(optarg); break;
            case 'p': push_model     = atoi(optarg); break;
            case 'D': direct_capture = atoi(optarg); break;
            case 't': want_10bit     = atoi(optarg); break;
            case 'h': usage(argv[0]); return 0;
            default:  usage(argv[0]); return 2;
        }
    }

    if (display && *display) {
        setenv("DISPLAY", display, 1);
    }
    if (!getenv("DISPLAY")) {
        log_line("no DISPLAY set — pass --display or set $DISPLAY");
        return 2;
    }

    /* Signal handlers: clean shutdown on parent death or explicit kill. */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT,  &sa, NULL);
    /* Don't let SIGPIPE kill us — write_all() detects EPIPE instead. */
    signal(SIGPIPE, SIG_IGN);

    /* Unbuffer stdout so frames flush immediately and Python sees them
     * without waiting for a buffer-full boundary. stdout is where the
     * binary stream goes, stderr is where diagnostics go. */
    setvbuf(stdout, NULL, _IONBF, 0);

    /* Load libnvidia-fbc.so.1 and resolve the entry point. */
    g_lib = dlopen(NVFBC_LIB, RTLD_NOW);
    if (!g_lib) {
        log_line("dlopen(%s) failed: %s", NVFBC_LIB, dlerror());
        return 3;
    }
    PNVFBCCREATEINSTANCE create_instance =
        (PNVFBCCREATEINSTANCE)dlsym(g_lib, "NvFBCCreateInstance");
    if (!create_instance) {
        log_line("dlsym(NvFBCCreateInstance) failed: %s", dlerror());
        dlclose(g_lib); g_lib = NULL;
        return 3;
    }

    memset(&g_fn, 0, sizeof(g_fn));
    g_fn.dwVersion = NVFBC_VERSION;
    NVFBCSTATUS st = create_instance(&g_fn);
    if (st != NVFBC_SUCCESS) {
        log_line("NvFBCCreateInstance failed: status=%d "
                 "(library version mismatch?)", (int)st);
        dlclose(g_lib); g_lib = NULL;
        return 3;
    }

    /* Create the NvFBC session handle. NvFBC creates its own GLX
     * context by default — we don't pass one. */
    NVFBC_CREATE_HANDLE_PARAMS ch;
    memset(&ch, 0, sizeof(ch));
    ch.dwVersion = NVFBC_CREATE_HANDLE_PARAMS_VER;
    st = g_fn.nvFBCCreateHandle(&g_session, &ch);
    if (st != NVFBC_SUCCESS) {
        log_line("NvFBCCreateHandle failed: status=%d", (int)st);
        log_nvfbc_err("NvFBCCreateHandle");
        dlclose(g_lib); g_lib = NULL;
        return 4;
    }
    g_handle_alive = 1;

    /* Sanity-check that capture is possible right now. */
    NVFBC_GET_STATUS_PARAMS gs;
    memset(&gs, 0, sizeof(gs));
    gs.dwVersion = NVFBC_GET_STATUS_PARAMS_VER;
    st = g_fn.nvFBCGetStatus(g_session, &gs);
    if (st != NVFBC_SUCCESS) {
        log_nvfbc_err("NvFBCGetStatus");
        cleanup();
        return 5;
    }
    if (!gs.bIsCapturePossible) {
        log_line("NvFBC reports capture not possible on this system "
                 "(GeForce gating? headless Xorg without NVIDIA output?)");
        cleanup();
        return 5;
    }
    if (!gs.bCanCreateNow) {
        log_line("NvFBC reports capture session cannot be created now "
                 "(another capture in progress, or X server in modeset)");
        cleanup();
        return 5;
    }
    log_line("NvFBC ready, screen=%ux%u driver_ver=%u",
             gs.screenSize.w, gs.screenSize.h, gs.dwNvFBCVersion);

    /* Create the capture session. We track the entire X screen in
     * system-memory mode, native BGRA, cursor off by default (client
     * draws its own), push-model for lowest latency.
     *
     * frameSize is PINNED to the initial screen size — this is
     * critical. Without this, NvFBC will happily resize its output
     * buffer if the X screen geometry changes mid-stream (a modeset,
     * a Flame fullscreen toggle, RandR reconfig, etc.) and the
     * downstream ffmpeg rawvideo encoder — which was started with a
     * fixed -video_size WxH — will interpret new bytes using the old
     * layout. That's what produced the "jigsaw puzzle" corruption
     * Randy saw mid-playback: every subsequent frame was sheared by
     * a constant row/stride offset. With frameSize pinned, NvFBC
     * internally scales to the locked dimensions so the wire format
     * to the encoder is stable for the life of the session.
     *
     * bAllowDirectCapture is off by default: it requires cursor to
     * be off AND has been observed to produce unstable frame layouts
     * under heavy compositor activity. Can be re-enabled with
     * --direct-capture 1 if a use case proves it helps. */
    NVFBC_CREATE_CAPTURE_SESSION_PARAMS cs;
    memset(&cs, 0, sizeof(cs));
    cs.dwVersion             = NVFBC_CREATE_CAPTURE_SESSION_PARAMS_VER;
    cs.eCaptureType          = NVFBC_CAPTURE_TO_SYS;
    cs.eTrackingType         = NVFBC_TRACKING_SCREEN;
    cs.bWithCursor           = with_cursor ? NVFBC_TRUE : NVFBC_FALSE;
    cs.frameSize             = gs.screenSize;   /* lock output size */
    cs.bDisableAutoModesetRecovery = NVFBC_FALSE;
    cs.bRoundFrameSize       = NVFBC_FALSE;
    cs.dwSamplingRateMs      = (fps > 0) ? (uint32_t)(1000 / fps) : 16;
    cs.bPushModel            = push_model ? NVFBC_TRUE : NVFBC_FALSE;
    cs.bAllowDirectCapture   = direct_capture ? NVFBC_TRUE : NVFBC_FALSE;

    st = g_fn.nvFBCCreateCaptureSession(g_session, &cs);
    if (st != NVFBC_SUCCESS) {
        log_nvfbc_err("NvFBCCreateCaptureSession");
        cleanup();
        return 6;
    }
    g_session_alive = 1;

    /* Set up the capture: NvFBC allocates a system-memory buffer (BGRA
     * by default; YUV420P10LE on the Phase 2 VIDEO-09 10-bit path) and
     * writes its address into pBuffer. It owns the buffer — we just
     * read from it. It re-allocates automatically on resolution change.
     *
     * Phase 2 VIDEO-09: 10-bit surface format selection. NVFBC_BUFFER_
     * FORMAT_YUV420P10LE has been in the NvFBC SDK since ~12.x; older
     * SDKs (and older driver bundles that ship an older NvFBC.h) don't
     * expose the enum value. Compile-out the 10-bit path when the SDK
     * is too old so the helper still builds against the older header
     * and degrades to BGRA at run time (the Python parent catches the
     * warning on stderr and flips the ServerColorCaps badge to
     * 'degraded'). */
    void *pBuffer = NULL;
    NVFBC_TOSYS_SETUP_PARAMS ss;
    memset(&ss, 0, sizeof(ss));
    ss.dwVersion     = NVFBC_TOSYS_SETUP_PARAMS_VER;
#ifdef NVFBC_BUFFER_FORMAT_YUV420P10LE
    if (want_10bit) {
        ss.eBufferFormat = NVFBC_BUFFER_FORMAT_YUV420P10LE;
        log_line("using 10-bit YUV420P10LE surface (VIDEO-09 main10 path)");
    } else {
        ss.eBufferFormat = NVFBC_BUFFER_FORMAT_BGRA;
    }
#else
    if (want_10bit) {
        log_line("SDK too old for NVFBC_BUFFER_FORMAT_YUV420P10LE; "
                 "falling through to BGRA (capability badge will be 'degraded')");
    }
    ss.eBufferFormat = NVFBC_BUFFER_FORMAT_BGRA;
#endif
    ss.ppBuffer      = &pBuffer;
    ss.bWithDiffMap  = NVFBC_FALSE;
    ss.ppDiffMap     = NULL;
    ss.dwDiffMapScalingFactor = 1;

    st = g_fn.nvFBCToSysSetUp(g_session, &ss);
    if (st != NVFBC_SUCCESS) {
        log_nvfbc_err("NvFBCToSysSetUp");
        cleanup();
        return 7;
    }

    log_line("capture loop starting (fps=%d cursor=%d push=%d direct=%d "
             "want_10bit=%d locked=%ux%u)",
             fps, with_cursor, push_model, direct_capture, want_10bit,
             gs.screenSize.w, gs.screenSize.h);

    /* Main capture loop. With push model + blocking grab, NvFBC
     * returns as soon as a new frame is available; otherwise it waits
     * up to grab_timeout_ms and returns the last frame. Either way we
     * forward whatever we get to stdout. */
    while (!g_stop) {
        NVFBC_FRAME_GRAB_INFO fi;
        memset(&fi, 0, sizeof(fi));

        NVFBC_TOSYS_GRAB_FRAME_PARAMS gp;
        memset(&gp, 0, sizeof(gp));
        gp.dwVersion      = NVFBC_TOSYS_GRAB_FRAME_PARAMS_VER;
        gp.dwFlags        = push_model ? NVFBC_TOSYS_GRAB_FLAGS_NOWAIT_IF_NEW_FRAME_READY
                                        : NVFBC_TOSYS_GRAB_FLAGS_NOFLAGS;
        gp.pFrameGrabInfo = &fi;
        gp.dwTimeoutMs    = (uint32_t)grab_timeout_ms;

        st = g_fn.nvFBCToSysGrabFrame(g_session, &gp);
        if (st == NVFBC_ERR_MUST_RECREATE) {
            /* Modeset happened. Destroy and recreate the session. */
            log_line("modeset detected — recreating capture session");
            NVFBC_DESTROY_CAPTURE_SESSION_PARAMS dp;
            memset(&dp, 0, sizeof(dp));
            dp.dwVersion = NVFBC_DESTROY_CAPTURE_SESSION_PARAMS_VER;
            g_fn.nvFBCDestroyCaptureSession(g_session, &dp);
            g_session_alive = 0;
            /* Loop until recreation succeeds or we're asked to stop. */
            while (!g_stop) {
                st = g_fn.nvFBCCreateCaptureSession(g_session, &cs);
                if (st == NVFBC_SUCCESS) break;
                struct timespec ts = { .tv_sec = 0, .tv_nsec = 200L * 1000 * 1000 };
                nanosleep(&ts, NULL);
            }
            if (g_stop) break;
            g_session_alive = 1;
            st = g_fn.nvFBCToSysSetUp(g_session, &ss);
            if (st != NVFBC_SUCCESS) {
                log_nvfbc_err("NvFBCToSysSetUp (post-recreate)");
                cleanup();
                return 8;
            }
            continue;
        }
        if (st != NVFBC_SUCCESS) {
            log_nvfbc_err("NvFBCToSysGrabFrame");
            cleanup();
            return 9;
        }

        /* Emit frame. In NOWAIT_IF_NEW_FRAME_READY mode NvFBC may
         * return an old frame if nothing changed; skip those so we
         * don't spam the pipe with duplicates. */
        if (!fi.bIsNewFrame) continue;

        uint32_t w  = fi.dwWidth;
        uint32_t h  = fi.dwHeight;
        uint32_t sz = fi.dwByteSize;
        if (!pBuffer || sz == 0) continue;

        uint8_t header[16];
        memcpy(header, FRAME_MAGIC, 4);
        /* Little-endian encoding explicit, so we're byte-order safe
         * regardless of host endianness (all our targets are LE, but
         * being explicit is cheap). */
        header[4]  = (uint8_t)(w       & 0xff);
        header[5]  = (uint8_t)((w>>8)  & 0xff);
        header[6]  = (uint8_t)((w>>16) & 0xff);
        header[7]  = (uint8_t)((w>>24) & 0xff);
        header[8]  = (uint8_t)(h       & 0xff);
        header[9]  = (uint8_t)((h>>8)  & 0xff);
        header[10] = (uint8_t)((h>>16) & 0xff);
        header[11] = (uint8_t)((h>>24) & 0xff);
        header[12] = (uint8_t)(sz      & 0xff);
        header[13] = (uint8_t)((sz>>8) & 0xff);
        header[14] = (uint8_t)((sz>>16)& 0xff);
        header[15] = (uint8_t)((sz>>24)& 0xff);

        if (write_all(STDOUT_FILENO, header, sizeof(header)) < 0) break;
        if (write_all(STDOUT_FILENO, pBuffer, sz) < 0) break;
    }

    log_line("shutting down");
    cleanup();
    return 0;
}
