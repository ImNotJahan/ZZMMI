/*
 * zzmmi-bridge.exe: runs inside Wine and replaces XXMI Launcher for injection.
 *
 * Arms 3dmloader.dll's SetWindowsHookEx-based hook for a DLL, starts the game, waits until
 * the DLL is loaded in the target process, then removes the hook. It only uses
 * 3dmloader.dll's exported API (HookLibrary / WaitForInjection / UnhookLibrary).
 *
 * Usage:
 *   zzmmi-bridge.exe --loader <3dmloader.dll> --dll <hook dll> --target <exe name>
 *                    [--timeout <seconds>] [--cwd <dir>] -- <command line to start the game>
 *
 * Prints one status line per step for the macOS side to parse:
 *   HOOKED | STARTED <pid> | INJECTED | ERROR <step> <code>
 * Exit codes: 0 injected, 1 usage, 2 loader, 3 hook, 4 start, 5 injection timeout.
 */
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

typedef int (*HookLibraryFn)(LPCWSTR dll, HHOOK *hook, HANDLE *mutex);
typedef int (*WaitForInjectionFn)(LPCWSTR dll, LPCWSTR process, int timeout);
typedef int (*UnhookLibraryFn)(HHOOK *hook, HANDLE *mutex);

static void status(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    vprintf(fmt, args);
    va_end(args);
    putchar('\n');
    fflush(stdout);
}

static FARPROC export_of(HMODULE lib, const char *name)
{
    return lib ? GetProcAddress(lib, name) : NULL;
}

/* Everything after "--" in the raw command line, quoting preserved. */
static const wchar_t *command_after_separator(const wchar_t *cmdline)
{
    const wchar_t *p = cmdline;
    int quoted = 0;
    for (; *p; p++) {
        if (*p == L'"')
            quoted = !quoted;
        else if (!quoted && p[0] == L'-' && p[1] == L'-' && (p == cmdline || p[-1] == L' ')
                 && (p[2] == L' ' || p[2] == 0)) {
            p += 2;
            while (*p == L' ')
                p++;
            return *p ? p : NULL;
        }
    }
    return NULL;
}

int wmain(int argc, wchar_t **argv)
{
    const wchar_t *loader = NULL, *dll = NULL, *target = NULL, *cwd = NULL;
    int timeout = 60;

    for (int i = 1; i < argc; i++) {
        if (!wcscmp(argv[i], L"--"))
            break;
        if (i + 1 >= argc)
            break;
        if (!wcscmp(argv[i], L"--loader")) loader = argv[++i];
        else if (!wcscmp(argv[i], L"--dll")) dll = argv[++i];
        else if (!wcscmp(argv[i], L"--target")) target = argv[++i];
        else if (!wcscmp(argv[i], L"--cwd")) cwd = argv[++i];
        else if (!wcscmp(argv[i], L"--timeout")) timeout = _wtoi(argv[++i]);
    }
    const wchar_t *command = command_after_separator(GetCommandLineW());
    if (!loader || !dll || !target || !command) {
        fprintf(stderr, "usage: zzmmi-bridge.exe --loader DLL --dll DLL --target EXE "
                        "[--timeout S] [--cwd DIR] -- COMMAND...\n");
        return 1;
    }

    HMODULE lib = LoadLibraryW(loader);
    HookLibraryFn hook_library = (HookLibraryFn)(void *)export_of(lib, "HookLibrary");
    WaitForInjectionFn wait_for_injection = (WaitForInjectionFn)(void *)export_of(lib, "WaitForInjection");
    UnhookLibraryFn unhook_library = (UnhookLibraryFn)(void *)export_of(lib, "UnhookLibrary");
    if (!hook_library || !wait_for_injection || !unhook_library) {
        status("ERROR loader %lu", GetLastError());
        return 2;
    }

    HHOOK hook = NULL;
    HANDLE mutex = NULL;
    /* 3dmloader codes: 100 another loader running, 200 DLL failed to load,
     * 300 DLL lacks CBTProc, 400 SetWindowsHookEx failed. */
    int rc = hook_library(dll, &hook, &mutex);
    if (rc != 0 || !hook) {
        status("ERROR hook %d", rc);
        return 3;
    }
    status("HOOKED");

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    ZeroMemory(&pi, sizeof(pi));
    si.cb = sizeof(si);
    wchar_t *mutable_command = _wcsdup(command);
    if (!CreateProcessW(NULL, mutable_command, NULL, NULL, FALSE, 0, NULL, cwd, &si, &pi)) {
        status("ERROR start %lu", GetLastError());
        unhook_library(&hook, &mutex);
        return 4;
    }
    status("STARTED %lu", pi.dwProcessId);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);

    rc = wait_for_injection(dll, target, timeout);
    unhook_library(&hook, &mutex);
    if (rc != 0) {
        status("ERROR inject %d", rc);
        return 5;
    }
    status("INJECTED");
    return 0;
}
