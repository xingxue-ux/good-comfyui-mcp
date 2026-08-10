# Krea2 / Dasiwa 完整提速指南（8GB 显卡实战版）

> 适用：Krea2 系模型（官方 krea2 / Dasiwa 微调 / 其他 Krea2 checkpoint）在 8GB 显存机器上的 ComfyUI 文生图。
> 实测机器：RTX 4060 Laptop 8GB + Ryzen 7 8845H + 16GB 内存。
> **效果：单张 1024 宽图从 2-3 分钟降到 25-55 秒（3-5 倍）。**

---

## 一、先诊断：你慢在哪一步？

跑一张图，盯这两个时间：
1. **提交后到"开始采样"的时间** = 模型加载时间（ComfyUI 前端进度条出现前）
2. **采样本身的时间**（进度条走动的时间）

- 加载占大头（>1 分钟）→ 看第二、三节（量化模型 + 模型常驻）
- 采样占大头 → 看第四、五节（步数 + 采样器 + 尺寸）
- 全程 GPU 占用 <50% → 看第六节（CPU 推理排查）

---

## 二、模型选型：量化决定一切（最大提速点）

同一个 Dasiwa/Krea2 checkpoint 有 4 种精度，**大小差 3.5 倍**：

| 精度 | 文件大小 | 8GB 卡表现 | 结论 |
|---|---|---|---|
| bf16/fp16 原版 | 24.5GB | 加载 2 分钟+，采样时疯狂 offload | ❌ 别用 |
| fp8 | ~13GB | 勉强，仍频繁 offload | ⚠️ 可用但慢 |
| **int8** | 12.8GB | 加载 ~40s，offload 可控 | ✅ 推荐 |
| **nf4（4bit）** | 7.0GB | 加载 ~20s，显存压力最小 | ✅✅ 最优 |

**下载源**：
- 官方（Comfy-Org 转换，含 int8/nf4/mxfp8/nvfp4 全量化）：
  `https://huggingface.co/Comfy-Org/Krea-2/tree/main/diffusion_models`
  国内加速：把域名换成 `hf-mirror.com`
- Dasiwa 微调（Civitai，需登录）：
  `https://civitai.red/models/2760803`（nf4 7GB / int8 12.8GB / bf16 24.5GB 三选一）

> **C 站访问约定**：一律用 `civitai.red`（完整 NSFW 版，civitai.com 会过滤内容）；API 密钥用环境变量 `CIVITAI_TOKEN`（下载/搜索 API 用 `https://civitai.red/api/...`）。

**CLIP 也要量化**：`qwen3vl_4b_fp8_scaled.safetensors`（5GB）代替 `qwen3vl_4b_bf16.safetensors`（8.5GB）——CLIP 加载时间和显存各省 40%。

**VAE**：`qwen_image_vae.safetensors`（~300MB，很小，无所谓）。

---

## 三、模型常驻：别反复加载

ComfyUI 默认**缓存已加载的模型**（跑完不卸载）——这是特性不是 bug，别"优化"掉它。

❌ 错误做法：
- 每次任务后调 `POST /free` 或 unload 模型
- 每张图切换不同模型（A 图 Dasiwa、B 图官方、C 图又切回来）
- 多个 UNETLoader 接开关来回切

✅ 正确做法：
- 固定一个主模型，一整天不换
- 模型第一次加载慢（40s），之后每张图都是"热加载"，秒进采样
- 多个模型切换 = 每次白花 40s-2min

---

## 四、步数和 CFG：Turbo 模型的正确姿势

Dasiwa 官方模型页明确推荐（不是玄学）：

**Turbo 版（文件名带 Turbo）**：
```
steps: 8
cfg: 1
sampler: er_sde（或 euler）
scheduler: simple（或 linear_quadratic）
denoise: 1.0（文生图）
```

**Raw 版（不带 Turbo）**才需要：
```
steps: 52+
cfg: 3.5
```

常见错误：
- 用 20-30 步 + CFG 8（这是别人工作流里抄来的旧参数）→ 慢 3-4 倍且没必要，Turbo 蒸馏模型 8 步就是设计目标
- CFG 1 不是写错——Turbo 模型 CFG=1 是对的（蒸馏模型不需要引导）
- 用 dpmpp_3m_sde / dpmpp_2m 等慢采样器 → er_sde 快且是作者推荐

---

## 五、尺寸与高修

