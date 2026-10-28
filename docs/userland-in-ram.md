# 用户态放进内存：能放什么、放不下什么

结论先说：**XNU 本身支持"从内存里的一个块设备当根卷"，这条路一行驱动都不用加；但"iOS 的用户态"
放到内存里放不进去 —— 不是架构问题，是体积问题。**

## 1. 三个体积，彼此差两个数量级

| 目标 | 真实体积 | 来源 |
| --- | --- | --- |
| 现在这张镜像（内核到控制台） | 内核 51.5 MiB，boot.img 65.5 MiB | `work/vermeer-boot-ci` |
| 恢复模式用户态（launchd + dyld cache + /System） | **176 MiB** | `RestoreRamDisk` = `044-75545-038.dmg` |
| 正常 iOS 根卷 | **4.0 GiB**（`OS`）+ 1.23 GiB（`Cryptex1,SystemOS`） | 同一个 IPSW |

`044-75545-038.dmg` 不是压缩包，是 IM4P（`IM4P`/`rdsk`/`0`）包了一层裸 APFS 容器：
OCTET STRING 长度 `0x0b000000` = 184,549,376 字节，容器 `nx_block_size` 4096 ×
`nx_block_count` 45056 = 184,549,376 —— 一个字节不多不少，所以它就是 **176 MiB 的原始卷**，
没有"解开后会变大"这回事。它的 trust cache 只有 10 KB 级（`Firmware/044-75545-038.dmg.trustcache`）。

于是"内核 + 恢复用户态"= 52 + 176 = **228 MiB**，卷要开 240 MiB，boot.img 约 241 MiB。
而 `fastboot boot` 已经实测过：65.5 MiB 过，**198,120 KB（193.5 MiB）被拒**（`Device sent unknown
status code`，见 `board.toml` `[payload]` 的注释）。所以这条路不是"能不能挂"，而是"能不能传上去"。

## 2. 原版 XNU 的 RAM 根链路（全部是 stock 代码）

1. `iokit/bsddev/IOKitBSDInit.cpp:745`：`IOFindBSDRoot()` 在 `/chosen/memory-map` 找 `RAMDisk`
   （16 字节 = 物理起址 + 长度），找到就 `mdevadd(-1, virt>>12, size>>12, 0)` —— 得到 `/dev/md0`。
2. 同文件往下：解析 boot-arg `rd=` / `rootdev=`，`md0` 直接 `mdevlookup()` 成根设备。
3. `bsd/vfs/vfs_subr.c:1178` `vfs_mountroot()`：对 `rootdev` 依次让每个带 `vfc_mountroot` 的文件系统试挂。
4. `bsd/vfs/vfs_conf.c:197`：`mockfs` 就是其中之一（本板 `CONFIG_XNU_SOURCE_MOCKFS=y`）。
   `mockfs_mountroot` 把整个设备当 `/sbin/launchd` 映射；若首 4 KiB 是 `N4KROOT` 头，则映射两个文件
   （`/sbin/launchd` + `/probe.bin`，容器上限 32 MiB）。
5. `bsd/kern/bsd_init.c:871` → `execve("/sbin/launchd")` → EL0。

`patches/xnu/null-root-media.patch` 已经把 `IOService::setRootMedia` 对"根下面没有
IOBlockStorageDriver 祖先"的情况放行——`md0` 正好是这种，所以这条链在本板是通的，不需要
`NativeUfs`，也不需要 `NativeRamBlock`（后者仍是 `CONFIG_XNU_NATIVE_RAM_BLOCK=n`）。

DarwinBoot 侧的接口也现成：ADT 生成器的 `--ramdisk` 只往 `/chosen/memory-map` 写一个 16 字节
占位符，loader 把 initrd 的实际物理区间填进去，第二个 entry 传 `rd=md0`。

## 3. 为什么这仍然不是"开机 iOS"

- `mockfs` 只给两个文件，没有 dyld shared cache、没有 `/System`：Apple 的 `launchd` 是动态链接的，
  **挂上去也 exec 不起来**。想让它起来，就得让 `AppleAPFS` 把 `md0` 当 APFS 挂上，也就是必须放
  完整 176 MiB 的容器。
- 窗口：`darwinboot-memory-size` 合法区间是 256 MiB – 8 GiB（`DarwinWindowSizeValid`），现在取最小值
  `0x10000000`；内核自身跨 66 MiB。176 MiB 的 initrd 由 UEFI 另外分配，不占窗口，但总 RAM 要留够。
