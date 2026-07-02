# 立创·实战派 ESP32-S3 的 ESP-Claw 配置指南

本文适用于立创开发板 **立创·实战派 ESP32-S3**，核心模组为
`ESP32-S3-WROOM-1-N16R8`（16 MB Flash、8 MB Octal PSRAM）。

ESP-Claw 原有开发板列表中没有与这块板完全匹配的型号。虽然
`esp32_S3_DevKitC_1` 使用相近的 ESP32-S3 模组，但它没有实战派板载的
ST7789、FT6336、ES8311、ES7210、QMI8658、PCA9557 和 GC0308 引脚配置，
因此不能直接使用。本仓库已增加专用 Board Manager 定义：

```text
开发板名称：lckfb_szpi_esp32s3
定义目录：application/edge_agent/boards/lckfb/lckfb_szpi_esp32s3
```

## 1. 已配置的硬件资源

下表依据立创开发板官方原理图说明和例程整理。I2C 地址在 Board Manager
配置中有两种表示方式，详见表后的说明。

| 功能           | 器件                   | ESP32-S3 引脚或地址                                                |
| -------------- | ---------------------- | ------------------------------------------------------------------ |
| 模组           | ESP32-S3-WROOM-1-N16R8 | 16 MB Flash、8 MB Octal PSRAM                                      |
| 公共 I2C/SCCB  | I2C0                   | SDA GPIO1、SCL GPIO2、100 kHz                                      |
| LCD            | ST7789，320×240       | MOSI GPIO40、SCLK GPIO41、DC GPIO39、SPI3、Mode 2、80 MHz          |
| LCD 片选       | PCA9557 IO0            | 低电平有效                                                         |
| LCD 背光       | PWM                    | GPIO42，低电平点亮                                                 |
| 触摸           | FT6336                 | I2C 7 位地址`0x38`                                               |
| 姿态传感器     | QMI8658                | I2C 7 位地址`0x6A`                                               |
| 音频输出       | ES8311                 | MCLK GPIO38、BCLK GPIO14、WS GPIO13、DOUT GPIO45；7 位地址`0x18` |
| 音频输入       | ES7210                 | MCLK GPIO38、BCLK GPIO14、WS GPIO13、DIN GPIO12；7 位地址`0x41`  |
| 功放使能       | NS4150B / PCA9557 IO1  | 高电平有效                                                         |
| IO 扩展器      | PCA9557                | 7 位地址`0x19`                                                   |
| TF 卡          | SDMMC 1-bit            | CLK GPIO47、CMD GPIO48、D0 GPIO21                                  |
| 摄像头         | GC0308 DVP             | XCLK 5、VSYNC 3、HREF 46、PCLK 7、D0～D7 为 16/18/8/17/15/6/4/9    |
| 摄像头休眠     | PCA9557 IO2            | 低电平唤醒                                                         |
| 用户/BOOT 按键 | 按键                   | GPIO0，低电平有效                                                  |

ESP Board Manager 的部分 I2C 设备配置使用 8 位地址，因此：

- FT6336：`0x38 << 1 = 0x70`
- ES8311：`0x18 << 1 = 0x30`
- ES7210：`0x41 << 1 = 0x82`
- PCA9557：`0x19 << 1 = 0x32`

QMI8658 的自定义元数据仍使用普通 7 位地址 `0x6A`。

## 2. 激活正确的 ESP-IDF 环境

ESP-Claw 当前文档要求 **ESP-IDF v5.5.4**。请优先安装并使用该版本，不要
用 v6.0.1 构建本项目。下面的安装目录只是示例，请按本机实际路径修改：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
. 'C:\esp\v5.5.4\esp-idf\export.ps1'
idf.py --version
```

预期版本输出：

```text
ESP-IDF v5.5.4
```

首次使用 Board Manager 时安装辅助包：

```powershell
python -m pip install --upgrade esp-bmgr-assist
```

为了避免 Windows 中文环境中 Board Manager 输出编码错误，可在当前
PowerShell 会话设置：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
```

## 3. 选择立创开发板

进入 ESP-Claw 的 `edge_agent` 工程目录：

```powershell
Set-Location 'D:\code\espclaw\esp-claw\application\edge_agent'
```

列出开发板：

```powershell
idf.py bmgr -c .\boards -l
```

选择本仓库新增的立创板定义：

```powershell
idf.py bmgr -c .\boards -b lckfb_szpi_esp32s3
```

成功时会出现类似信息：

```text
Board selected: lckfb_szpi_esp32s3
Board configuration generated successfully
```