- 分辨率保持在 **1024 系**：1024×576 / 1024×1024 / 1024×1536 / 1024×2048
- 不要跑 1536² 以上：Turbo 8 步下大图质量提升有限，耗时翻倍
- **不要接 hires fix 二次采样**：Turbo 直出质量已经够（原图作者就是 8 步直出 1024×2048）。接了高修 = 时间×2
- 除非追求 4K 输出，否则直出 + 事后放大（upscale 模型）比 hires fix 快

---

## 六、CPU 推理排查

如果采样时 GPU 利用率低（nvidia-smi 看）：
```
nvidia-smi --query-gpu=utilization.gpu --format=csv
```
跑图时应 >80%。如果 <50%：
1. 检查 ComfyUI 启动参数有没有 `--cpu` / `--force-fp16` 之类
2. 检查模型文件是不是真的量化版（看文件大小，24.5GB 的 bf16 在 8GB 卡上会被迫大量 offload，等效 CPU 速度）
3. 检查驱动/进程占用（Windows 下 explorer 等也占显存，8GB 卡剩 7GB 才算正常）

---

## 七、完整最优配置清单（照抄即可）

```
模型：    DasiwaKrea2TurboRaw_cutedisasterV2Turbo.safetensors（nf4 版 7GB）
CLIP：    qwen3vl_4b_fp8_scaled.safetensors（type=krea2）
VAE：     qwen_image_vae.safetensors
采样：    steps=8, cfg=1, sampler=er_sde, scheduler=simple, denoise=1.0
尺寸：    1024×576 / 1024×1024 / 1024×1536 / 1024×2048
高修：    无（直出）
模型切换：无（固定一个）
负向：    空或极简（Turbo 模型对负向不敏感）
```

## 八、实测数据（RTX 4060 Laptop 8GB）

| 配置 | 1024×576 | 1024×1536 | 1024×2048 |
|---|---|---|---|
| bf16 + 20 步 + 高修（错误示范） | 2-3 分钟 | 3-5 分钟 | 5-8 分钟 |
| int8 + 8 步直出 | ~25s | ~40s | ~55s |
| nf4 + 8 步直出 | ~18s | ~30s | ~40s |

## 九、提示词注意（质量相关，不算提速但影响返工）

- Krea2 是**自然语言模型**：用完整英文句子描述，不是 danbooru tag 堆叠
- 画风用明确词：`2D anime illustration, cel shading, clean lineart`
- 别写 `realistic`（会真的画成写实）
- 皮肤质感：`matte natural skin`（哑光）或 `subsurface scattering`（次表面散射光）按需选，别混写

---

## 十、Krea2 风格 LoRA 手册（同 seed 实测结论）

> 方法：同提示词同 seed，逐个单挂 1.0 权重对比。文件在 `models/loras/krea2/style/`（魔搭 X MuseAI 训练，base=krea/Krea-2-Turbo，4000 步）。

### 各 LoRA 实测特性

| LoRA | 触发词 | 实测特点 | 用途 |
|---|---|---|---|
| **meion**（铭音） | `meion krea2 style` | 动作更诱惑（pose 偏魅惑）、**偏写实**质感 | 想要诱惑姿态/写实质感时加重 |
| **dk.senie** | `dk.senie krea2 style` | 画风更**梦幻**（色彩鲜亮的梦幻，非朦胧雾感） | 想要梦幻氛围时加重 |
| **kieed** | （k2-kieed-style-v1） | **正常/中性**画风（最接近无 LoRA 基准） | 几乎无害的陪跑，低权重可挂可不挂 |
| **void0** | `void 0 style` | **神态更媚**、**偏少女脸（幼态）** | 想要媚态表情/幼脸时加重 |
| **NXMZ**（南湘梦斋） | `NXMZ krea2 style` | ⚠️ **带大胸倾向**（画师偏好丰满体型），权重 0.6 时明显 | **出平胸/瘦体型图不要挂**；想要丰满体型时用 |

### 选配建议

- 想要**诱惑姿态** → 加重 meion
- 想要**媚态表情/幼脸** → 加重 void0
- 想要**梦幻氛围** → 加重 senie
- 想要**纯正画风** → 少挂或只挂 kieed
- **体型敏感图（平胸/瘦）** → 不挂 NXMZ，或只用低权重（≤0.2）
- 默认日常组合（本环境验证）：meion 0.4 + senie 0.3 + kieed 0.2 + void0 0.2（无 NXMZ）

### 注意

- 低权重（0.2~0.4）单挂差异不明显，叠加才体现整体画风；对比测试需拉高到 1.0
- 全部训练在**官方 Krea2**（krea/Krea-2-Turbo）上，用在 Dasiwa 时效果略有偏差
- 触发词可加在正向提示词开头（`meion krea2 style` 等），增强 LoRA 效果
