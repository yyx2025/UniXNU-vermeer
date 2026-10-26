# 上机检查清单（K70 首启）

刷机前的必过项，以及首屏三种结果各自对应什么。**每次上机都从头过一遍**，别跳步——环境问题伪装成镜像问题的次数，比真镜像问题多得多。

## 0. USB 链路闸门（不通过就不要刷）

`fastboot boot` 不写分区，失败最多重开机；但**任何 flash 操作在链路不稳时都可能写坏**。所以先确认链路。

### 判断「幽灵设备」还是「真掉线」

`Get-PnpDevice` 里 `Status = Unknown` 表示**当前不存在**（历史残留），不是故障。只看在线的：

```powershell
Get-PnpDevice -PresentOnly |
  Where-Object { $_.Class -in 'AndroidUsbDeviceClass','USB','WPD' } |
  Format-Table Status,Class,FriendlyName -Auto
```

有 `Error` / `Unknown` 的**在线**条目才是问题。

### 稳定性测试

手机进 fastboot，连发 30 次小命令：

```powershell
$fail = 0
for ($i = 1; $i -le 30; $i++) {
  & fastboot getvar product *> $null
  if ($LASTEXITCODE -ne 0) { $fail++ }
  Start-Sleep -Milliseconds 300
}
"失败 $fail / 30"
```

| 结果 | 判断 | 动作 |
| --- | --- | --- |
| 0 / 30 | 链路稳 | 可以刷 |
| 1–3 / 30 | 轻度不稳 | 换线再测；刷时手机别动 |
| 4+ / 30 | 不可用 | **停止**，修线/端口/尾插 |

### 报错码对照

| 报错 | Win32 | 含义 |
| --- | --- | --- |
| `指定的设备不存在 (433)` | ERROR_NO_SUCH_DEVICE | 设备从总线消失——**物理层** |
| `连接到系统的设备没有发挥作用 (31)` | ERROR_GEN_FAILURE | 在但无法通信 |
| `信号灯超时时间已到 (121)` | ERROR_SEM_TIMEOUT | 传输超时 |
| `未知 USB 设备(设备描述符请求失败)` | — | 主机连枚举都没完成 |

出现 433 就等于「手机掉线了」，和镜像大小、分区、协议都无关。

### 修法顺序（按概率）

1. **换线**——劣质线最常见症状就是「能充电、数据断续」。
2. **换口**——机箱后面板/主板直连；优先试 **USB 2.0 口**（SuperSpeed 差分线对线材质量极敏感）。避开前面板、Hub、扩展坞。
3. **关 USB 节能**：

```powershell
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setactive SCHEME_CURRENT
```

4. **清幽灵设备**（管理员）。残留的失败枚举会让下次插拔更乱：

```powershell
Get-PnpDevice |
  Where-Object { $_.Status -eq 'Unknown' -and $_.Class -in 'USB','AndroidUsbDeviceClass' } |
  ForEach-Object { pnputil /remove-device $_.InstanceId }
```

5. **换电脑**：同一根线插另一台。仍掉 → 线或手机尾插；正常 → 原机口/驱动。
6. **尾插**：断电后酒精棉签轻擦触点、压缩空气吹灰；刷机时手机平放、线不受侧向力、别边充边刷。

**判据**：安卓下 `adb devices` 也疯狂掉线 → 线/尾插；只有 fastboot 掉 → 主机侧。

### 一个强判据：超级快充要不要靠重启才生效

如果手机「每次都要硬重启才能激活超级快充」，那基本可以判定**尾插/充电 IC 有问题**，而不是线材：

- 超级快充（120W）要过 CC1/CC2 检测 + PD 协商，用的是 Type-C 里**另外几根引脚**；
- 这些引脚接触不良时，PD 状态机会卡在低档，插着不动也一直是慢充；
- 重启会重新初始化 PD 控制器，于是「重启之后就好了」——这正是状态机卡死的典型表现；
- 而 USB 数据用的 D+/D- 也在同一个座上，虚焊/氧化会**同时**造成「快充要重启」和「fastboot 随机掉线」。

先用充电侧验证，比反复试数据线快：

```bash
adb shell dumpsys battery                     # 看 status / 电压电流
adb shell cat /sys/class/power_supply/*/uevent # 宽电压、电流上限、PD 状态
```

对照实验：

| 现象 | 判断 |
| --- | --- |
| 换原装 120W 充电器 + 原装 6A 线，仍要重启才快充 | 手机侧（尾插/充电 IC） |
| 同一套充电器插另一台手机立刻快充 | 排除充电器 |
| 插头转到某个角度/轻压才触发快充 | **尾插虚焊**，需要换尾插小板 |
| 拔插几次偶尔能触发 | 触点氧化或灰尘，先清 |

**处理顺序**：断电后酒精棉签清触点 + 压缩空气吹 → 仍不行就换尾插小板（K70 是独立小板，成本不高）。修好之后，fastboot 掉线大概率一起消失——这两个症状是同一处硬件。


## 1. 刷机前

```powershell
fastboot devices                    # 必须连续 10 次都能看到同一个序列号
fastboot getvar current-slot        # 确认在 a 还是 b
fastboot oem device-info            # 确认 bootloader 已解锁
```

镜像：`out/vermeer/boot/boot.img`（当前 68,657,152 字节 / 65.5 MiB）。

> 曾经失败的那版是 202,874,880 字节，卡在 `Sending 'boot.img' (198120 KB)`。`[payload] size_mib` 已从 192 改成 64。

## 2. 引导

```powershell
fastboot boot boot.img
```

**不写任何分区**。传输成功后会看到 UEFI 画面 → UniXNU 菜单（3 秒倒计时）→ 内核。

## 3. 首屏三种结果

### A. 屏幕出现文字（内核 console 或 panic）

链路全通。按文字内容判断下一步：

- 停在 `waiting for root device` → 正常，存储阶段未做（`CONFIG_XNU_NATIVE_UFS=n`）；
- 出现 `panic` + backtrace → 记下最上面一行和 `Debugger` 前的函数名；
- 出现乱码/花屏文字 → framebuffer 的 base/stride 不对，对照 serial 里的 video base。

### B. 黑屏无文字

按顺序查：

1. splash 节点是否生效——`board.toml [[bootimg.dtb_node]]` 给 `/reserved-memory/splash_region` 写了 `0xb8000000+0x2b00000`；ABL 没有 splash 区会直接关屏；
2. panic 屏是否打上——`payload.json` 里 `panic_screen` 必须为 `true`；
3. UEFI 自己有没有画面？若 UEFI 菜单都不出，问题在固件而不是内核。

### C. 几秒内自动重启

看门狗没关干净：

- boot-args 已有 `sheng-disable-watchdogd=1`；
- 但 PM8550 的硬件 WDT 需要单独处理，下一步加 `wdt=-1` / 对应驱动；
- 也可能是内存与 reserved 区重叠导致瞬间复位，核对 `owned_memory_size` 与 K70 的 30 个保留区。

## 4. 回到安卓 / 恢复

```powershell
fastboot reboot            # 正常回安卓
```

卡住不动就长按 **电源 + 音量下** 十秒以上，再正常开机。**因为什么都没写，安卓一定还在。**

## 5. 记录

每次上机都记一行：镜像 sha256、`getvar` 结果、屏幕现象、耗时。排障靠的是对比，不是记忆。
