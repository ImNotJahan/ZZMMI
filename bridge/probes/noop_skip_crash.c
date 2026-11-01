/*
 * Experimental probe: identical to noop_hook.c except for a narrowly scoped VEH.
 * MHYPBase.dll+0x1856a61 is `mov [rsi], rdx`; on rejection it executes with
 * rsi=0x1000. Skip that one instruction once to determine whether this is the
 * only enforcement point, without suppressing any other exception.
 */
#include <windows.h>
#include <stdint.h>

#define MHYPBASE_CRASH_RVA 0x1856a61

static HANDLE log_file = INVALID_HANDLE_VALUE;
static PVOID veh;
static LONG skipped;

static void log_message(const char *message)
{
    if (log_file != INVALID_HANDLE_VALUE) {
        DWORD written;
        WriteFile(log_file, message, (DWORD)lstrlenA(message), &written, NULL);
        FlushFileBuffers(log_file);
    }
    OutputDebugStringA(message);
}

static LONG CALLBACK skip_crash(PEXCEPTION_POINTERS pointers)
{
    PEXCEPTION_RECORD record = pointers->ExceptionRecord;
    HMODULE base;
    const unsigned char *address;

    if (record->ExceptionCode != EXCEPTION_ACCESS_VIOLATION ||
        record->ExceptionInformation[0] != EXCEPTION_WRITE_FAULT ||
        record->ExceptionInformation[1] != 0x1000 || skipped)
        return EXCEPTION_CONTINUE_SEARCH;

    base = GetModuleHandleW(L"MHYPBase.dll");
    if (!base)
        return EXCEPTION_CONTINUE_SEARCH;
    address = (const unsigned char *)base + MHYPBASE_CRASH_RVA;
    if (record->ExceptionAddress != address ||
        address[0] != 0x48 || address[1] != 0x89 || address[2] != 0x16)
        return EXCEPTION_CONTINUE_SEARCH;

    skipped = 1;
    pointers->ContextRecord->Rip += 3;
    log_message("ZZMMI probe: skipped MHYPBase+0x1856a61 write to 0x1000\n");
    return EXCEPTION_CONTINUE_EXECUTION;
}

__declspec(dllexport) LRESULT CALLBACK CBTProc(int code, WPARAM wparam, LPARAM lparam)
{
    return CallNextHookEx(NULL, code, wparam, lparam);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(instance);
        log_file = CreateFileA("zzmmi_veh_probe.log", GENERIC_WRITE, FILE_SHARE_READ,
                               NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        veh = AddVectoredExceptionHandler(1, skip_crash);
        log_message(veh ? "ZZMMI probe: VEH installed\n" : "ZZMMI probe: VEH install failed\n");
    } else if (reason == DLL_PROCESS_DETACH) {
        if (log_file != INVALID_HANDLE_VALUE)
            CloseHandle(log_file);
    }
    return TRUE;
}
