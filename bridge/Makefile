# Cross-compiles the Windows-side helpers (brew install mingw-w64).
CC = x86_64-w64-mingw32-gcc
CFLAGS = -O2 -Wall -Wextra -municode -static
OUT = build

all: $(OUT)/zzmmi-bridge.exe $(OUT)/probes/noop/zzmmi_noop.dll \
	$(OUT)/probes/noop-skip-crash/zzmmi_noop_skip_crash.dll \
	$(OUT)/probes/noop-patch-crash/zzmmi_noop_patch_crash.dll \
	$(OUT)/probes/noop-hide-module/zzmmi_noop_hide_module.dll \
	$(OUT)/probes/passthru/d3d11.dll

$(OUT)/zzmmi-bridge.exe: zzmmi_bridge.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -o $@ $<

$(OUT)/probes/noop/zzmmi_noop.dll: probes/noop_hook.c
	@mkdir -p $(dir $@)
	$(CC) -O2 -Wall -shared -static -o $@ $< -luser32

$(OUT)/probes/noop-skip-crash/zzmmi_noop_skip_crash.dll: probes/noop_skip_crash.c
	@mkdir -p $(dir $@)
	$(CC) -O2 -Wall -Wextra -shared -static -o $@ $< -luser32

$(OUT)/probes/noop-patch-crash/zzmmi_noop_patch_crash.dll: probes/noop_patch_crash.c
	@mkdir -p $(dir $@)
	$(CC) -O2 -Wall -Wextra -shared -static -o $@ $< -luser32

$(OUT)/probes/noop-hide-module/zzmmi_noop_hide_module.dll: probes/noop_hide_module.c
	@mkdir -p $(dir $@)
	$(CC) -O2 -Wall -Wextra -shared -static -o $@ $< -luser32

$(OUT)/probes/passthru/d3d11.dll: probes/passthru_d3d11.c
	@mkdir -p $(dir $@)
	$(CC) -O2 -Wall -shared -static -o $@ $< -luser32

clean:
	rm -rf $(OUT)

.PHONY: all clean
