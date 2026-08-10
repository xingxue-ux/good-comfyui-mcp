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

## 快速开始（给 Agent 的提示词）

在你的 Agent（Claude / Cursor / pi 等）的会话首条消息里粘贴：

```text
请先阅读项目 README：https://github.com/xingxue-ux/good-comfyui-mcp
按其「安装（初始化引导）」章节主动协助我完成安装与初始化，
```

## 安装（初始化引导）

### 1. 依赖

```bash
pip install -r requirements.txt        # mcp + httpx
```

### 2. ComfyUI + 模型

需要本地 ComfyUI（默认 127.0.0.1:8188），并准备：
- **Anima 管线**：`pipeline.json`（本包自带示例）引用的模型：
  - UNET：`anima-base-v1.0.safetensors`（放 `models/diffusion_models/`）
  - CLIP：`qwen_3_06b_base.safetensors`（`models/text_encoders/`，type=stable_diffusion）
  - VAE：`qwen_image_vae.safetensors`（`models/vae/`）
  - 放大：`RealESRGAN_x2plus.pth`（`models/upscale_models/`，可选）
- **Krea2 管线**（可选）：Dasiwa 等 Krea2 checkpoint + `qwen3vl_4b_*` CLIP，
  详见 `KREA2_TUNING.md`
- **ComfyUI 自定义节点**（`pipeline.json` 依赖，缺了 generate 会失败）：
  - [rgthree-comfy](https://github.com/rgthree/rgthree-comfy)（Lora Loader (LoraManager)、Image Comparer）
  - [ComfyUI-Impact-Pack](https://github.com/ltdrdata/ComfyUI-Impact-Pack)（FaceDetailer、SAMLoader、UltralyticsDetectorProvider，含对应 SAM 模型与 bbox/segm detector）

  安装：ComfyUI 菜单 → Custom Nodes → Install via Git URL，或把仓库 clone 到
  `ComfyUI/custom_nodes/` 后重启。

### 3. Ollama 识图模型

```bash
ollama pull qwen3-vl:8b    # 主识图模型（准确，NSFW 会拒答）
ollama pull llava:7b       # 无审查 fallback（NSFW 图识图）
```

### 4. camofox-browser（Danbooru 角色查询，必需）

`lookup_character_tags` / `lookup_character_appearance` 通过 camofox-browser 的
反检测浏览器访问 Danbooru（复刻前确认角色 tag 是标准流程）：

```bash
npm install -g camofox-browser   # 或按项目 README 安装
camofox-browser                   # 启动服务（默认 127.0.0.1:9377）
```

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

| 类型 | 依赖 | 用途 | 必需? |
|---|---|---|---|
| Python | mcp, httpx, numpy, pillow（requirements.txt） | MCP 框架/网络/图像处理 | ✅ |
| 服务 | ComfyUI（127.0.0.1:8188） | 生成引擎 | ✅ |
| 模型 | anima-base-v1.0 + qwen_3_06b_base + qwen_image_vae（pipeline.json 引用） | Anima 管线 | ✅ |
| 节点 | rgthree-comfy、ComfyUI-Impact-Pack | pipeline 自定义节点 | ✅ |
| 服务 | Ollama + qwen3-vl:8b + llava:7b | 识图 | ✅（识图功能） |
| 服务 | camofox-browser（127.0.0.1:9377） | Danbooru 角色查询 | ✅ |
| 配置 | CIVITAI_TOKEN / CIVITAI_SEARCH_KEY | LoRA 下载/搜索 | 可选 |
| 服务 | python -m http.server 8899 -d compare | 对比页展示 | 可选 |


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
