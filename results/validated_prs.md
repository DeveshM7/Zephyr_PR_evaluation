# EmbedEval — Validated Zephyr PR Instances

**14 PRs confirmed FAIL→PASS** across the full pipeline run.
Platform: `qemu_x86` | Simulation: Zephyr SDK QEMU inside Docker (Ubuntu 24.04)

---

## Summary Table

| PR | Title | Merged | FAIL Type | +Lines | -Lines | Files | Linked Issues |
|----|-------|--------|-----------|-------:|-------:|------:|---------------|
| [#90096](https://github.com/zephyrproject-rtos/zephyr/pull/90096) | sys: clock: add sys_clock api and remove posix from iso c time | 2025-06-24 | Linker error | +796 | -619 | 35 | [#88882](https://github.com/zephyrproject-rtos/zephyr/issues/88882), [#88555](https://github.com/zephyrproject-rtos/zephyr/issues/88555) |
| [#102462](https://github.com/zephyrproject-rtos/zephyr/pull/102462) | kernel: Add K_TIMEOUT_SUM() macro | 2026-01-22 | Compile error | +246 | -1 | 6 | [#102133](https://github.com/zephyrproject-rtos/zephyr/issues/102133) |
| [#38777](https://github.com/zephyrproject-rtos/zephyr/pull/38777) | lib: os: mpsc_pbuf: Test and Fix for concurrency issues | 2022-12-29 | Runtime assertion | +729 | -186 | 8 | [#38268](https://github.com/zephyrproject-rtos/zephyr/issues/38268) |
| [#65697](https://github.com/zephyrproject-rtos/zephyr/pull/65697) | posix: pthread: ensure pthread_key_delete() removes correct key | 2023-11-24 | Runtime assertion | +37 | -1 | 2 | [#65696](https://github.com/zephyrproject-rtos/zephyr/issues/65696) |
| [#82272](https://github.com/zephyrproject-rtos/zephyr/pull/82272) | kernel/sched: Correct k_sleep() return value when a 32-bit tick wraparound occurs | 2024-12-03 | Runtime assertion | +58 | -1 | 5 | [#79863](https://github.com/zephyrproject-rtos/zephyr/issues/79863) |
| [#43405](https://github.com/zephyrproject-rtos/zephyr/pull/43405) | logging: Add option for prolonged backend initialization | 2022-06-09 | Runtime assertion | +284 | -11 | 6 | [#38494](https://github.com/zephyrproject-rtos/zephyr/issues/38494) |
| [#33690](https://github.com/zephyrproject-rtos/zephyr/pull/33690) | Fix return code for unimplemented functions in driver APIs (ENOSYS vs ENOTSUP) | 2021-03-30 | Runtime assertion | +112 | -90 | 13 | [#23727](https://github.com/zephyrproject-rtos/zephyr/issues/23727) |
| [#58030](https://github.com/zephyrproject-rtos/zephyr/pull/58030) | tests: mgmt: mcumgr: Add smp_version test | 2023-05-26 | Compile error | +524 | -19 | 15 | [#58003](https://github.com/zephyrproject-rtos/zephyr/issues/58003) |
| [#66762](https://github.com/zephyrproject-rtos/zephyr/pull/66762) | posix: env: support for environ, getenv(), setenv(), unsetenv() | 2024-03-08 | Compile error | +860 | -4 | 19 | [#66861](https://github.com/zephyrproject-rtos/zephyr/issues/66861), [#66862](https://github.com/zephyrproject-rtos/zephyr/issues/66862), [#66863](https://github.com/zephyrproject-rtos/zephyr/issues/66863), [#66864](https://github.com/zephyrproject-rtos/zephyr/issues/66864) |
| [#73799](https://github.com/zephyrproject-rtos/zephyr/pull/73799) | posix: add support for mmap, memlock, shared memory, and mprotect | 2024-06-14 | Compile error | +1210 | -87 | 25 | [#59950](https://github.com/zephyrproject-rtos/zephyr/issues/59950), [#59951](https://github.com/zephyrproject-rtos/zephyr/issues/59951), [#59952](https://github.com/zephyrproject-rtos/zephyr/issues/59952), [#59953](https://github.com/zephyrproject-rtos/zephyr/issues/59953) |
| [#74435](https://github.com/zephyrproject-rtos/zephyr/pull/74435) | sensor: fix: decouple Sensor Async API request from its execution | 2024-07-09 | Compile error | +536 | -9 | 21 | [#73676](https://github.com/zephyrproject-rtos/zephyr/issues/73676) |
| [#90060](https://github.com/zephyrproject-rtos/zephyr/pull/90060) | sys: timeutil: add utility functions for struct timespec | 2025-05-22 | Compile error | +982 | -15 | 8 | [#88115](https://github.com/zephyrproject-rtos/zephyr/issues/88115) |
| [#101203](https://github.com/zephyrproject-rtos/zephyr/pull/101203) | net: sockets: tls: Add support for multiple client sessions in DTLS server socket | 2026-01-23 | Compile error | +1777 | -262 | 5 | [#64954](https://github.com/zephyrproject-rtos/zephyr/issues/64954) |
| [#51809](https://github.com/zephyrproject-rtos/zephyr/pull/51809) | Bluetooth: Move crypto toolbox functions from `smp.c` into their own file | 2022-11-10 | Compile error | +757 | -332 | 14 | [#51297](https://github.com/zephyrproject-rtos/zephyr/issues/51297) |

---

## By FAIL Type

### Compile / Linker Error (9 PRs)
These fail at build time on base commit — the strongest possible FAIL signal.

| PR | Title | +Lines |
|----|-------|-------:|
| [#90096](https://github.com/zephyrproject-rtos/zephyr/pull/90096) | sys: clock: add sys_clock api | +796 |
| [#102462](https://github.com/zephyrproject-rtos/zephyr/pull/102462) | kernel: Add K_TIMEOUT_SUM() macro | +246 |
| [#58030](https://github.com/zephyrproject-rtos/zephyr/pull/58030) | mcumgr: Add smp_version test | +524 |
| [#66762](https://github.com/zephyrproject-rtos/zephyr/pull/66762) | posix: env: getenv/setenv/unsetenv | +860 |
| [#73799](https://github.com/zephyrproject-rtos/zephyr/pull/73799) | posix: mmap/shm/mprotect | +1210 |
| [#74435](https://github.com/zephyrproject-rtos/zephyr/pull/74435) | sensor: Async API decoupling | +536 |
| [#90060](https://github.com/zephyrproject-rtos/zephyr/pull/90060) | sys: timespec utility functions | +982 |
| [#101203](https://github.com/zephyrproject-rtos/zephyr/pull/101203) | net/tls: DTLS multi-session server | +1777 |
| [#51809](https://github.com/zephyrproject-rtos/zephyr/pull/51809) | Bluetooth: crypto toolbox refactor | +757 |

### Runtime Assertion (5 PRs)
These build successfully on base commit but tests fail at runtime.

| PR | Title | +Lines |
|----|-------|-------:|
| [#38777](https://github.com/zephyrproject-rtos/zephyr/pull/38777) | lib: mpsc_pbuf concurrency fix | +729 |
| [#65697](https://github.com/zephyrproject-rtos/zephyr/pull/65697) | posix: pthread_key_delete fix | +37 |
| [#82272](https://github.com/zephyrproject-rtos/zephyr/pull/82272) | kernel/sched: k_sleep() wraparound | +58 |
| [#43405](https://github.com/zephyrproject-rtos/zephyr/pull/43405) | logging: prolonged backend init | +284 |
| [#33690](https://github.com/zephyrproject-rtos/zephyr/pull/33690) | driver API: ENOSYS vs ENOTSUP | +112 |

---

## Stats

| Metric | Value |
|--------|-------|
| Total validated PRs | 14 |
| Compile/linker error FAILs | 9 (64%) |
| Runtime assertion FAILs | 5 (36%) |
| Smallest PR (lines added) | #65697 (+37 lines) |
| Largest PR (lines added) | #101203 (+1777 lines) |
| Oldest PR | #33690 (2021-03-30) |
| Newest PR | #102462 (2026-01-22) |
| Median lines added | ~700 |
