# 能不能跑更新的 iOS（含"iOS 27"）—— 事实与路线

先纠正一个前提：**iOS 27 现在不存在**。2026-10 的最新版本线是 iOS/iPadOS 26，"27"要等 2027 年。所以本文回答的是"能不能换到比 17.7.11 更新的系统"。

## 结论

| 路线 | 系统 | 身份 | 可行性 | 说明 |
| --- | --- | --- | --- | --- |
| A（现状） | iPadOS 17.7.11 (21H461) | iPad7,1 / J120 | ✅ 已在跑 | sheng 同款，vermeer 沿用 |
| **B（唯一可行的升级）** | **iPadOS 18.7.10 (22H374)** | **iPad7,11（A10）** | ⚠️ 原理可行，工程量大 | A10 无 PAC，内核是纯 arm64 |
| C | iOS 26 / 未来的 27 | iPhone（arm64e） | ❌ 短期不可行 | 需要 PAC 栈，无任何项目做过 |

## 证据

**① 非 PAC 的 iPad 一直支持到 iPadOS 18.7.x。**
`api.ipsw.me/v4/device/iPad7,11` 显示该机型最新固件为 **18.7.10 / 22H374 / 2026-08-17**，且 18.7.9、18.7.8、18.7.7 连续在发（2026 年内）。iPad 7 是 **A10 Fusion**，A10 不实现指针认证（PAC 从 A12 才有），因此这条线跑的是**纯 arm64 内核**，与 iPhone X（A11）同一机制。

**② 对应世代的 XNU 源码是公开的。**
`apple-oss-distributions/xnu` 的 tag 一路排到 `xnu-12377.121.6`；其中 `xnu-11215.81.4`、`xnu-11417.x` 属于 iOS/iPadOS 18 世代，`xnu-12377.x` 属于 26 世代。也就是说"换更新的内核"在源码层面没有版权或可得性障碍。

**③ iPhone 路线为什么卡在 16.7.16。**
本地实测（`tools/extract_fileset.py`，对象是 `iPhone15,3` iOS 16.0 的 kernelcache）：

```
cpu=arm64e  filetype=12  fileset_entries=241
```

cpusubtype=2 即 **arm64e**，整个用户态与内核都按 PAC 编译。iPhone XS 之后所有 iPhone 都是这样，而 iOS 17+ 不再为 A11 提供固件 → iPhone 路线的上限就是 16.7.16，没有"再往上"的空间。

顺带一个直接证据：同一个 kernelcache 里躺着 `com.apple.driver.AppleT8120PMGR`（A16 的 PMGR）和 `com.apple.AGXG15P_A0`（A16 的 GPU 驱动）——**这就是为什么不能直接搬 Apple 的 stock kernelcache**，它带着 Apple 自己芯片的驱动。UniXNU 的做法是从中只捡 `board.toml [kernel.prelink] roots` 白名单里的 kext 去 prelink，白名单就是"提取清单"。

## 走路线 B 需要动什么

1. **新 OS profile**：`profiles/os/ipados-18.7.10-22H374`，身份换成 iPad7,11（`/product` 从该机型真机的 DeviceTree 抄），`[ipsw]` 指向 iPadOS 18.7.10 的 restore。
2. **XNU 源码换代**：从 `xnu-10063.141.1` 换到 `xnu-11215.x`，把现有补丁系列（machine config、framebuffer console、mockfs、AMFI 放行、AppleKeyStore stub…）逐条重放。这一步通常会在 `makedefs`、MIG、DriverKit iig、kext 依赖上撞墙。
3. **prelink 重新对齐**：18 的 kernelcache 结构与 roots 列表都会变（新增/删除 kext、地址不同），`emit_prelink.py` 的 `slide`、`early_security_init` 偏移要重新算。
4. **boot-args 与框架假设重验**：work interval、AudioClock、IOSurface 语义在 18 上都有变动。

**风险**：这是"换一个大版本再走一遍 bring-up"，不是改配置。而且目前**全世界没有一个移植跑在 iPadOS 18 上**——17.7.11 之所以是公共底座，是因为它是被验证过的那一版。

## 对当前任务的影响：不要现在做

K70 连 17.7.11 都还没亮屏。正确顺序是：

1. 先用 CI 产出的 `boot.img` 让 K70 出内核 console（当前阶段）；
2. 再补 UFS（`audit-storage-interface.py` 实机捕获 → `build-os-root.py` → 开 UFS + verified-root）；
3. 再补 Goodix GT9916S 触摸；
4. **换 OS profile 应作为独立分支实验**（例如 `feat/ipados-18`），不污染主线。真正值得先试的不是 18，而是"17.7.11 + iPad 7 身份"——因为 iPad7,11 与 iPad7,1 同为 arm64，但面板比例更接近手机，能减少 letterbox 面积。

## 附：本次提取用的工具

`scripts/extract-fileset.py`：解析 Mach-O fileset，输出 kext 清单、`cpusubtype`（0=arm64 / 2=arm64e）、按子系统分组，并可 `--grep` 定位。判断"某个 IPSW 能不能用"时，先跑它看 cpusubtype 和 kext 名单，比读文档快。
