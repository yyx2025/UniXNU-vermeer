# 双系统：不擦安卓数据，也能进 iOS 用户态

结论：**在存储层面完全可行**，因为 UniXNU 的 iOS 根卷只是"一段裸块范围 + 一个 4 KiB 清单"，并不要求它叫 `userdata`。真正决定"要不要清数据"的只有一件事：这段范围放在哪里。

## 1. 先看清存储模型

`scripts/unixnu/userdata.py` 干的事很朴素——把 IPSW 里的两张磁盘镜像**原样**写进一段连续块范围：

```
0x000000  manifest（4 KiB，零填充到 1 MiB）
0x100000  window "OS"        ← OS.dmg 的原始字节，1 MiB 对齐
....      window "SystemOS"  ← Cryptex1,SystemOS 的原始字节
```

清单是 `SHNGROOT` 格式，记录每个窗口的名字/偏移/大小/sha256；内核侧由 `NativeUfs`（或 `NativeRamBlock`）当块设备读出来，APFS 直接在镜像里认卷。

所以 `[userdata]` 这个名字是**约定，不是约束**：
- `first_lba` / `last_lba` 只描述"写哪儿"；
- 唯一硬性检查是 `the image is larger than the userdata partition`（容量不够才报错）。

**这段范围指向哪个分区，就等于把 iOS 装在哪个分区。** 一加 13T 的移植记录里就写着 `userdata.partition=unixnu` —— 指向一个自己命名的分区，不是安卓的 userdata。

## 2. 三条路线

| | 根卷放哪 | 安卓数据 | 持久化 | 开机代价 | 风险 |
| --- | --- | --- | --- | --- | --- |
| **A RAM-only** | 内存（`NativeRamBlock`） | 完全不碰 | ❌ 重启即失 | 每次上传 8–10 GiB | 最低 |
| **B 独立分区** | 自建 GPT 分区 / 未用分区 / slot B | 完全不碰 | ✅ | 秒级 | 中（写错分区/OTA 覆盖） |
| C 写 userdata | userdata | **全清** | ✅ | 秒级 | 高（这是 sweet 走的，本项目不要） |

### 方案 A：全内存（推荐作为第一条打通的路）

HackiOS 就是这么干的，UniXNU 也有对应组件：

```toml
[kernel]
# 根卷来自内存，不需要 UFS
archives = ["build-arm-driver.py NativeUfs" → 换成 ram-block 构建器, ...]
```

```ini
CONFIG_XNU_NATIVE_RAM_BLOCK=y
CONFIG_XNU_NATIVE_UFS=n
```

```bash
# data 卷（/private/var）也放内存
./unixnu build payload --data-ramdisk <seed> --unique-chip-id <hex>
```

要点：
- `NativeRamBlock` 把内存窗口当块设备，`SHNGROOT` 清单照样适用；
- RAM 需求 ≈ OS + SystemOS（iPadOS 17 约 8–10 GiB）+ 运行时开销。**K70 的 12 GiB 机型勉强够，16 GiB 舒服**；`board.toml` 里现在的 `owned_memory_size = 0x130000000`（4.75 GiB）要按实测放大；
- 代价是每次开机都要从 PC 把镜像送进内存（USB 3.x 下几分钟），且**重启就回安卓、iOS 侧不保存任何东西**。

### 方案 B：独立分区（想要持久化才做）

三种落点，风险递增：

1. **自建分区**（最干净）：先把 userdata 缩小 N GiB，再新建一个 GPT 分区（例如叫 `unixnu`），把 `[userdata] first_lba/last_lba` 指过去。安卓侧要离线 resize f2fs，**这一步有数据风险**，务必先备份。
2. **占用未分配空间**：如果 GPT 尾部有空隙且够 8–10 GiB，直接新建分区，零风险。
3. **占用 slot B**：K70 是 A/B 机型，`system_b`/`vendor_b`/`product_b` 平时不用。若三者连续且总容量够，把它们当成一个范围用。
   - 代价：安卓 OTA 可能写 slot B，会破坏 iOS 卷 → 要么关自动更新，要么接受"每次更新后重刷 iOS 卷"。
   - 好处：**完全不动 userdata**，安卓照常。

写入方式：`./unixnu build userdata` 会产出 `out/<dev>/userdata/userdata-root.img` 并打印建议的 fastboot 命令。**不要照抄 `fastboot flash userdata`**——那是方案 C。要按你选的范围写：
- 若范围正好等于一个分区：`fastboot flash <分区名> userdata-root.img`
- 若跨多个分区：从 TWRP/root 下 `dd` 到对应块设备偏移，或用 `fastboot flash` 逐段写后再校验 sha256。

### 方案 C：写 userdata（否决）

`sweet` 就是这么做的，`fastboot flash userdata` 一刷安卓数据全没。本项目明确不走。

## 3. "双系统"其实已经成立了

`fastboot boot boot.img` **不写任何分区**，只从内存引导一次。所以形态天生就是：

- 平时开机 → 安卓（boot 分区没动过）
- 想进 iOS → `fastboot boot out/vermeer/boot/boot.img`

这已经满足"双系统"的日常含义，且不需要动任何分区表。真正的取舍只在"iOS 根卷是否需要持久化"。

## 4. 但"进用户态"还缺两块，跟存储无关

根卷解决的是 `launchd` 能不能挂上去。要让 **SpringBoard 出现在屏幕上**，还需要：

1. **显示**：现在 `CONFIG_XNU_NATIVE_LEGACY_DISPLAY=n`，只靠 UEFI 的 framebuffer 画不了 iOS 的合成画面。要么开 mobile/legacy display（需要实机证据），要么等 GPU 路径。
2. **输入**：`Goodix GT9916S` 驱动还没有（仓库里链的还是 sheng 的 Novatek）。

也就是说顺序应当是：**console（当前）→ 根卷 + launchd → 显示 → 触摸 → 才叫"进用户态"**。

## 5. 需要你先量出来的三个数

| 项 | 用途 | 怎么拿 |
| --- | --- | --- |
| GPT 布局（含空闲区、slot B 起止 LBA） | 决定方案 B 能不能做、往哪写 | 安卓 root 下 `sgdisk --print /dev/block/sda`，或 `ls -l /dev/block/by-name` |
| 最大可用 RAM 窗口 | 决定方案 A 是否可行 | UEFI 探针打印的 largest free window |
| OS / SystemOS 镜像实际大小 | 判断容量够不够 | `./unixnu build userdata` 会打印精确 GiB（超容量会直接报错） |
