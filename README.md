# good-comfyui-mcp MCP Server

用本地 ComfyUI 生成 AI 图像的 MCP 服务器，附带"参考图 → 复刻"工具链：
角色 tag 查询（Danbooru）、本地视觉识图（Ollama）、小番茄混淆解混淆、
Civitai LoRA 精确检索/下载/验证。

## 功能

| 工具 | 说明 |
|---|---|
| `lookup_character_tags` | Danbooru 角色规范 tag 查询（camofox 浏览器，30 天缓存） |
| `lookup_character_appearance` | 角色外貌特征统计（solo 图 tag 频率） |
| `generate` | Anima / Krea2 双引擎文生图，可覆盖 steps/cfg/sampler，带 `reference_image` 自动生成对比页 |
| `extract_image_info` | PNG/JPEG 元数据解析（ComfyUI prompt/workflow、WebUI parameters） |
| `describe_image` | 本地识图（Ollama qwen3-vl:8b GPU；NSFW 自动 fallback llava:7b 无审查模型） |
| `search_lora` | Civitai LoRA 精确版穷举搜索（网页搜索端点 models_v9，比 API 搜索全） |
| `download_lora` | Civitai 下载 + safetensors 头验证 |
| `lookup_lora_hash` | 本地文件 SHA256 → C 站 by-hash 反查精确来源 |
| `deconfuse_image` | 小番茄混淆（Gilbert 曲线）解混淆 |
| `list_cached_characters` | 已缓存角色列表 |

## 快速开始（给 Agent 的提示词）

在你的 Agent（Claude / Cursor / pi 等）的会话首条消息里粘贴：

```text
请先阅读项目 README：https://github.com/xingxue-ux/good-comfyui-mcp
按其「安装（初始化引导）」章节主动协助我完成安装与初始化，并介绍此 MCP 的功能。
```

## 更新

- **git clone 安装**：进入包目录执行 `git pull`（升级后重启 MCP 客户端生效）
- **ZIP 安装**：GitHub 页面 Code → Download ZIP，解压覆盖原目录
  （覆盖前备份你自己改过的 `pipeline.json` 和 `compare/` 目录）

本包**未发布到 PyPI**，不支持 `pip install good-comfyui-mcp`；
安装方式只有 git clone 或 ZIP 下载，然后在 MCP 客户端里注册
`python <包目录>/good_comfyui_mcp.py` 即可（pyproject.toml 仅供本地
`pip install .` 元数据使用，无需执行）。

注意事项：
- 升级的是**代码**，模型文件（anima-base-v1.0 等）无需重下；仅当更新说明提到
  模型/自定义节点变更时才需要补装
- 若你改过 `pipeline.json`，git pull 可能冲突——先备份再更新
- 升级后重启 MCP 客户端使新代码生效，然后调 `server_info` 确认环境正常

## 复刻经验（实战教训）

- **提示词一字不改，负面也一样**：同 seed 同正面、负面不同 → 画面差异可达 MAE 8+；
  复刻时负面提示词必须与元数据完全一致
- **PNG 被删也能找回 seed**：ComfyUI `/history/{prompt_id}` 保留每次运行的完整
  prompt（含 seed），输出文件删除不影响追溯
- **`training_` 前缀 = Civitai 私有训练**：`training_<id>-<时间戳>` 命名的 LoRA
  是训练任务默认命名，未发布则公开库搜不到，属正常
- **LoRA 文件名可能同名不同内容**：C 站"精确版"覆盖旧文件后，旧图复刻效果会变
  （by-hash 反查可确认文件版本）
- **动画师 LoRA 触发词变体**：画师简称可能对应 trainedWords 变体
  （如 kan2 → @kan2arin → Kanzarin），搜不到全名时试触发词

## 默认 LoRA（5 件套）

`generate` 不传 `lora_text` 时默认挂载 5 件套（`DEFAULT_LORAS`）：

| LoRA | 权重 | 用途 |
|---|---|---|
| ushikani_kassen_lora-000013 | 0.3 | 画风 |
| anima-darklight-style-v1-000194 | 0.3 | 朦胧氛围 |
| anima-base-1-photo-background-v4 | 0.6 | 写实背景 |
| RealSkin SliderV2 | 0.8 | 写实皮肤 |
| surtr945_v1 | 0.8 | 画风 |