不要输入下面这种只有路径的内容：

```powershell
application/edge_agent/boards/
```

PowerShell 会把它当作待执行的命令。查看目录应使用：

```powershell
Get-ChildItem .\boards
```

## 4. 配置、编译和烧录

Board Manager 会根据开发板定义生成目标芯片和外设配置，不需要再执行
`idf.py set-target`。如需调整 Wi-Fi、日志等工程选项：

```powershell
idf.py menuconfig
```

编译：

```powershell
idf.py build
```

在设备管理器中查看 CH340K 对应的串口号，然后烧录并打开监视器。以下以
`COM6` 为例：

```powershell
idf.py -p COM6 flash monitor
```

退出串口监视器：

```text
Ctrl+]
```

如果切换过开发板、ESP-IDF 版本或依赖，建议清理后重新生成：

```powershell
idf.py fullclean
idf.py bmgr -c .\boards -b lckfb_szpi_esp32s3
idf.py build
```

## 5. Board Manager 等待锁的处理

出现下面的信息时，不代表 `-c` 参数错误：

```text
[ESP_BMGR_ASSIST] Waiting for board manager bootstrap lock.
```

它表示另一个 Board Manager 进程正在初始化，或上一次进程异常退出后留下
了锁。先关闭其他正在运行 `idf.py` 的终端并等待片刻。确认没有相关 Python
或 `idf.py` 进程后，才可以删除提示中给出的具体 `.lock` 文件，再重新执行：

```powershell
$env:ESP_BMGR_LOCK_TIMEOUT = '120'
idf.py bmgr -c .\boards -b lckfb_szpi_esp32s3
```

`No such option: -c` 通常表示 `esp-bmgr-assist` 尚未装入当前激活的 ESP-IDF
Python 环境。请在激活 IDF 后重新执行：

```powershell
python -m pip install --upgrade esp-bmgr-assist
idf.py bmgr --help
```

## 6. 当前适配状态和注意事项

- Board Manager 已能识别该板，并通过 6 个 peripheral、10 个 device 的
  配置校验和代码生成。
- FT6336 使用 Espressif 的 FT5x06 系列驱动。
- PCA9557 与 TCA9554 的输入、输出、极性和方向寄存器地址一致，当前通过
  TCA9554 兼容驱动接入。需要在真机上继续验证 LCD 片选、功放使能和摄像头
  休眠控制。
- 当前 ESP-Claw Lua IMU 模块没有 QMI8658 后端。因此配置中保留了准确的
  QMI8658 地址和总线信息，但跳过自动初始化；若应用要读取姿态数据，需要
  后续增加 QMI8658 驱动适配。
- 当前配置已加入 GC0308 DVP 管脚和驱动选项，但仍需使用实物验证图像方向、
  颜色格式和帧率。
- 本机使用 IDF v6.0.1 测试时，工程在 CMake 阶段因现有 `mcp_mdns` 仍依赖
  IDF 6 已移除的 `json` 组件而停止。这不是立创板 YAML 的校验错误；应按
  项目要求使用 IDF v5.5.4 完成全量编译和烧录。

## 7. 配置文件说明

| 文件                         | 作用                                           |
| ---------------------------- | ---------------------------------------------- |
| `board_info.yaml`          | 开发板名称、芯片和厂商信息                     |
| `board_peripherals.yaml`   | I2C、I2S、SPI、背光和按键总线/引脚             |
| `board_devices.yaml`       | LCD、触摸、音频、TF 卡、IMU、摄像头等设备      |
| `setup_device.c`           | ST7789、FT5x06 和 PCA9557 兼容驱动工厂         |
| `sdkconfig.defaults.board` | 16 MB Flash、8 MB Octal PSRAM 和摄像头默认配置 |

## 8. 官方资料

- [立创·实战派 ESP32-S3 开发板介绍与硬件资源](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/introduction.html)
- [ST7789 LCD 官方例程说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/lcd-display.html)
- [FT6336 / LVGL 触摸官方说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/lvgl.html)
- [QMI8658 姿态传感器官方说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/attitude-sensor.html)
- [ES7210 音频输入官方说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/audio-input-es7210.html)
- [ES8311 音频输出与 PCA9557 官方说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/audio-output-es8311.html)
- [TF 卡官方说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/sd-card.html)
- [GC0308 摄像头官方说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/camera.html)
- [按键官方说明](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/beginner/key.html)
- [开源硬件资料入口](https://wiki.lckfb.com/zh-hans/szpi-esp32s3/open-source-hardware/)
