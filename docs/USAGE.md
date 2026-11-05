# 使用说明

以下命令从项目目录运行。打开打印机、安装 **12 mm 覆膜 TZe**，先通过系统蓝牙设置完成配对。
将设备信息中的蓝牙地址输入变量，不需要写入源码：

```bash
read -r -p 'PT-D610BT 蓝牙地址: ' PRINTER_MAC
```

## 查询状态

```bash
python3 -I -B scripts/ptd610bt_bluetooth.py status --mac "$PRINTER_MAC" --channel 2
```

状态查询不会打印。正常打印以普通用户运行，无需 `sudo`、CUPS 或 Brother SDK。
KDE 的通用“连接”按钮可能显示 `ProfileUnavailable`；已配对设备仍可能通过直接打开
RFCOMM/SPP 工作，应以这条状态查询的结果判断。脚本不会自动配对或重启蓝牙服务。

## 文本与图片

先生成预览，确认内容后显式打印：

```bash
python3 -I -B scripts/ptd610bt_bluetooth.py preview --text 'BT TEST' --length-mm 30 --output /tmp/ptd610-text-preview.png
python3 -I -B scripts/ptd610bt_bluetooth.py print --mac "$PRINTER_MAC" --channel 2 --text 'BT TEST' --length-mm 30
```

图片使用同一组命令；替换为你明确选择的本地图片路径：

```bash
python3 -I -B scripts/ptd610bt_bluetooth.py preview --image '/path/to/label.png' --length-mm 30 --output /tmp/ptd610-image-preview.png
python3 -I -B scripts/ptd610bt_bluetooth.py print --mac "$PRINTER_MAC" --image '/path/to/label.png' --length-mm 30
```

`--text` 和 `--image` 二选一。默认字体仅用于 ASCII 文本；其他文字需通过 `--font`
指定含有相应字形的字体文件。图片转换成 64 点高的黑白光栅。预览命令完全离线，不会连接打印机，
且拒绝覆盖任何已经存在的输出文件；重复预览请换新文件名。

## LBX 标签

```bash
python3 -I -B scripts/ptd610bt_lbx.py preview '/path/to/label.lbx' --output /tmp/ptd610-lbx-preview.png
python3 -I -B scripts/ptd610bt_lbx.py print '/path/to/label.lbx' --mac "$PRINTER_MAC" --channel 2
```

源 LBX 只读，不会被解压到磁盘、保存或上传。渲染器使用记录的可打印背景区域计算自动长度，
不会把编辑器的最大长度当作实际出纸长度。支持单张横向自动长度布局、未旋转的简单文本与内嵌图片；
Arial 由 Liberation Sans 替代，显示可能与 P-touch Editor 有细微差别。其他字体、条码、
多页、旋转/翻转、自动换行等未支持布局会报错。此工具不提供编辑器或模板传输到标签机的功能。

## 双击直接打印

按 [部署说明](DEPLOYMENT.md) 安装单独的 LBX 类型和默认关联后，双击 `.lbx` 或 `.LBX`
会直接打印一张，完成或出错后保留 Konsole 窗口。没有每次打印前的确认框。
配置生成器和安装配置步骤本身均不打印。

一次只打开一个标签文件，等当前任务结束后再打开下一个。未实现并发任务队列；重复双击可能产生重复任务。

## 返回码与不确定结果

| 返回码 | 含义 |
|---|---|
| 0 | 预览/状态成功，或打印已收到设备完成及空闲回包 |
| 2 | 输入、布局、设备状态或连接错误 |
| 3 | 已开始发送任务，但结果不确定；不会自动重发 |
| 130 | 用户中断 |

CLI 参数用法错误也返回 2。打印成功回包证明设备报告完成，实际文字、方向和裁切仍应检查实体标签。
出现 `PRINT OUTCOME UNCERTAIN` 时先检查打印机已经输出的内容，再决定是否手动重试。
命令行打印脚本可加 `--trace` 查看解码状态；LBX CLI 不提供该参数。

## 为什么会切出一段空白

当前使用小留白和自动裁切：可能先切掉约 **23 mm 空白前导带**，随后输出标签。
这通常是机内打印头与切刀距离造成的走带，不是重复打印。
文本/图片的 `--length-mm` 是图像长度；标签两端还会各增加约 2 mm，不能当作最终裁切总长度。
LBX 采用自身背景区域的内容长度。长短数值均受实际设备走带影响。
参见 [Brother PT-D610BT 留白与裁切说明](https://support.brother.com/g/b/faqend.aspx?c=us_ot&faqid=faqp00001293_017&lang=en&prod=d610bteus)。
