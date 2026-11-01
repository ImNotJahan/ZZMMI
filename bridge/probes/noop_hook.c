/*
 * Probe DLL: does nothing except provide the CBTProc entry point 3dmloader requires.
 * Injecting it the same way as 3DMigoto tells us whether the anti-tamper reacts to the
 * injection itself (a foreign module loaded through a windows hook) rather than to
 * anything 3DMigoto does once loaded.
 */
#include <windows.h>

__declspec(dllexport) LRESULT CALLBACK CBTProc(int code, WPARAM wparam, LPARAM lparam)
{
    return CallNextHookEx(NULL, code, wparam, lparam);
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH)
        DisableThreadLibraryCalls(instance);
    return TRUE;
}
