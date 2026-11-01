/*
 * Experimental probe: wait for MHYPBase, then replace only the three-byte
 * deliberate write at +0x1856a61 with NOPs. No VEH or other hooks are installed.
 */
#include <windows.h>

#define MHYPBASE_CRASH_RVA 0x1856a61

static HANDLE log_file = INVALID_HANDLE_VALUE;

static void log_message(const char *message)
{
    if (log_file != INVALID_HANDLE_VALUE) {
        DWORD written;
        WriteFile(log_file, message, (DWORD)lstrlenA(message), &written, NULL);
        FlushFileBuffers(log_file);
    }
    OutputDebugStringA(message);
}

static DWORD WINAPI patch_crash(LPVOID unused)
{
    int attempt;
    (void)unused;

    for (attempt = 0; attempt < 60000; attempt++) {
        HMODULE base = GetModuleHandleW(L"MHYPBase.dll");
        if (base) {
            unsigned char *address = (unsigned char *)base + MHYPBASE_CRASH_RVA;
            DWORD old_protect, ignored;
            if (address[0] != 0x48 || address[1] != 0x89 || address[2] != 0x16) {
                log_message("ZZMMI probe: crash instruction signature mismatch\n");
                return 2;
            }
            if (!VirtualProtect(address, 3, PAGE_EXECUTE_READWRITE, &old_protect)) {
                log_message("ZZMMI probe: VirtualProtect failed\n");
                return 3;
            }
            address[0] = address[1] = address[2] = 0x90;
            FlushInstructionCache(GetCurrentProcess(), address, 3);
            VirtualProtect(address, 3, old_protect, &ignored);
            log_message("ZZMMI probe: patched MHYPBase+0x1856a61 to NOPs\n");
            return 0;
        }
        Sleep(1);
    }
    log_message("ZZMMI probe: MHYPBase was not loaded\n");
    return 1;
}

__declspec(dllexport) LRESULT CALLBACK CBTProc(int code, WPARAM wparam, LPARAM lparam)
{
    return CallNextHookEx(NULL, code, wparam, lparam);
}

static BOOL in_target_process(void)
{
    wchar_t path[MAX_PATH];
    wchar_t *name;
    if (!GetModuleFileNameW(NULL, path, MAX_PATH))
        return FALSE;
    name = wcsrchr(path, L'\\');
    name = name ? name + 1 : path;
    return !lstrcmpiW(name, L"ZenlessZoneZero.exe");
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        HANDLE thread;
        DisableThreadLibraryCalls(instance);
        if (!in_target_process())
            return TRUE;
        log_file = CreateFileA("zzmmi_patch_probe.log", GENERIC_WRITE, FILE_SHARE_READ,
                               NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        thread = CreateThread(NULL, 0, patch_crash, NULL, 0, NULL);
        if (thread)
            CloseHandle(thread);
        else
            log_message("ZZMMI probe: CreateThread failed\n");
    } else if (reason == DLL_PROCESS_DETACH) {
        if (log_file != INVALID_HANDLE_VALUE)
            CloseHandle(log_file);
    }
    return TRUE;
}