- 传 `lora_text=""` 显式空载；传自定义 `<lora:...>` 覆盖默认
- 使用默认组合前需把 5 个文件放入 `models/loras/`，下载信息见
  `examples/loras_required.json`（C 站 modelId/versionId/页面链接，已 by-hash 验证精确版）：
  `curl -L -o <文件名> "https://civitai.red/api/download/models/<versionId>?token=$CIVITAI_TOKEN"`
- 其他引擎（krea2）不受影响（用 `lora_list` 参数）

## 初始化验证样例（examples/）

内置两个可立即复刻的样例（完整元数据：提示词/负面/seed/参数/LoRA），
**初始化完成后跑一遍即可验证环境是否正确**：

```bash
python run_example.py            # 跑全部两个样例
python run_example.py repro_anima_00015   # 只跑一个
```

- `examples/repro_anima_00015.json` + `ref_repro_anima_00015.png`：
  双人沙发月光夜（seed 8682388855765119，5 件套 LoRA）
- `examples/repro_sofa_rose.json` + `ref_repro_sofa_rose.png`：
  沙发玫瑰写实（seed 2075224187，4 LoRA 链）

脚本复刻后与参考图逐像素对比 MAE：**MAE≈0 = 环境与参考一致**；
差异大（>25）说明模型/LoRA 缺失或版本不符，用 `server_info` 检查缺失项。

## 安装（初始化引导）

### 1. Python 依赖

```bash
pip install -r requirements.txt        # mcp、httpx、numpy、pillow
```

### 2. ComfyUI（⚠️ 先确认版本）

官方安装方式任选其一（详见 https://github.com/Comfy-Org/ComfyUI ）：
- **Desktop App**（官方推荐，Windows/macOS）：https://www.comfy.org/download
- **Windows Portable**（便携版）：官方发布页下载解压即用（自带 Python 3.13 + torch cu130）
- **Manual Install**：`git clone https://github.com/Comfy-Org/ComfyUI` →
  `pip install -r requirements.txt` → N 卡安装 `pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130`
- **comfy-cli**：`pip install comfy-cli && comfy install`

**版本检查（重要）**：
- 界面左下角版本号，或仓库内 `comfyui_version.py`（如 `v0.31.0`）
- 本 MCP 要求较新版本：**Anima 模型原生支持 + `--enable-manager` pip 包方式**均需
  新版（约 v0.30+，2026 年版本）；老版本（2024 年及以前）的 Manager 是
  custom_nodes 克隆方式，且可能不支持 Anima
- torch 最低 2.7；Nvidia 20 系以上建议 cu130+
- 启动：`python main.py --enable-manager`

**ComfyUI-Manager（插件管理器，新版是 pip 包不是 clone）**：
```bash
cd <ComfyUI目录>
python -m pip install -r manager_requirements.txt   # 安装 comfyui_manager 包
python main.py --enable-manager                      # 启用（界面右侧 Manager 按钮）
# 更新：python -m pip install -U comfyui_manager
```

**模型**（`pipeline.json` 引用，Anima 管线）：
- UNET：`anima-base-v1.0.safetensors`（放 `models/diffusion_models/`，C 站 civitai.red 搜索 "Anima" 下载）
- CLIP：`qwen_3_06b_base.safetensors`（`models/text_encoders/`，type=stable_diffusion）
- VAE：`qwen_image_vae.safetensors`（`models/vae/`）
- 放大：`RealESRGAN_x2plus.pth`（`models/upscale_models/`，可选）
- 默认 5 件套 LoRA（见上表，放 `models/loras/`）
- **自定义节点**：Anima 默认管线只用 ComfyUI 内置节点，**无需任何自定义节点**；
  LoRA 由 MCP 动态注入为标准 LoraLoader 链
- **Krea2 管线**（可选）：Dasiwa 等 Krea2 checkpoint + `qwen3vl_4b_*` CLIP，
  详见 `KREA2_TUNING.md`

### 3. Ollama 识图模型（按需，可后装）

官方安装：https://ollama.com/download（Windows 安装器；Linux `curl -fsSL https://ollama.com/install.sh | sh`）
**不需要初始化时装**——首次调用 `describe_image` 识图时再装即可：
```bash
ollama pull qwen3-vl:8b    # 主识图模型（准确，NSFW 会拒答）
ollama pull llava:7b       # 无审查 fallback（NSFW 图识图）
```
`server_info` 会以 `on_demand` 提示未装项；未装时 `describe_image` 会提醒安装。

### 4. camofox-browser（Danbooru 角色查询，必需）

