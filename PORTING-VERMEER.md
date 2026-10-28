# vermeer 移植记录（Redmi K70 / SM8550）

本文记录板级决策、已经踩过的坑，以及首启前还缺什么。数值全部有出处：本机 `dtbo.img`/`uefiplat.cfg` 提取、上游 `UniXNU-sheng`，或同 SoC 手机移植（fuxi / nuwa / ishtar）。

## 1. 当前状态

| 环节 | 状态 | 依据 |
| --- | --- | --- |
| UEFI 固件（vermeerPkg → SILICIUM_UEFI.fd） | ✅ 真机可进 UEFI 菜单 | `firmware` job 常绿；本地 `vermeer-zh-boot.img` 已刷 |
| 内核源码 + prelink（iPadOS 17.7.11 / iPad7,1） | 🔧 链接依赖已修，等待 CI 验证 | `configs/vermeer_defconfig` |
| payload / boot.img | ⏳ 依赖 kernel + seed | `build-kernel.yml` 的 `bootimg` job |
| 存储（UFS） | ⏸ 首启阶段关闭，见 §2.1 | `CONFIG_XNU_NATIVE_UFS=n` |
| 显示 | 🔧 走 UEFI GOP + `splash_region`（video-mode 面板） | `[[bootimg.dtb_node]]` |
| 触摸 | ❌ 缺 Goodix GT9916S 驱动 | §4.3 |
| GPU / WiFi / 音频 | ❌ | `CONFIG_XNU_NATIVE_ADRENO=n` |

## 2. 已踩过的两个坑

### 2.1 UFS 必须在 prelink 之后才能进链接

**症状**（`kernel` job 最终链接，不是编译）：

```
Undefined symbols for architecture arm64:
  "IOBlockStorageDevice::gMetaClass", referenced from:
      NativeUfsBlock::MetaClass::MetaClass() in libNativeUfs.a(NativeUfs.o)
  "IOBlockStorageDevice::init(OSDictionary*)", referenced from:
      vtable for NativeUfsBlock in libNativeUfs.a(NativeUfs.o)
  ... requestIdle / doSetPriority / setProperties / doGetProvisionStatus / doUnmap / ctor / dtor
```

**原因**：`NativeUfs` 无条件以 `-DNATIVE_VERIFIED_BLOCK=1` 编译，`NativeUfsBlock` 继承 `IOBlockStorageDevice`。这个基类属于 Apple 的 IOStorageFamily，其实现只有在 prelink 之后才存在于内核里（`libVerifiedRootProbe.a` / 预链接的 kernelcache）。因此：

- **UFS 不能和 verified-root 拆开**。sheng 的 `sheng_defconfig` 是 `NATIVE_UFS=y` + `VERIFIED_ROOT_PROBE=y` 成对出现的。
- 同 SoC 手机的独立佐证：`hanzi123-bilp/unixnu-fuxi` 的 `configs/fuxi_defconfig`（stage-2a）写的就是 `CONFIG_XNU_NATIVE_UFS=n`，注释为「Linking NativeUfs needs the original IOBlockStorageDevice class … resolved to the addresses inside the prelinked kernelcache, and those come from audit-storage-interface.py's live capture, which no repository carries」；它的完整配置放在 `board.full.toml` / `fuxi_full_defconfig`，属于阶段 B。
- 恢复 UFS 的正确顺序：先跑 `scripts/audit-storage-interface.py` 拿到实机捕获 → `scripts/build-os-root.py` 产出 `libVerifiedRootProbe.a` → 再把 `NATIVE_UFS` 与 `VERIFIED_ROOT_PROBE` 一起打开。

**当前选择**：bring-up profile 关掉 UFS。没有块设备的内核会停在屏幕上的 `waiting for root device`，这正好是本阶段的验收标准。

### 2.2 `kernel-bootstrap.log` 里的 mockfs 未定义是预期的

`unixnu build kernel` 在缺少编译记录时会先跑一次 bootstrap pass，它**故意不带归档**（`XNU_BOOTSTRAP=1`），失败被吞掉，只要留下 `startup.o.json` 就继续。真正的判据是第二次（`kernel.log`）能否链接成功。排查时别看错日志。

## 3. 板级硬件事实（本机 dtbo / uefiplat 提取）

| 项 | 值 | 出处 |
| --- | --- | --- |
| UFS 控制器 | `ufshc@1d84000`（`+0x3000`，第三段 `0x1d90000+0x9000`），irq **265** | base DT |
| UFS PHY | `ufsphy_mem@1d80000` | base DT |
| UFS 颗粒 | **SK 海力士**（SK hynix），`manufacturer_id` 预期 `0x03` | 机主确认 |
| 内存颗粒 | **美光**（Micron）LPDDR5X | 机主确认 |
| SPMI PMIC-arb | `spmi@c42d000`（第二路 `@c432000`） | base DT |
| PMIC | `pm8550@1`、`pm8550vs@2/3/4/6`、`pm8550ve@5`、`pm8550b@7`、`pm8010@c/d`、`pmk8550@0` | base DT |
| 触摸 | **Goodix GT9916S**，I²C-addr `0x5d`，IRQ 25；主控 SE4 GPIO 44–47 / reset 24，次控 `gt9916S2` SE8 GPIO 56–59 | dtbo overlay |
| 面板 | **N81A**，`qcom,mdss_dsi_n81a_36_02_0a_duledsi_dsc_vid`，`panel-type = dsi_video_mode`（双 DSI + DSC） | dtbo overlay |
| 面板供电 | `dsi_panel_pwr_supply_N81A` | dtbo overlay |
| MDP / DSI | `mdss_mdp@ae00000`；`mdss_dsi_ctrl0@ae94000`、`ctrl1@ae96000`（hw v2.7） | base DT |
| GIC | dist `0x17100000/0x10000`，redist `0x17180000/0x200000` | base DT / ADT 生成器 |
| 计时器 | 19.2 MHz | ADT 生成器 |
| 保留内存 | 30 个 reserved 区共 477 MiB，**全部低于 4 GiB**（mpss 264 MiB @`0x8a800000` 最大） | `vermeer.dtb` |
| UEFI 窗 | FD `0xA7000000`、stack `0xA760D000`、Display Reserved `0xB8000000+0x2B00000` | `uefiplat.cfg` |
| splash | `/reserved-memory/splash_region` 由 `bootimg.dtb_node` 注入，否则 ABL 关屏 | `board.toml` |

