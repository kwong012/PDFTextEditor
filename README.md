# PDFTextEditor

在**电子文本 PDF** 上原地修改文字，并尽可能让改动在视觉上与原文件一致。
适用于 Word 等 Office 软件导出的可选中文本 PDF，也适用于网页 / Markdown 转出来的
PDF（这类文件常把每个字做成独立的 Type3 内嵌字形）。不适用于扫描件，以及文字已被
转成矢量轮廓的 PDF。

嘿嘿（￣︶￣）↗　本来是用来p假条的，顺便小改pdf的，后来pymupdf基础上加GUI,简化了命令行里的一些操作。

目前纯个人使用，我也不知道还有什么bug，界面撕裂什么的就先不管了，能p就是好 (:

![PDFTextEditor 界面：左侧「原图 / 改后」对比预览，右侧编辑与修改清单](assets/screenshot.png)

---

## 主要功能

- **即时预览**：选中文字、填好「替换」就立刻看到效果，不用先加进清单；
  点「添加到清单」才真正写入规则（预览状态会明确标出来）。
- **矩形框选**：按住左键在文字上拖一个框，一次改一整句。网页 / Markdown 转出来的
  PDF 常常**一个字一个片段**，点选只能改一个字，框选正好解决这个；Word 类的 PDF
  一行一个片段，框选就是选整行。拖动不足 3 像素仍按单击处理，两种用法都保留。
  （目前框选只支持**一行内**，跨行会提示并只取锚点那一行。）
- **「原文」可以手改**：文字层抽出来是乱码时（Type3 内嵌字形、字体缺 Unicode 映射），
  直接在「原文」里改成正确文字即可 —— 替换位置是按原位置走的，不受改文字影响。
- **Type3 内嵌字形不再误报缺字体**：这类字形由 PDF 自带（网页/MD 转 PDF 常见），
  系统里根本没有对应字体文件，所以程序用中文字体近似它，并在识别行说明来龙去脉；
  真缺字体时才弹浮窗。开文档/翻页时日志还会提示本页片段数、Type3 片段数与
  「有几个字形没有文字映射」（那些字抽出来会是乱码）。
- **替换文字缺字形会自动回退**：把英文片段改成中文时，程序**逐字**检查字形，缺的字改用
  包含该字形的字体（默认宋体优先，可用 `font_fallback` 指定），并在「替换」行右侧与日志里
  说明「哪几个字 → 落到哪个字体」，不会再画成方框；选区里混了多种字体时也会提示。
- **颜色跟随原文**：默认沿用原文颜色（灰字、红字不会被改成黑色）。
  点色块可用调色板选色；「取色器」可以直接在页面上点一下取该处颜色。
- **字体自动定位**：自动读 PDF 里的字体名，在系统字体目录里找对应字体
  （支持 `.ttc` 的各个字面、粗体/斜体；`Heiti`、`Helvetica` 这类走替身表映射）。
  系统里**没有**该字体时会弹浮窗说明要装哪个字体 —— 把字体文件放进
  `data\fonts\`（便携版）或 `%LOCALAPPDATA%\PDFTextEditor\fonts\` 即可，
  **不需要**装进 Windows 字体目录，也不用管理员权限。
- **字号 / 描边**：字号按原文精确取值（10.45 就是 10.45，不再取整）；
  伪加粗描边默认**关闭**，需要时在「高级」里打开（或用「设置 ▾ → 自动标定描边」）。
- **对比预览**：左「原图」右「改后」，滚动与缩放同步；「适应窗口」自动缩放。
- **修改清单**：同一处重复添加会覆盖；可单条删除或清空。

---

## 下载

从 [Releases](../../releases) 下载 `PDFTextEditor-Portable-<版本>.zip`，
解压到任意位置，
双击 `PDFTextEditor.exe` 就能用。
「卸载」＝把解压出来的文件夹删掉。

- 系统要求：**64 位 Windows 10 及以上**
- 别放在 `C:\Program Files` 这类需要管理员权限的目录，否则字体缓存写不进去
- 首次运行若弹「Windows 已保护你的电脑」，点「更多信息 → 仍要运行」
- 解压后目录里有 `README-Portable.txt`（使用说明）；首次修改文字会在 `data\` 生成字体缓存，可随时删除

> 想从源码运行、或自己重新出包，见下面的「源码运行」与「打包exe」。

---

## 源码运行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 图形界面

```bash
python pdf_editor_gui.py
```



### 3. 命令行

```bash
copy config.example.json config.json
python edit_pdf.py --config config.json --dry-run   # 先看会匹配到哪些片段
python edit_pdf.py --config config.json             # 生成
```

---

## 配置

GUI 的「导出 config / 导入 config」与 CLI 使用**同一套格式**，可以互相复用。

```jsonc
{
  "src": "原文件路径（相对 config 所在目录或绝对路径）",
  "out": "输出路径",
  "font": "C:\\Windows\\Fonts\\simsun.ttc",  // 全局默认字体文件
  "font_name": "simsun",
  "font_face": 0,           // .ttc 里用第几个字面（从 0 开始）
  "font_size": 10,          // 全局默认字号
  "color": [0, 0, 0],       // 全局默认文字颜色；不写则沿用原文颜色
  "bold_stroke": 0,         // 伪加粗描边宽度（默认 0 = 不描边，见下文）
  "replacements": [
    { "old": "原文", "new": "新文" },

    // 左对齐并留出边框间距（间隙以"字宽"为单位 → pt = 比例 × 字号）
    { "old": "原文", "new": "新文",
      "align": "left", "left_border_x": 158.88, "left_gap": 3.333 },

    // 只改指定的一处（多份相同文本时用）
    { "old": "原文", "new": "新文",
      "scope": "single", "page": 0, "bbox": [100.0, 200.0, 160.0, 210.0] },

    // 覆盖全局字体/字号/描边/颜色（font_face = .ttc 里的第几个字面）
    { "old": "原文", "new": "新文", "font_size": 12, "bold_stroke": 0.04,
      "font": "C:\\Windows\\Fonts\\msyhbd.ttc", "font_face": 0, "color": [255, 0, 0] }
  ]
}
```

字段全部向后兼容：缺省字段沿用全局默认。

| 字段 | 说明 |
|---|---|
| `scope` | `all`（默认）替换全部相同文本；`single` 仅替换 `page`+`bbox` 指定的一处；`range` 替换 `page`+`bbox` 框到的**一行内**多个片段（界面框选用，合并成一条，缺 `bbox` 会被忽略） |
| `align` | 缺省按原基点重绘；`left` 从 `left_border_x + left_gap` 起左对齐 |
| `bbox` | PDF 坐标 `[x0,y0,x1,y1]`，`scope=single`/`range` 时用于精确定位 |
| `color` | `[r,g,b]`（0-255）或 `"#rrggbb"`；不写则**沿用原文颜色** |
| `font` / `font_face` | 字体文件路径与字面号（`.ttc` 可指定第几个字面，从 0 开始） |
| `bold_stroke` | 伪加粗描边宽度，默认 `0`（不描边）。原文是"用描边假装粗体"时才需要 |





---

## 打包exe

推荐在项目内的**虚拟环境**里打包，避免影响全局 Python（venv 放项目根的 `.venv`，已被 .gitignore 忽略）：

```powershell
# 1) 建虚拟环境并安装依赖（只需一次）
#    请用 64 位 Python 建 venv（py -0p 可列出本机所有版本）
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

# 2) 打包（默认 onedir，启动快）
powershell -ExecutionPolicy Bypass -File build_exe.ps1
# 想要单文件版：加 -Onefile   想看报错：加 -Console
```

> 分发给别人时请用 **64 位** Python 建 venv（32 位包有 4 GB 内存上限）。
> 自查：`.\.venv\Scripts\python.exe -c "import struct;print(struct.calcsize('P')*8)"` 应输出 `64`。

`build_exe.ps1` 会自动优先使用 `.venv`（没有则退回全局 python）。
打包的**中间产物与输出都写到 `worktemp\pyinstaller\`**（已忽略）：

- 默认 `--onedir` → `worktemp\pyinstaller\dist\PDFTextEditor\PDFTextEditor.exe`
- 加 `-Onefile` → `worktemp\pyinstaller\dist\PDFTextEditor.exe`

字体缓存位置：程序目录里有 `portable.flag`（便携版）时写在**程序目录的 `data\`**，
否则写在 `%LOCALAPPDATA%\PDFTextEditor`。自己下载的字体（系统里没有的那些）放在
同目录的 `fonts\` 里即可被自动识别，不需要装进 Windows 字体目录。

### 便携版

```powershell
powershell -ExecutionPolicy Bypass -File build_portable.ps1
```

一条命令完成：读 `VERSION` → onedir 构建 → 复制到 `worktemp\portable\PDFTextEditor\` →
放入 `portable.flag` 和 `packaging\README-Portable.txt` → 压成
`worktemp\portable\PDFTextEditor-Portable-<版本>.zip`，并打印大小与 SHA256。




