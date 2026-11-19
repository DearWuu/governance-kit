# 视觉门控（参考实现）

`visual_gate.py` 是确定性的三态视觉门控：解两张 PNG、逐像素比对，只输出证据，不替人做审美判断。

## 用法

```bash
python3 visual_gate.py --baseline baseline.png --actual actual.png --json
# {"status":"PASS","diff_ratio":0.0,"width":1440,"height":900,"reason":""}

python3 visual_gate.py --baseline baseline.png --actual actual.png --threshold 0.002
# visual_gate: PASS diff_ratio=0.000000 1440x900 threshold=0.002
```

退出码：`PASS=0`、`FAIL=1`、`UNABLE=2`。只支持 8-bit RGB/RGBA、非隔行 PNG；其余格式、尺寸不一致、解码失败一律 `UNABLE`。

## 基线纪律

- **基线只在 CI 单一环境生成**：固定 OS、字体、字体渲染、DPR、视口尺寸、浏览器版本与渲染参数；本机/其他容器生成的图只能作为诊断参考，不得直接入库当基线。
- **权限分离**：agent 只负责产出截图与比对证据；基线图的更新必须由人 review 后提交。agent 不得改基线、不得调 `--threshold`、不得放宽 `--pixel-tolerance` 来让门控变绿，也不得把 `UNABLE` 解释为通过。
- 基线变更与业务改动分开提交，提交信息写明「基线更新 + 原因 + 生成环境指纹」。

## 三态口径

| 状态 | 含义 | 计入通过率 |
| --- | --- | --- |
| PASS | 差异像素占比 ≤ 阈值 | 是（分子/分母） |
| FAIL | 差异像素占比 > 阈值 | 是（仅分母） |
| UNABLE | 工具不可用、格式不支持、尺寸不符、文件损坏 | **否**，单独统计 |

`UNABLE` 不是「差不多等于通过」。持续出现 `UNABLE` 应视为门控失效并修环境，而不是累积成绿灯。

## Electron 截图

用 CDP 的 `Page.captureScreenshot`（`webContents.debugger` 或 Playwright/Puppeteer 的 CDP session）驱动真实渲染进程；不要用已废弃/实验性的 `_electron` 截图路径，也不要对窗口句柄做 OS 级截屏（受合成器、缩放、遮挡影响，不可复现）。

```js
const dbg = win.webContents.debugger;
dbg.attach("1.3");
const { data } = await dbg.sendCommand("Page.captureScreenshot", {
  format: "png",
  captureBeyondViewport: false,
  fromSurface: true,
});
require("fs").writeFileSync("actual.png", Buffer.from(data, "base64"));
```

截图前固定：窗口尺寸、devicePixelRatio、缩放级别、动画（`prefers-reduced-motion` 或禁用 transition）、光标与滚动位置。动效/视频/时间戳等不确定区域应遮罩后再截。

## 自检

```bash
python3 visual_gate.py --selftest
```

在临时目录生成极小 PNG，覆盖：相同图 PASS、改一像素 FAIL、放宽阈值 PASS、尺寸不一致 UNABLE、损坏文件 UNABLE、16-bit UNABLE、RGB 解析、过滤类型 0-4 解码一致性。退出码 0 表示自检全过。

## 边界

本实现拒绝「部分透明像素的 RGB 不同但视觉相同」这类对抗样本的额外容忍：只按通道差与像素占比判定。阈值是团队口径，改动等同门控放宽，需单独评审并记录在案。