`lookup_character_tags` / `lookup_character_appearance` 通过 camofox-browser 的
反检测浏览器访问 Danbooru（复刻前确认角色 tag 是标准流程）。
npm 官方包（本机验证可用的是 @askjo 版，需 Node >= 20）：

```bash
npm install -g @askjo/camofox-browser
camofox-browser                   # 启动服务（默认 127.0.0.1:9377）
```

注：npm 上有两个同名实现——`@askjo/camofox-browser`（askjo/jo-inc，本 MCP 验证用）
和 `camofox-browser`（redf0x1/redf0x1，功能类似的不同实现）；装哪个都行，
但请保持与 README 命令一致（本包按 @askjo 版验证）。

### 5. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI 地址 |
| `PIPELINE` | `./pipeline.json`（包内） | Anima 管线 workflow 路径 |
| `MODELS_ROOT` | `PIPELINE 同级 models/` | ComfyUI 模型根目录（含 diffusion_models/text_encoders/vae/loras 等子目录） |
| `CAMOFOX_URL` | `http://127.0.0.1:9377` | camofox-browser 地址 |
| `LORA_ROOT` | `models/loras` | LoRA 目录 |

### 5b. 可选：Civitai（civitai.red）集成

`search_lora` / `download_lora` / `lookup_lora_hash` 三个工具依赖 Civitai。
**不配置也能用其余全部功能**（生成/识图/解混淆/元数据）。

civitai.red 是 Civitai 的完整 NSFW 镜像（civitai.com 会过滤内容），账号/API 通用。

1. **注册/登录** civitai.red，获取 API token：
   登录后打开 `https://civitai.red/user/account` → API Keys → 新建 key
2. **配置 `CIVITAI_TOKEN`**（仅 `download_lora` 下载需要；搜索/反查是公开端点不需要）：

   ```bash
   export CIVITAI_TOKEN="你的API key"
   ```

3. **配置 `CIVITAI_SEARCH_KEY`**（LoRA 穷举搜索需要，网页搜索端点 `search-new.civitai.com`）：
   打开 civitai.red 任意页 → F12 → Network 面板 → 搜索框搜任意词 →
   找到 `multi-search` 请求 → 复制 `Authorization: Bearer xxx` 里的 xxx：

   ```bash
   export CIVITAI_SEARCH_KEY="xxx"
   ```

4. **验证**：

   ```bash
   export CIVITAI_TOKEN="..." CIVITAI_SEARCH_KEY="..."
   python lora_search.py --fresh "surtr945_v1.safetensors"
   # 返回 [EXACT] ... 即 SEARCH_KEY 生效（真实搜索）
   python lora_search.py --hash "models/loras/任意LoRA.safetensors"
   # 返回 [命中] modelId=... 即网络连通（无需 token）
   ```

### 5c. 对比页查看（可选但推荐）

`generate` 带 `reference_image` 时返回的 `view_url` 指向本地静态服务器
（默认 http://127.0.0.1:8899）。启动方式：

```bash
cd 本包目录 && mkdir -p compare && python -m http.server 8899 -d compare
```

不启动也能用全部功能，只是看不到对比页（view_url 无法访问）。

### 6. 自检

```bash
python -c "import good_comfyui_mcp; print('OK')"   # 模块加载
# 用 MCP 客户端调用 server_info：返回每项依赖状态 + missing[] 引导提示
#（ComfyUI/模型/自定义节点/Ollama/camofox/Civitai 配置全查）
```

会话开始建议先调一次 `server_info`——它会返回 `missing` 列表，按提示逐项补齐即可。

## 给 Agent 的初始化引导说明

用户安装本 MCP 后，Agent（模型）应**主动**辅助用户完成初始化：

1. 第一轮先调用 **`setup_guide`**（拿到 9 步初始化清单）和 **`server_info`**（拿到缺失项）
2. 按清单逐项引导：
   - 必需项（1-6、9）：缺失时提示用户执行对应命令，完成后重新调 `server_info` 验证
   - 可选项（7-8）：询问用户是否需要，不需要就跳过并说明影响
3. 直到 `server_info` 的 `missing` 只剩可选项（或用户明确不需要）→ 初始化完成，进入正常使用
4. 之后每次会话可先快速调 `server_info` 确认环境没变（如 ComfyUI/Ollama 重启后）

## 依赖清单