> 显存/面板：**video-mode** 面板会持续扫描内存缓冲区，写什么显示什么，因此首启不需要写显示控制器（command-mode 的 Pixel 4/6a 才必须先接管控制器）。

> 颗粒：DTB 不写厂商（UFS 的 `manufacturer_id` 只在 device descriptor 里，LPDDR 的厂商在 ABL 读 MR 时才有）。**sheng 是三星 UFS + 三星内存**，所以 `NativeUfs` 里任何按厂商分支或写死时序的地方都不能照抄。恢复 UFS 阶段时的第一步不是读盘，是**认设备**：`hostQueryRead()` 目前只允许 `0x03`/`0x05`，连 `READ DESCRIPTOR(0x01)` 都没放开，所以先给它加 `0x01`、读 device descriptor 前 `0x20` 字节、看 offset `0x15` 是不是 `0x03`（SK hynix）。认对了再谈初始化；海力士的 Write Booster / auto-hibernate 在 bring-up 阶段先关掉走常规读写，避免把 cache 行为当成控制器缺陷。

## 4. 首启前必须解决的缺口

### 4.1 `[userdata]` GPT 几何（阶段 B 才需要）
当前仍是 sheng 的占位值。需要真机：

```bash
fastboot oem get-partition-info userdata      # 或 GPT dump
```

### 4.2 `unique_chip_id`（首启可先用固定值）
`[payload.adt] unique_chip_id = "required"`。它只用于给 `IOPlatformUUID` 播种；**任意非零 8 字节十六进制都能让首启走通**，CI 里用 `0000000000000001` 即可（`os-root` job 本来也是这么干的）。正式设备再换成真值，避免 UUID 撞车。

### 4.3 触摸：缺 Goodix GT9916S 驱动
仓库现在链的 `drivers/NativeNovatekTouch` 是 sheng 的 Novatek NT36532E，**与 K70 无关**（`xyyyds0106` 那份 `proprietary-files.txt` 里的 `novatek_nt36532_n81a_fw_csot.bin` 也是同一误会）。K70 的实际控制器是 **Goodix GT9916S**。

可借力：`dipa-studio/ios-sweet` 的补丁 `0063`（GENI FIFO + Goodix GT9896 协议）、`0060`（SPMI 按键 + GENI I²C 触摸）、`0062`（RPMh 触摸供电）。GT9896 与 GT9916S 同属 Berlin 家族，报文格式接近，但寄存器块与固件加载流程要按 GT9916S 的 datasheet/厂商驱动核对。

另外别忘 **edge touch**：从屏幕边缘起手的触摸必须标记为边缘触摸，否则上滑回不到主屏。

### 4.4 GPU 的 GMU 窗不同
`CONFIG_XNU_NATIVE_ADRENO=n` 的原因写在 defconfig 里：K70 的 GMU 窗是 `0x3d68000`，sheng 是 `0x3d6a000`；`prepare-sheng-gmu.py` 还需要 SHA 固定的 Linux a6xx 源 + sheng 编译后的 DTS。恢复要连同 vermeer 自己的 GPU 输入集一起做。

### 4.5 面板 PCD
`Display` 侧的 PCD 是 1440×3200。K70 是 2K（3200×1440）面板，与 dtbo 里的 `n11_42_02_0a` 1440×3200 一致；但同一份 dtbo 里还有 `n81a` 的双 DSI timing，**首启时以 UEFI GOP 报告的实际 width/height/stride 为准**，M4 阶段再做 iPhone/iPad 画布居中。

## 5. 首启验收标准

`boot.img` 组成：`BootShim + SILICIUM_UEFI.fd + vermeer.dtb（含 splash 节点）+ payload 卷`。

真机执行（不写任何分区）：

```bash
fastboot boot out/vermeer/boot/boot.img
```

通过标准：**屏幕上出现内核 console 输出**（或 panic screen 的报错文字），且不发生「几秒后自动重启」——后者通常意味着看门狗未关（`wdt=-1` / PM8550 WDT）。

## 6. 上游与社区参考

| 来源 | 借什么 |
| --- | --- |
| `UniXNU/UniXNU-sheng` | SoC 级驱动、payload 链、`porting.toml` 写法 |
| `hanzi123-bilp/unixnu-fuxi` | 阶段 A/B 拆分、UFS 放到 prelink 之后、`build-legacy-stub.py`、`audit-storage-interface.py` 的实机捕获要求 |
| `matejkoo217/UniXNU-fuxi`、`0721GPT/UniXNU-fuxi` | SM8550 手机的板级数值（fuxi 同为 8 Gen 2） |
| `mansar1337/UniXNU-vermeer` | 源码 XNU 基线、`tools/make_vermeer_adt.py`、UEFI 探针（CPU/GOP/空闲内存） |
| `dipa-studio/ios-sweet` | 无串口调试（framebuffer console + 分区日志环）、Goodix 触摸协议、HFS+ 根 |
| `oosexgang/hackios-xnu-kernel` | UEFI loader 常驻 + USB 日志回传、设备目录化移植结构 |
