/*
 * Experimental probe: hide only this DLL from the PEB's three module-enumeration
 * lists after 3dmloader has verified injection and removed its global hook.
 * The loader's hash entries are retained, so GetModuleHandle still works.
 */
#include <windows.h>
#include <winternl.h>

typedef struct {
    ULONG Length;
    BOOLEAN Initialized;
    HANDLE SsHandle;
    LIST_ENTRY InLoadOrderModuleList;
    LIST_ENTRY InMemoryOrderModuleList;
    LIST_ENTRY InInitializationOrderModuleList;
} FULL_PEB_LDR_DATA;

typedef struct {
    LIST_ENTRY InLoadOrderLinks;
    LIST_ENTRY InMemoryOrderLinks;
    LIST_ENTRY InInitializationOrderLinks;
    PVOID DllBase;
} FULL_LDR_DATA_TABLE_ENTRY;

static HINSTANCE self_module;
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

static void unlink_entry(PLIST_ENTRY entry)
{
    entry->Blink->Flink = entry->Flink;
    entry->Flink->Blink = entry->Blink;
    entry->Flink = entry->Blink = entry;
}

static DWORD WINAPI hide_module(LPVOID unused)
{
    FULL_PEB_LDR_DATA *ldr;
    PLIST_ENTRY link;
    (void)unused;

    /* Let WaitForInjection finish and UnhookLibrary remove the global hook. */
    Sleep(2000);
    ldr = (FULL_PEB_LDR_DATA *)NtCurrentTeb()->ProcessEnvironmentBlock->Ldr;
    if (!ldr) {
        log_message("ZZMMI probe: no PEB loader data\n");
        return 1;
    }
    for (link = ldr->InLoadOrderModuleList.Flink;
         link != &ldr->InLoadOrderModuleList; link = link->Flink) {
        FULL_LDR_DATA_TABLE_ENTRY *entry =
            CONTAINING_RECORD(link, FULL_LDR_DATA_TABLE_ENTRY, InLoadOrderLinks);
        if (entry->DllBase == self_module) {
            unlink_entry(&entry->InLoadOrderLinks);
            unlink_entry(&entry->InMemoryOrderLinks);
            unlink_entry(&entry->InInitializationOrderLinks);
            log_message("ZZMMI probe: unlinked own PEB enumeration entries\n");
            return 0;
        }
    }
    log_message("ZZMMI probe: own module entry not found\n");
    return 2;
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

__declspec(dllexport) LRESULT CALLBACK CBTProc(int code, WPARAM wparam, LPARAM lparam)
{
    return CallNextHookEx(NULL, code, wparam, lparam);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        HANDLE thread;
        DisableThreadLibraryCalls(instance);
        self_module = instance;
        if (!in_target_process())
            return TRUE;
        log_file = CreateFileA("zzmmi_hide_probe.log", GENERIC_WRITE, FILE_SHARE_READ,
                               NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        thread = CreateThread(NULL, 0, hide_module, NULL, 0, NULL);
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
