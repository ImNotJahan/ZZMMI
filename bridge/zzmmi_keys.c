/* zzmmi-keys.exe: press keys inside the running Wine session, so 3DMigoto sees them.
 *
 * 3DMigoto polls its hotkeys (reload_config = F10, [Key*] sections, ...) once per frame with
 * GetAsyncKeyState. In Wine that reads the desktop's key state in wineserver, which SendInput
 * from any process in the same prefix updates. So this lets the Mac side trigger
 * "reload mods" (F10) without synthesizing macOS events or needing Accessibility permission.
 *
 * Usage: zzmmi-keys.exe [--hold MS] KEY [KEY...]
 *   KEY is a virtual-key code: decimal (121), hex (0x79) or F1-F24.
 *   All KEYs go down in order, stay held for MS milliseconds (default 150, i.e. several
 *   frames even at low frame rates), then come up in reverse order. So `0x11 0x12 0x79`
 *   is Ctrl+Alt+F10.
 *
 * First, any modifier Wine thinks is held (and that isn't one of the KEYs) gets a key-up.
 * Wine's macOS driver maps Command to Alt, so Cmd+Tab out of the game leaves Alt down in
 * Wine: macOS delivers the Command release to the other app. Alt then stays down until the
 * game window is focused again, and 3DMigoto ignores `no_modifiers` hotkeys like F10/F6.
 * Exit status: 0 ok, 1 bad arguments, 2 SendInput failed.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#define MAX_KEYS 8

static int parse_key(const wchar_t *s) {
    if ((s[0] == L'F' || s[0] == L'f') && s[1]) {
        wchar_t *end;
        long n = wcstol(s + 1, &end, 10);
        if (!*end && n >= 1 && n <= 24)
            return VK_F1 + (int)n - 1;
        return -1;
    }
    wchar_t *end;
    long vk = wcstol(s, &end, 0);
    return (!*end && vk > 0 && vk < 256) ? (int)vk : -1;
}

static int send_key(WORD vk, BOOL up) {
    INPUT in = {0};
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = vk;
    in.ki.wScan = (WORD)MapVirtualKeyW(vk, MAPVK_VK_TO_VSC);
    in.ki.dwFlags = up ? KEYEVENTF_KEYUP : 0;
    return SendInput(1, &in, sizeof(in)) == 1;
}

/* Each modifier's generic VK, which GetAsyncKeyState also reports, then its left and right VKs. */
static const WORD MODIFIERS[][3] = {
    {VK_MENU, VK_LMENU, VK_RMENU},
    {VK_CONTROL, VK_LCONTROL, VK_RCONTROL},
    {VK_SHIFT, VK_LSHIFT, VK_RSHIFT},
    {0, VK_LWIN, VK_RWIN},
};

static int is_down(WORD vk) {
    return (GetAsyncKeyState(vk) & 0x8000) != 0;
}

static int requested(WORD vk, const WORD *keys, int n) {
    for (int i = 0; i < n; i++)
        if (keys[i] == vk)
            return 1;
    return 0;
}

/* Release modifiers stuck down in Wine's key state, except ones the caller asked to press. */
static int release_stuck_modifiers(const WORD *keys, int n) {
    int ok = 1;
    for (size_t m = 0; m < sizeof(MODIFIERS) / sizeof(MODIFIERS[0]); m++) {
        const WORD *vks = MODIFIERS[m];
        if (requested(vks[0], keys, n) || requested(vks[1], keys, n) || requested(vks[2], keys, n))
            continue;
        for (int side = 1; side <= 2; side++)
            if (is_down(vks[side]))
                ok &= send_key(vks[side], TRUE);
        if (vks[0] && is_down(vks[0]))  /* generic one still down without a side: release it too */
            ok &= send_key(vks[0], TRUE);
    }
    return ok;
}

int wmain(int argc, wchar_t **argv) {
    DWORD hold = 150;
    WORD keys[MAX_KEYS];
    int n = 0;
    for (int i = 1; i < argc; i++) {
        if (!wcscmp(argv[i], L"--hold") && i + 1 < argc) {
            hold = (DWORD)wcstoul(argv[++i], NULL, 10);
            continue;
        }
        int vk = parse_key(argv[i]);
        if (vk < 0 || n == MAX_KEYS) {
            fwprintf(stderr, L"bad key: %ls\n", argv[i]);
            return 1;
        }
        keys[n++] = (WORD)vk;
    }
    if (!n) {
        fwprintf(stderr, L"usage: zzmmi-keys.exe [--hold MS] KEY [KEY...]\n");
        return 1;
    }
    int ok = release_stuck_modifiers(keys, n);
    for (int i = 0; i < n; i++)
        ok &= send_key(keys[i], FALSE);
    Sleep(hold);
    for (int i = n - 1; i >= 0; i--)
        ok &= send_key(keys[i], TRUE);
    if (!ok) {
        fwprintf(stderr, L"SendInput failed: %lu\n", GetLastError());
        return 2;
    }
    return 0;
}
