/*
 * Probe DLL, built as d3d11.dll: injected like 3DMigoto, so Unity's LoadLibrary("d3d11.dll")
 * binds to it, but it forwards device creation untouched to the real (system32/DXMT)
 * d3d11.dll. No wrapping and no hooks. If the game survives this but not 3DMigoto, the
 * anti-tamper reacts to what 3DMigoto does, not to a foreign d3d11.dll being present.
 */
#include <windows.h>
#include <d3d11.h>

static HMODULE real_d3d11(void)
{
    static HMODULE module;
    if (!module) {
        wchar_t path[MAX_PATH];
        UINT n = GetSystemDirectoryW(path, MAX_PATH);
        lstrcpyW(path + n, L"\\d3d11.dll");
        module = LoadLibraryW(path);
    }
    return module;
}

static FARPROC real(const char *name)
{
    HMODULE module = real_d3d11();
    return module ? GetProcAddress(module, name) : NULL;
}

__declspec(dllexport) LRESULT CALLBACK CBTProc(int code, WPARAM wparam, LPARAM lparam)
{
    return CallNextHookEx(NULL, code, wparam, lparam);
}

typedef HRESULT(WINAPI *CreateDeviceFn)(IDXGIAdapter *, D3D_DRIVER_TYPE, HMODULE, UINT,
                                        const D3D_FEATURE_LEVEL *, UINT, UINT, ID3D11Device **,
                                        D3D_FEATURE_LEVEL *, ID3D11DeviceContext **);
typedef HRESULT(WINAPI *CreateDeviceAndSwapChainFn)(IDXGIAdapter *, D3D_DRIVER_TYPE, HMODULE, UINT,
                                                    const D3D_FEATURE_LEVEL *, UINT, UINT,
                                                    const DXGI_SWAP_CHAIN_DESC *, IDXGISwapChain **,
                                                    ID3D11Device **, D3D_FEATURE_LEVEL *,
                                                    ID3D11DeviceContext **);

__declspec(dllexport) HRESULT WINAPI D3D11CreateDevice(
    IDXGIAdapter *adapter, D3D_DRIVER_TYPE type, HMODULE software, UINT flags,
    const D3D_FEATURE_LEVEL *levels, UINT count, UINT sdk, ID3D11Device **device,
    D3D_FEATURE_LEVEL *level, ID3D11DeviceContext **context)
{
    CreateDeviceFn fn = (CreateDeviceFn)(void *)real("D3D11CreateDevice");
    return fn ? fn(adapter, type, software, flags, levels, count, sdk, device, level, context) : E_FAIL;
}

__declspec(dllexport) HRESULT WINAPI D3D11CreateDeviceAndSwapChain(
    IDXGIAdapter *adapter, D3D_DRIVER_TYPE type, HMODULE software, UINT flags,
    const D3D_FEATURE_LEVEL *levels, UINT count, UINT sdk, const DXGI_SWAP_CHAIN_DESC *desc,
    IDXGISwapChain **swapchain, ID3D11Device **device, D3D_FEATURE_LEVEL *level,
    ID3D11DeviceContext **context)
{
    CreateDeviceAndSwapChainFn fn = (CreateDeviceAndSwapChainFn)(void *)real("D3D11CreateDeviceAndSwapChain");
    return fn ? fn(adapter, type, software, flags, levels, count, sdk, desc, swapchain, device, level,
                   context)
              : E_FAIL;
}

BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH)
        DisableThreadLibraryCalls(instance);
    return TRUE;
}