- 传输：228 MiB 的 boot.img 是这条路的第一道硬门槛，而它不是我们能决定的。

## 4. 现在的状态

- `unixnu build payload --userland FILE` 已实现：镜像里多一个 `Payloads/userland.img`，
  ADT 多一个 `RAMDisk` 占位符，`unixnu.toml` 多一个 `rd=md0` 的 entry（`id = "userland"`），
  默认 entry 不变。卷大小取 `[payload] size_mib_userland`（本板 240），默认 entry 仍是 64 MiB。
- `scripts/make-vermeer-adt.py --ramdisk` 只写占位符，不写 `ephemeral-storage`（那是 Data 种子专用）。
- 外壳已经算清：`044-75545-038.dmg` 共 184,549,403 字节 = **27 字节 IM4P 头** + 184,549,376 字节
  APFS 容器。容器起点 27 可以直接验证：`nx_superblock` 的魔数 `NXSB` 落在文件偏移 59，即容器内 32
  （块 0 的 `nx_magic` 字段），而 `nx_block_size`(4096) × `nx_block_count`(45056) 又恰好等于
  184,549,376。所以 `userland.img` = 去掉前 27 字节的原文件，不需要再解析任何外壳。

## 5. 顺序

1. 先把固件那个 `ASSERT ((BOOLEAN)(0==1))` 的死循环解决 —— 现在内核还没跑到 EL1 入口，用户态再小也轮不到。
2. 再验 `fastboot boot` 能不能吃下 ~241 MiB。
3. 都过了，才轮到 APFS 挂 `md0` 和 launchd。

## 6. 实际打出来的那张包（2026-10-29）

按上面第 2 条的上限打了一张，尺寸卡在 `fastboot boot` 的临时启动范围里，**不写任何分区**：

| 项 | 值 |
| --- | --- |
| 镜像 | `work/big/boot/boot-big.img` |
| 大小 | 253,206,528 字节（241.48 MiB），低于 ABL 常见的 256 MiB 下载上限 |
| sha256 | `a25cb9c08970fceb911548059340b4cf6db60d251b8c0d72a01157fcb2855bc8` |
| 内核段 | 1,540,486 字节，与 CI 的 `boot.img` 逐字节相同（`80fa29e1…`） |
| ramdisk | 240 MiB 的 FAT32 卷，落址 `0x01000000`–`0x10000000` |
| 卷内 | `kernel.macho` 51.5 MiB + `kernel.macho.adt` 13 KiB + `userland.img` 176 MiB + `unixnu.toml` |

`userland.img` 是剥离外壳后的 176 MiB APFS 容器，就是 `RestoreRamDisk` 的全部内容——恢复用户态
（launchd、dyld shared cache、`/System`）都在里面，不是只放了个空壳。

两个 entry：`userland`（默认，`rd=md0`）和 `console`（原来的内核控制台）。倒计时 3 秒后进
`userland`，3 秒内按上/下键可以退回 `console`。

窗口算术，确认不会撞上 `EFI_BAD_BUFFER_SIZE`：
内核跨 0x42F6000（67.0 MiB）+ boot_args 一页 + ADT 13 KiB 对齐 + ramdisk 176 MiB = 243.0 MiB，
上限是 `WindowSize - BLOCK_SIZE` = 254 MiB，余 11 MiB。

### 这张包是用什么打的

`scripts/mk-payload-volume.py`——CI 用 `mformat`/`mcopy`，本机没有 mtools 时用它生成**同样布局**的
FAT32 卷（512 字节扇区、单扇区簇、32 保留扇区、双 FAT、长文件名）。文件按给定顺序连续分配，
所以镜像可复现；读回校验过每个文件的 sha256 与源文件一致。

### 失败怎么读

- `fastboot: error: cannot load 'boot-big.img': data too large` 之类的**发送前**报错 = 超过
  `max-download-size`，跑一条 `fastboot getvar max-download-size` 就知道上限，然后按比例砍
  `size_mib_userland`。
- 传到一半 `Sending ... FAILED (Device sent unknown status code)` = 链路（这根线换过尾插小板，
  已知会随机掉线），不是尺寸。
- 传输成功、`fastboot boot` 后又落回菜单 = 内存窗口；屏上会有 `NO WINDOW … BIG RUN …`。
- 屏幕停在 `ASSERT [...](0==1)` = 固件断言，和这张包无关，先在**小包**上解决它再回来。
