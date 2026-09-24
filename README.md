# PDFTextEditor

在**电子文本 PDF** 上原地修改文字，并让改动在视觉上与原文件一致。
适用于 Word / Aspose.Words 等导出的可选中文本 PDF；不适用于扫描件（图片型 PDF）。

提供两种用法：

- **图形界面** `pdf_editor_gui.py`：在预览里点选要改的文字，输入替换内容，所见即所得。
- **命令行** `edit_pdf.py`：按 `config.json` 批量替换，便于自动化和复用。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 图形界面

```bash
python pdf_editor_gui.py
```

操作流程：

1. **打开 PDF**；
2. 在左侧预览中**点击要修改的文字**（自动高亮，并带入字体/字号/左边框）；
3. 填写「替换为」；按需设置**字体**、**字号**、**对齐**、**范围**；
4. **添加到清单**（可加多条）；
5. **预览对比**确认效果；
6. **另存为…** 生成新 PDF。

### 3. 命令行

```bash
copy config.example.json config.json
python edit_pdf.py --config config.json --dry-run   # 先看会匹配到哪些片段
python edit_pdf.py --config config.json             # 生成
```

---

## 配置说明（config.json）

GUI 的「导出 config / 导入 config」与 CLI 使用**同一套格式**，可以互相复用。

```jsonc
{
  "src": "原文件路径（相对 config 所在目录或绝对路径）",
  "out": "输出路径",
  "font": "C:\\Windows\\Fonts\\simsun.ttc",  // 全局默认字体文件
  "font_name": "simsun",
  "font_size": 10,          // 全局默认字号
  "bold_stroke": 0.03,      // 伪加粗描边宽度（见下文）
  "replacements": [
    { "old": "原文", "new": "新文" },

    // 左对齐并留出边框间距（间隙以"字宽"为单位 → pt = 比例 × 字号）
    { "old": "原文", "new": "新文",
      "align": "left", "left_border_x": 158.88, "left_gap": 3.333 },

    // 只改指定的一处（多份相同文本时用）
    { "old": "原文", "new": "新文",
      "scope": "single", "page": 0, "bbox": [100.0, 200.0, 160.0, 210.0] },

    // 覆盖全局字体/字号/描边
    { "old": "原文", "new": "新文", "font_size": 12, "bold_stroke": 0.04 }
  ]
}
```

字段全部向后兼容：缺省字段沿用全局默认。

| 字段 | 说明 |
|---|---|
| `scope` | `all`（默认）替换全部相同文本；`single` 仅替换 `page`+`bbox` 指定的一处 |
| `align` | 缺省按原基点重绘；`left` 从 `left_border_x + left_gap` 起左对齐 |
| `bbox` | PDF 坐标 `[x0,y0,x1,y1]`，`scope=single` 时用于精确定位 |

---

## 原理与坑（重要）

1. **逐字符定位**
   整段字符串 + 字体默认字距重排会累积误差（实测会把 117.4pt 的字串画成 119.1pt）。
   必须用 `rawdict` 取每个字符的**基点(origin)** 逐字绘制。

2. **"加粗"往往是描边，不是字体**
   很多导出器会把 `SimSun` 与 `SimSun,Bold` 内嵌成**同一份字体**（字节相同），
   真正的加粗来自内容流里的 `2 Tr`（填充+描边）+ 很小线宽。
   因此重绘时要用 `render_mode=2` + 极细 `border_width` 复刻，而不是换成黑体。
   用界面上的「自动标定加粗」实测校准最稳。

3. **删旧字不要填白块**
   `add_redact_annot(rect, fill=None)`。若填白色，会在文字四周留下一个白色小矩形，
   多数阅读器看不见，但部分阅读器会把它渲染成边框，看起来像"表格多出一个框把字盖住"。
   同时 `apply_redactions` 需带 `graphics=LINE_ART_NONE, images=IMAGE_NONE` 保住表格线。

4. **收尾**
   `doc.subset_fonts()` 做字体子集化（否则文件会从几十 KB 涨到约 10MB）；
   `doc.set_metadata(原元数据)` 保留 Producer/Creator/时间戳。

5. **`.ttc` 字体**
   PyMuPDF 对 Windows 的 `simsun.ttc` 支持不稳，程序会自动用 fontTools 取第 0 号字面
   转成 `.ttf` 缓存。缓存目录：

   - 源码运行：用户缓存目录（`%LOCALAPPDATA%\PDFTextEditor`）；
   - exe 打包运行：同样写 `%LOCALAPPDATA%\PDFTextEditor`，不依赖 exe 所在目录可写。

---

## 打包成 exe

```powershell
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

产物在 `dist\PDFTextEditor.exe`。首次运行会在 `%LOCALAPPDATA%\PDFTextEditor` 生成字体缓存。

---

## 目录结构

```
PDFTextEditor/
├── pdf_edit_core.py     核心：匹配 / 定位 / 删除 / 重绘 / 收尾
├── edit_pdf.py          命令行入口
├── pdf_editor_gui.py    图形界面入口
├── config.example.json  配置模板
├── build_exe.ps1        打包脚本
├── requirements.txt
└── README.md
```

## 常见问题

- **改完文字变细/变粗**：用「自动标定加粗」重新校准 `bold_stroke`。
- **文件体积暴涨**：确认保存走了 `finalize()`（含 `subset_fonts()`）。
- **点选不到文字**：该 PDF 可能是扫描件（无文本层），本工具不适用。
- **左下角预览与最终不一致**：左下角显示的是**原图**；点「预览对比」看改后效果。
