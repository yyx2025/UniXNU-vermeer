# vermeer userland

一个**自包含的用户态**：单一静态 arm64 Mach-O，被 XNU 当作 PID 1 执行，然后 fork/exec 出自己的
shell 和心跳进程。没有 libSystem、没有 dyld、没有共享缓存——凡是它用到的能力，都能在源码里一指到具体
系统调用号（`darwin.h` 抄自 `bsd/kern/syscalls.master`）。

## 它凭什么能被 exec

内核的 `mockfs` 把**根设备本身**当作可执行文件：

- `mockfs.h`：「the assumption that our root device is in fact a mach-o file ... the root
  directory, a mountpoint for devfs, and an executable representing the root device」；
- `mockfs_vnops.c` 的 lookup 把 `sbin`/`usr`/`lib` 解析回根节点，`launchd` 解析到那个文件节点；
- `bsd/kern/kern_exec.c` 的 `load_init_program()` 于是 `execve("/sbin/launchd")`，也就是这份镜像。

所以「用户态」不需要任何新驱动，只需要三件事：RAM 里的根设备（`rd=md0`）、一个合法的 Mach-O、
以及让 AMFI 认它。

## 镜像由三部分构成

| 产物 | 作用 |
| --- | --- |
| `launchd.macho` | 程序本体，静态链接、无 dyld |
| `kernel.macho.trustcache` | 版本 1 头 + 版本 2 模块，里面只有一条 20 字节 cdhash |
| `userland.img` | `N4KROOT` 容器：第 0 页头 + `launchd` @4096 + `motd.txt` @下一个 4 KiB 边界 |

`N4KROOT` 是 `patches/xnu/mockfs-test-file.patch` 引入的格式，让 mockfs 在一个块设备上呈现两个文件：

```
struct mockfs_test_header {
    char     magic[8];       // "N4KROOT\0"
    uint32_t version;        // 1
    uint32_t count;          // 2
    uint64_t launch_offset;  // 必须是 4096
    uint64_t launch_size;
    uint64_t test_offset;    // 4 KiB 对齐、>= 8192
    uint64_t test_size;
};
```

内核因此有 `/sbin/launchd`（就是这份程序）和 `/probe.bin`、`/dyld`（`motd.txt`）。
`init.c` 里的 `probe_data_file()` 会把第二个文件读回来打印——这是「根是真文件系统」的证据，
不是「exec 了一个 blob」。

限制：整个容器 ≤ 32 MiB（`mockfs_vfsops.c` 的 mountroot 校验）。

## 为什么要自己签名

Linux CI 上没有 `codesign(1)`，所以 `adhoc.py` 做了它做的两件事：

1. 在头部空档插入 `LC_CODE_SIGNATURE`，追加一个 SuperBlob，里面是一个 `CodeDirectory`
   （版本 `0x20400`，88 字节头，SHA-256，每 4 KiB 一页）；
2. 算出 cdhash = `sha256(CodeDirectory)[:20]`，包成 DarwinBoot 认识的信任缓存。

字段布局抄自 `osfmk/kern/cs_blobs.h`，接受/拒绝的判据抄自 `bsd/kern/ubc_subr.c` 的
`cs_validate_codedirectory()`：页大小必须等于内核页（4K 或 16K）、`hashSize` 必须等于该哈希类型的
长度、`hashOffset`/`nSpecialSlots`/`nCodeSlots` 必须自洽、identifier 必须 NUL 结尾。
这些都对上，内核才会去算 cdhash，再去信任缓存里查。

工作流用 `--owner-trust-cache` 传它：这条路径跳过 `board.toml [payload] trust_cache_sha256`
白名单，因为白名单里是 sheng 的缓存哈希。

## 构建

```sh
bash userland/build.sh userland/build
```

需要一个能跨到 `aarch64-apple-ios17.0` 的 clang 和 `ld64.lld`，不需要 Apple SDK——源码不包含
任何系统头文件。产物见 `build/`。

## 它做什么

PID 1（supervisor）：

1. 打开 `/dev/console`，读 `sysctl` 打硬件信息（核数、内存、页大小、内核版本串）；
2. `mmap` 1 MiB 匿名内存，逐页写图案再读回校验，然后 `munmap`——EL0 的 VM 通路；
3. 通过 mockfs 读回 `/sbin/launchd` 和 `/probe.bin`；
4. `fork` 出 `hb`（心跳）和 `sh`（shell），每次都用 `execve("/sbin/launchd")` 重启成对应角色，
   所以每 spawn 一次就重走一遍 open / load_machfile / AMFI；
5. 永远 `wait4()`，谁退了就重启谁；PID 1 绝不自己退出。

shell 提供 `help sysinfo mem ls cat ps uptime echo fork reboot exit`。
`/dev/console` 的写出内核图形控制台画到屏上——`pexpert/arm/pe_init.c` 在
`boot_args->Video` 非零时调用 `PE_initialize_console(..., kPEGraphicsMode)`，
而 `DarwinBoot.c` 已经把 UEFI GOP 的基址/步幅/宽高写进了 `boot_args->Video`。
用户进程不碰显存 MMIO：不需要，也没被映射。

## 它不是什么

**这不是 Apple 的 launchd，也起不了 SpringBoard。** 那需要另外三件事：

- `AppleAPFS` 挂载 OS 卷（kext 在预链接内核里，但需要块设备驱动）；
- AMFI 接受 Apple 签名的镜像（信任缓存里要有 Apple 的模块，或者走 owner 策略）；
- SEP 提供 keybag（本板没有 SEP 驱动）。

今天这份用户态是它们要坐的那一层。容器格式已经给它留了位置：同一个 `N4KROOT` 能带第二个文件，
真 launchd + 真 dyld 共享缓存就是那么递进去的。