| 类型 | 依赖 | 安装方式（已查证） | 必需? |
|---|---|---|---|
| Python | mcp, httpx, numpy, pillow | `pip install -r requirements.txt` | ✅ |
| 服务 | ComfyUI（127.0.0.1:8188） | Desktop App / Portable / git clone + pip（见第 2 步；**需新版 v0.30+** 支持 Anima） | ✅ |
| 服务 | ComfyUI-Manager | `pip install -r manager_requirements.txt` + `--enable-manager`（新版是 pip 包） | ✅（插件管理） |
| 模型 | anima-base-v1.0 / qwen_3_06b_base / qwen_image_vae / RealESRGAN_x2plus | civitai.red 下载放对应 models 子目录 | ✅ |
| LoRA | 默认 5 件套（ushikani/darklight/photo-bg/RealSkin/surtr945） | civitai.red 下载放 models/loras/（可用本 MCP search_lora） | ✅（默认挂载） |
| 节点 | 无（pipeline 只用 ComfyUI 内置节点） | — | ✅ |
| 服务 | Ollama + qwen3-vl:8b + llava:7b | ollama.com/download + `ollama pull`（按需，首次识图时装） | 可选 |
| 服务 | camofox-browser（127.0.0.1:9377） | `npm install -g @askjo/camofox-browser`（Node>=20，另有 redf0x1 同名实现） | ✅ |
| 配置 | CIVITAI_TOKEN / CIVITAI_SEARCH_KEY | civitai.red API Keys / F12 抓 multi-search Bearer | 可选 |
| 服务 | python -m http.server 8899 -d compare | Python 自带 | 可选 |



## 启动

```bash
python good_comfyui_mcp.py
```

MCP stdio 服务器，客户端配置示例：

```json
{
  "mcpServers": {
    "good-comfyui-mcp": {
      "command": "python",
      "args": ["/path/to/good_comfyui_mcp.py"],
      "env": { "CIVITAI_TOKEN": "你的token" }
    }
  }
}
```

## 工具链用法

### 参考图 → 复刻

1. `extract_image_info` 解析元数据（有参数直接复刻）
2. 无元数据 → `describe_image` 识图（NSFW 自动走 llava:7b）
3. `lookup_character_tags` 确认角色 tag（首次必查）
4. 与用户确认提示词
5. `generate(prompt, reference_image=原图路径)` 出图（自动生成对比页）

### LoRA 精确检索

```bash
python lora_search.py "surtr945_v1.safetensors"     # 穷举搜索
python lora_search.py --fresh "xxx.safetensors"      # 跳过已知表重搜
python lora_search.py --hash "models/loras/xxx.safetensors"  # SHA256 反查
```

搜索使用 Civitai **网页搜索端点**（`search-new.civitai.com/multi-search`，
Meilisearch `models_v9` 索引）——API 搜索（`/api/v1/models?query=`）会漏掉
部分已发布模型（publishedAt 异常的），网页端点能搜到。匹配逻辑：
完整文件名（保留 `@`/`_`/`---`）→ 文件名一字不差 → trainedWords 触发词
（前缀/相等，短词防误报）→ 指定 base 优先 → 有文件优先。

### 小番茄解混淆

```bash
python xfq_tool.py 混淆图.png --mode dec --times 1
```

小番茄混淆 = Gilbert 曲线 + 黄金比例偏移的像素置换，可逆、无密钥。
注意：混淆后经过 JPEG 压缩/缩放的图可能无法还原（曲线位置失配）。

## 文件

- `good_comfyui_mcp.py` — MCP 服务器主程序
- `pipeline.json` — Anima 管线示例 workflow（默认正负提示词为通用占位）
- `lora_search.py` — Civitai LoRA 精确版搜索工具
- `xfq_tool.py` — 小番茄混淆/解混淆工具
- `lora_annotate.py` — LoRA 清单标注（扫描 + KNOWN 字典人工维护）
- `KREA2_TUNING.md` — Krea2 引擎调参笔记（量化选型/采样参数/风格 LoRA 实测）
- `LICENSE` — MIT
- `pyproject.toml` — 包元数据（`pip install .` 可安装，命令 `good-comfyui-mcp`）

## 已知限制

- 识图模型：qwen3-vl:8b 会拒 NSFW，fallback llava:7b（无审查但多角色图会幻觉，建议裁剪分角色识别）
- Civitai 搜索：模型级 publishedAt 异常的模型 API 搜索搜不到（网页端点可以），
  个别模型连网页端点也不收录（只能按 ID 直达或 by-hash 反查）
- 解混淆：仅支持小番茄（Gilbert 曲线）混淆；带密钥的像素混淆（如 PicEncrypt）
  无法在无密钥时还原
